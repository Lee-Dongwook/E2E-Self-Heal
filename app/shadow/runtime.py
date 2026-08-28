"""Shadow Runtime entry point.

Instantiating the runtime has no side effects, and all collaborating components
are optional so a minimal runtime can be created and driven without wiring any
real components yet.
"""

import json
import os
import shlex
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import cast

import structlog
from playwright.sync_api import sync_playwright

from app.config import settings
from app.runner import _as_text, _terminate_process_tree, process_group_kwargs
from app.sandbox import assert_command_allowed, assert_read_allowed, assert_write_allowed
from app.shadow.browser_state import to_playwright_storage_state
from app.shadow.config import ShadowConfig
from app.shadow.context import ShadowContext
from app.shadow.injector import MockInjector
from app.shadow.interfaces import IMockInjector, IShadowRuntime, IShadowWorkspace, ISnapshotStore
from app.shadow.schemas import CapturedRequest, ShadowRunResult
from app.shadow.snapshot_store import SnapshotStore
from app.shadow.workspace import ShadowWorkspace

logger = structlog.get_logger(__name__)

SHADOW_PLACEHOLDER_MESSAGE = (
    "Shadow Testing runtime is under development — no shadow logic runs yet."
)


def _get_free_port() -> int:
    """Return an available TCP port by binding to port 0 and releasing it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return cast(int, s.getsockname()[1])


def _fetch_ws_endpoint(port: int, timeout: float = 10.0) -> str:
    """Fetch the Chrome DevTools websocket endpoint from a running Chromium debug port."""
    url = f"http://localhost:{port}/json/version"
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    return cast(str, data["webSocketDebuggerUrl"])


class ShadowRuntime(IShadowRuntime):
    """Minimal Shadow Runtime that manages a lifecycle and a :class:`ShadowContext`.

    Collaborators are injected optionally so the runtime can be instantiated on
    its own. :meth:`initialize` creates and activates a context; :meth:`shutdown`
    deactivates and releases it. Both methods are idempotent.
    """

    def __init__(
        self,
        workspace: IShadowWorkspace | None = None,
        snapshot_store: ISnapshotStore | None = None,
        injector: IMockInjector | None = None,
    ) -> None:
        self.workspace = workspace
        self.snapshot_store = snapshot_store
        self.injector = injector
        self._context: ShadowContext | None = None

    @property
    def context(self) -> ShadowContext | None:
        """The active :class:`ShadowContext`, or ``None`` before initialization."""
        return self._context

    @property
    def is_active(self) -> bool:
        """Whether the runtime currently holds an active context."""
        return self._context is not None and self._context.is_active

    def initialize(self) -> None:
        """Create and activate the shadow context.

        Idempotent: calling it again while already active leaves the existing
        context in place. Access the context via :attr:`context`.
        """
        if self.is_active:
            logger.info("shadow_runtime_already_initialized")
            return

        self._context = ShadowContext(
            workspace=self.workspace,
            snapshot_store=self.snapshot_store,
            injector=self.injector,
        )
        self._context.activate()
        logger.info("shadow_runtime_initialized")

    def shutdown(self) -> None:
        """Deactivate and release the shadow context.

        Idempotent: a no-op if the runtime was never initialized or is already
        shut down.
        """
        if self._context is None:
            logger.info("shadow_runtime_already_shutdown")
            return

        self._context.deactivate()
        self._context = None
        logger.info("shadow_runtime_shutdown")


def _build_run_result(is_success: bool, injector: MockInjector) -> ShadowRunResult:
    """Summarize replay metrics, averaging confidence across matched requests only."""
    matched_count = len(injector.matched_requests)
    missed_requests: list[CapturedRequest] = list(injector.unmatched_requests)
    average_score = (
        sum(score for _, score in injector.matched_requests) / matched_count
        if matched_count
        else 0.0
    )
    return ShadowRunResult(
        is_success=is_success,
        matched_count=matched_count,
        missed_count=len(missed_requests),
        missed_requests=missed_requests,
        score=average_score,
    )


def run_shadow(
    test_path: str | Path | None = None,
    snapshot_id: str | None = None,
    config: ShadowConfig | None = None,
) -> ShadowRunResult | str:
    """Dedicated entry point for ``e2e-healer --shadow``.

    When called without arguments (placeholder mode), exercises the minimal
    runtime lifecycle (initialize → shutdown) and returns a human-readable
    status message for the CLI to surface.

    When called with a *test_path* and *snapshot_id*, orchestrates a full
    Shadow Replay run:

    1. Load the saved :class:`ShadowSnapshot` from the :class:`SnapshotStore`.
    2. Launch a headless Chromium process with a remote-debugging port.
    3. Attach a Python :class:`MockInjector` that intercepts all network
       requests and fulfils them from the snapshot data.
    4. Write a temporary Playwright config that connects Node.js tests to the
       Python-controlled browser via ``connectOptions.wsEndpoint``.
    5. Run ``npx playwright test <test_path> --config <tmp_config>`` as a
       subprocess.
    6. Collect matched / missed request counts and return a :class:`ShadowRunResult`.
    """
    if test_path is None or snapshot_id is None:
        runtime = ShadowRuntime()
        runtime.initialize()
        runtime.shutdown()
        return SHADOW_PLACEHOLDER_MESSAGE

    test_path = Path(test_path)
    assert_read_allowed(test_path)

    cfg = config or ShadowConfig()
    workspace = ShadowWorkspace(cfg)
    store = SnapshotStore(workspace)
    snapshot = store.get_snapshot(snapshot_id)

    debug_port = _get_free_port()
    is_success = False
    config_path: Path | None = None

    with sync_playwright() as p:
        # Launch headless Chromium with a remote-debugging port so the
        # Node.js Playwright process can connect to it via CDP/WS.
        browser = p.chromium.launch(
            headless=True,
            args=[f"--remote-debugging-port={debug_port}"],
        )
        try:
            # Obtain the WebSocket debugger URL from the CDP /json/version endpoint.
            ws_endpoint = _fetch_ws_endpoint(debug_port)

            # Attach a MockInjector to a fresh browser context so that every
            # network request made by the test is intercepted and fulfilled from
            # the snapshot data.
            injector = MockInjector(
                miss_policy=cfg.miss_policy,
                match_options=cfg.match_options,
            )
            storage_state = to_playwright_storage_state(snapshot.state_snapshots)
            if storage_state is None:
                context = browser.new_context()
            else:
                context = browser.new_context(storage_state=storage_state)
            try:
                injector.inject_mock(context, snapshot.network_snapshots)

                # Write a temporary Playwright config that redirects Node.js to the
                # Python-controlled browser via connectOptions.
                config_file_name = "shadow_playwright.config.js"
                config_content = f"""module.exports = {{
  use: {{
    connectOptions: {{
      wsEndpoint: "{ws_endpoint}",
    }}
  }}
}};
"""

                config_path = workspace.tmp_path(config_file_name)
                assert_write_allowed(config_path, reason="shadow_playwright_config")
                config_path.write_text(config_content, encoding="utf-8")

                # Build the subprocess command.
                cmd_parts = shlex.split(settings.playwright_cmd)
                cmd = [*cmd_parts, str(test_path), "--config", str(config_path)]
                # On Windows, `npx` is a .cmd wrapper that must be invoked explicitly.
                if os.name == "nt" and cmd and cmd[0] == "npx":
                    cmd[0] = "npx.cmd"
                assert_command_allowed(cmd, reason="shadow_playwright_test")

                env = os.environ.copy()
                env["PLAYWRIGHT_WS_ENDPOINT"] = ws_endpoint

                logger.info("shadow_playwright_run_started", cmd=cmd)
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    **process_group_kwargs(),
                )
                try:
                    stdout, stderr = process.communicate(timeout=settings.test_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    _terminate_process_tree(process)
                    try:
                        drained_stdout, drained_stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        drained_stdout, drained_stderr = "", ""
                    logger.warning(
                        "shadow_playwright_timeout",
                        timeout=settings.test_timeout_seconds,
                        path=str(test_path),
                    )
                    logger.info(
                        "shadow_playwright_run_finished",
                        passed=False,
                        timed_out=True,
                    )
                    is_success = False
                    # Keep partial output available to debuggers without changing the
                    # public ShadowRunResult schema.
                    logger.debug(
                        "shadow_playwright_timeout_output",
                        output=(
                            _as_text(exc.stdout)
                            + _as_text(exc.stderr)
                            + _as_text(drained_stdout)
                            + _as_text(drained_stderr)
                        ),
                    )
                else:
                    is_success = process.returncode == 0
                    logger.info(
                        "shadow_playwright_run_finished",
                        passed=is_success,
                        returncode=process.returncode,
                    )
            finally:
                context.close()
        finally:
            browser.close()
            if config_path is not None:
                config_path.unlink(missing_ok=True)
            workspace.cleanup(is_success=is_success)
    return _build_run_result(is_success, injector)
