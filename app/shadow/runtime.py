"""Shadow Runtime entry point.

Instantiating the runtime has no side effects, and all collaborating components
are optional so a minimal runtime can be created and driven without wiring any
real components yet.
"""

import os
import shlex
import subprocess
from pathlib import Path

import structlog

from app.config import settings
from app.runner import _as_text, _terminate_process_tree, process_group_kwargs
from app.sandbox import assert_command_allowed, assert_read_allowed
from app.shadow.browser_state import to_playwright_storage_state
from app.shadow.config import ShadowConfig
from app.shadow.context import ShadowContext
from app.shadow.injector import MockInjector
from app.shadow.interfaces import IMockInjector, IShadowRuntime, IShadowWorkspace, ISnapshotStore
from app.shadow.replay_bridge import ShadowReplayBridge, create_shadow_test_artifacts
from app.shadow.schemas import CapturedRequest, ShadowRunResult
from app.shadow.snapshot_store import SnapshotStore
from app.shadow.workspace import ShadowWorkspace

logger = structlog.get_logger(__name__)

SHADOW_PLACEHOLDER_MESSAGE = (
    "Shadow Testing runtime is under development — no shadow logic runs yet."
)


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
    2. Create a temporary test copy that imports a Shadow-aware Playwright fixture.
    3. Apply saved storage state to the fixture-owned test context.
    4. Route that context's requests through an authenticated loopback bridge to
       the Python :class:`MockInjector`.
    5. Run ``npx playwright test <temporary_test_path>`` as a subprocess.
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

    is_success = False
    injector = MockInjector(
        miss_policy=cfg.miss_policy,
        match_options=cfg.match_options,
    )
    storage_state = to_playwright_storage_state(snapshot.state_snapshots)
    artifacts = None

    try:
        artifacts = create_shadow_test_artifacts(test_path, storage_state)
        bridge = ShadowReplayBridge(injector, snapshot.network_snapshots)
        with bridge:
            cmd_parts = shlex.split(settings.playwright_cmd)
            cmd = [*cmd_parts, str(artifacts.test_path)]
            # On Windows, `npx` is a .cmd wrapper that must be invoked explicitly.
            if os.name == "nt" and cmd and cmd[0] == "npx":
                cmd[0] = "npx.cmd"
            assert_command_allowed(cmd, reason="shadow_playwright_test")

            env = os.environ.copy()
            env["E2E_HEALER_SHADOW_CONTROL_URL"] = bridge.url
            env["E2E_HEALER_SHADOW_CONTROL_TOKEN"] = bridge.token

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
        if artifacts is not None:
            artifacts.cleanup()
        workspace.cleanup(is_success=is_success)
    return _build_run_result(is_success, injector)
