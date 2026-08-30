"""Cross-process bridge that applies Shadow replay to a Playwright Test context."""

import base64
import json
import re
import secrets
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import structlog

from app.sandbox import assert_write_allowed
from app.shadow.config import MissPolicy
from app.shadow.injector import MockInjector
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.schemas import CapturedRequest, NetworkSnapshot

logger = structlog.get_logger(__name__)

_PLAYWRIGHT_IMPORT = re.compile(r"(?P<quote>['\"])@playwright/test(?P=quote)")
_MAX_CONTROL_BODY_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class ShadowTestArtifacts:
    """Temporary fixture and transformed test used by one replay run."""

    fixture_path: Path
    test_path: Path

    def cleanup(self) -> None:
        """Remove only the two uniquely named files created for this run."""
        for path in (self.test_path, self.fixture_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "shadow_replay_artifact_cleanup_failed",
                    path=str(path),
                    error=str(exc),
                )


class ShadowReplayBridge:
    """Serve replay decisions to the Node fixture over an authenticated loopback API."""

    def __init__(
        self,
        injector: MockInjector,
        snapshots: list[NetworkSnapshot],
    ) -> None:
        self.injector = injector
        self.injector.matcher = SnapshotMatcher(snapshots, options=injector.match_options)
        self._lock = threading.Lock()
        self._token = secrets.token_urlsafe(32)
        handler = self._build_handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="shadow-replay-bridge",
            daemon=True,
        )

    @property
    def url(self) -> str:
        """Return the loopback URL used by the generated Node fixture."""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def token(self) -> str:
        """Return the per-run bearer token used to authenticate bridge calls."""
        return self._token

    def __enter__(self) -> "ShadowReplayBridge":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class ReplayHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if not self._is_authorized():
                    self._send_json(403, {"error": "forbidden"})
                    return
                try:
                    payload = self._read_json()
                    if self.path == "/route":
                        result = bridge._route(payload)
                    elif self.path == "/record":
                        result = bridge._record(payload)
                    else:
                        self._send_json(404, {"error": "not found"})
                        return
                except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, result)

            def log_message(self, format: str, *_args: object) -> None:
                del format
                return

            def _is_authorized(self) -> bool:
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {bridge.token}"
                return secrets.compare_digest(supplied, expected)

            def _read_json(self) -> dict[str, Any]:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > _MAX_CONTROL_BODY_BYTES:
                    raise ValueError("invalid control request size")
                value = json.loads(self.rfile.read(content_length))
                if not isinstance(value, dict):
                    raise ValueError("control request must be a JSON object")
                return value

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return ReplayHandler

    def _route(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = CapturedRequest.model_validate(payload)
        with self._lock:
            assert self.injector.matcher is not None
            try:
                response, score = self.injector.matcher.match_with_score(request)
            except NoMatchError:
                self.injector.unmatched_requests.append(request)
                if self.injector.miss_policy is MissPolicy.LENIENT:
                    return {"action": "continue"}
                if self.injector.miss_policy is MissPolicy.RECORD_AND_AUGMENT:
                    return {"action": "record"}
                return {"action": "abort"}
            self.injector.matched_requests.append((request, score))
        return {
            "action": "fulfill",
            "response": response.model_dump(mode="json"),
        }

    def _record(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = CapturedRequest.model_validate(payload["request"])
        response_payload = payload["response"]
        body_base64 = response_payload.get("body_base64")
        body_bytes = base64.b64decode(body_base64) if body_base64 is not None else None
        response = MockInjector._capture_response(
            int(response_payload["status"]),
            dict(response_payload.get("headers", {})),
            body_bytes,
        )
        with self._lock:
            self.injector._augment(request, response)
        return {"ok": True}


def create_shadow_test_artifacts(
    test_path: Path,
    storage_state: Mapping[str, Any] | None,
) -> ShadowTestArtifacts:
    """Create a temporary test copy that imports the Shadow-aware Playwright fixture."""
    unique_id = f"{uuid.uuid4().hex[:12]}"
    uses_commonjs = (
        re.search(
            r"require\s*\(\s*['\"]@playwright/test['\"]\s*\)",
            test_path.read_text(encoding="utf-8"),
        )
        is not None
    )
    fixture_suffix = ".cjs" if uses_commonjs else ".mjs"
    fixture_path = test_path.parent / f".e2e-healer-shadow-{unique_id}{fixture_suffix}"
    replay_test_path = test_path.parent / f".e2e-healer-shadow-{unique_id}-{test_path.name}"
    fixture_import = f"./{fixture_path.name}"

    source = test_path.read_text(encoding="utf-8")
    transformed, replacement_count = _PLAYWRIGHT_IMPORT.subn(
        json.dumps(fixture_import),
        source,
    )
    if replacement_count == 0:
        raise ValueError(
            "Shadow replay requires the test to import or require '@playwright/test' directly"
        )

    fixture_source = (
        _commonjs_fixture(storage_state) if uses_commonjs else _esm_fixture(storage_state)
    )
    assert_write_allowed(fixture_path, reason="shadow_replay_helper")
    assert_write_allowed(replay_test_path, reason="shadow_replay_test")
    artifacts = ShadowTestArtifacts(fixture_path=fixture_path, test_path=replay_test_path)
    try:
        with fixture_path.open("x", encoding="utf-8") as fixture_file:
            fixture_file.write(fixture_source)
        with replay_test_path.open("x", encoding="utf-8") as replay_file:
            replay_file.write(transformed)
    except BaseException:
        artifacts.cleanup()
        raise
    return artifacts


def _fixture_body(storage_state: Mapping[str, Any] | None) -> str:
    storage_override = (
        f"  storageState: {json.dumps(storage_state, separators=(',', ':'))},\n"
        if storage_state is not None
        else ""
    )
    return f"""{storage_override}  context: async ({{ context }}, use) => {{
    await context.route('**/*', async route => {{
      const request = route.request();
      const capturedRequest = {{
        method: request.method(),
        url: request.url(),
        headers: await request.allHeaders(),
        body: request.postData(),
      }};
      const decision = await shadowCall('/route', capturedRequest);
      if (decision.action === 'fulfill') {{
        const response = decision.response;
        const body = response.body == null
          ? undefined
          : response.is_base64
            ? Buffer.from(response.body, 'base64')
            : response.body;
        await route.fulfill({{
          status: response.status,
          headers: response.headers,
          body,
        }});
      }} else if (decision.action === 'continue') {{
        await route.continue();
      }} else if (decision.action === 'record') {{
        const response = await route.fetch();
        await shadowCall('/record', {{
          request: capturedRequest,
          response: {{
            status: response.status(),
            headers: await response.allHeaders(),
            body_base64: Buffer.from(await response.body()).toString('base64'),
          }},
        }});
        await route.fulfill({{ response }});
      }} else {{
        await route.abort('failed');
      }}
    }});
    await use(context);
  }},
"""


def _esm_fixture(storage_state: Mapping[str, Any] | None) -> str:
    return f"""import {{ test as base }} from '@playwright/test';
export * from '@playwright/test';

const shadowUrl = process.env.E2E_HEALER_SHADOW_CONTROL_URL;
const shadowToken = process.env.E2E_HEALER_SHADOW_CONTROL_TOKEN;

async function shadowCall(path, payload) {{
  const response = await fetch(`${{shadowUrl}}${{path}}`, {{
    method: 'POST',
    headers: {{
      'Authorization': `Bearer ${{shadowToken}}`,
      'Content-Type': 'application/json',
    }},
    body: JSON.stringify(payload),
  }});
  if (!response.ok) throw new Error(`Shadow bridge failed: ${{response.status}}`);
  return response.json();
}}

export const test = base.extend({{
{_fixture_body(storage_state)}}});
"""


def _commonjs_fixture(storage_state: Mapping[str, Any] | None) -> str:
    return f"""const playwright = require('@playwright/test');
const shadowUrl = process.env.E2E_HEALER_SHADOW_CONTROL_URL;
const shadowToken = process.env.E2E_HEALER_SHADOW_CONTROL_TOKEN;

async function shadowCall(path, payload) {{
  const response = await fetch(`${{shadowUrl}}${{path}}`, {{
    method: 'POST',
    headers: {{
      'Authorization': `Bearer ${{shadowToken}}`,
      'Content-Type': 'application/json',
    }},
    body: JSON.stringify(payload),
  }});
  if (!response.ok) throw new Error(`Shadow bridge failed: ${{response.status}}`);
  return response.json();
}}

const test = playwright.test.extend({{
{_fixture_body(storage_state)}}});
module.exports = {{ ...playwright, test }};
"""
