"""Mock injector to intercept outgoing Playwright network requests and replay matching snapshots."""

import asyncio
import base64
import inspect
from typing import Any

import structlog

from app.shadow.config import MissPolicy
from app.shadow.interfaces import IMockInjector
from app.shadow.match_options import MatchOptions
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot

logger = structlog.get_logger(__name__)


class MockInjector(IMockInjector):
    """Playwright-specific mock injector.

    Intercepts outgoing network requests and fulfills them using a matching snapshot response.

    When a request has no matching snapshot, behavior is governed by ``miss_policy``:

    - ``MissPolicy.STRICT`` (default): abort the request so the miss surfaces as a failure.
    - ``MissPolicy.LENIENT``: fall back to the live network and let the request through.
    - ``MissPolicy.RECORD_AND_AUGMENT``: fetch the live response, capture it into the
      snapshot set (``recorded_snapshots`` and the active matcher), and fulfil the request
      with the freshly recorded response.
    """

    def __init__(
        self,
        page_or_context: Any = None,
        miss_policy: MissPolicy = MissPolicy.STRICT,
        match_options: MatchOptions | None = None,
    ):
        self.page_or_context = page_or_context
        self.miss_policy = miss_policy
        self.match_options = match_options or MatchOptions()
        self.unmatched_requests: list[CapturedRequest] = []
        self.matched_requests: list[tuple[CapturedRequest, float]] = []
        self.recorded_snapshots: list[NetworkSnapshot] = []
        self.matcher: SnapshotMatcher | None = None

    def inject_mock(self, target: Any, mock_data: Any) -> Any:
        """Injects network mocks for the target pattern or page/context.

        - If target is a string (e.g. a glob or regex pattern), registers request
          interception for that pattern on the attached Playwright page/context.
        - If target is a Playwright Page/BrowserContext object, attaches it as
          the active target page/context and intercepts all requests ("**/*").

        - mock_data can be a SnapshotMatcher or a list of NetworkSnapshot objects.
        """
        # 1. Resolve mock_data into a SnapshotMatcher
        if isinstance(mock_data, SnapshotMatcher):
            self.matcher = mock_data
        elif isinstance(mock_data, list):
            # Parse list elements to NetworkSnapshot if needed, or assume correct type
            snapshots = []
            for item in mock_data:
                if isinstance(item, dict):
                    snapshots.append(NetworkSnapshot(**item))
                else:
                    snapshots.append(item)
            self.matcher = SnapshotMatcher(snapshots, options=self.match_options)
        else:
            self.matcher = SnapshotMatcher([mock_data], options=self.match_options)

        # 2. Resolve target and pattern
        pattern = "**/*"
        if isinstance(target, str):
            pattern = target
        else:
            self.page_or_context = target

        if not self.page_or_context:
            raise ValueError("No Playwright page or context attached to MockInjector")

        # 3. Define the routing handlers
        def handle_request_sync(route: Any, request: Any) -> None:
            captured_req = self._capture_request(request)
            try:
                assert self.matcher is not None
                response, score = self.matcher.match_with_score(captured_req)
                self.matched_requests.append((captured_req, score))
                route.fulfill(**self._fulfill_kwargs(response))
            except NoMatchError:
                self._handle_miss_sync(route, captured_req)

        async def handle_request_async(route: Any, request: Any) -> None:
            captured_req = self._capture_request(request)
            try:
                assert self.matcher is not None
                response, score = self.matcher.match_with_score(captured_req)
                self.matched_requests.append((captured_req, score))
                await route.fulfill(**self._fulfill_kwargs(response))
            except NoMatchError:
                await self._handle_miss_async(route, captured_req)

        # 4. Bind the route to the Playwright target (supports both sync and async APIs)
        if inspect.iscoroutinefunction(self.page_or_context.route):
            try:
                loop = asyncio.get_running_loop()
                return loop.create_task(self.page_or_context.route(pattern, handle_request_async))
            except RuntimeError:
                raise RuntimeError(
                    "Cannot register async route on Playwright page/context without a running event loop. "
                    "Ensure this is called from within an active async event loop."
                )
        else:
            self.page_or_context.route(pattern, handle_request_sync)
            return None

    @staticmethod
    def _capture_request(request: Any) -> CapturedRequest:
        """Build a :class:`CapturedRequest` from a Playwright request object."""
        return CapturedRequest(
            method=request.method,
            url=request.url,
            headers=request.headers,
            body=request.post_data,
        )

    @staticmethod
    def _fulfill_kwargs(response: CapturedResponse) -> dict[str, Any]:
        """Build ``route.fulfill`` keyword arguments from a captured response."""
        body = response.body
        if response.is_base64 and body:
            body_bytes = base64.b64decode(body)
        else:
            body_bytes = body.encode("utf-8") if body is not None else None
        return {"status": response.status, "headers": response.headers, "body": body_bytes}

    @staticmethod
    def _capture_response(
        status: int, headers: dict[str, str], body_bytes: bytes | None
    ) -> CapturedResponse:
        """Build a :class:`CapturedResponse` from a live response, base64-encoding binary bodies."""
        if body_bytes is None:
            return CapturedResponse(status=status, headers=headers, body=None, is_base64=False)
        try:
            return CapturedResponse(
                status=status, headers=headers, body=body_bytes.decode("utf-8"), is_base64=False
            )
        except UnicodeDecodeError:
            return CapturedResponse(
                status=status,
                headers=headers,
                body=base64.b64encode(body_bytes).decode("ascii"),
                is_base64=True,
            )

    def _augment(self, request: CapturedRequest, response: CapturedResponse) -> None:
        """Add a freshly recorded interaction to the snapshot set and active matcher."""
        snapshot = NetworkSnapshot(request=request, response=response)
        self.recorded_snapshots.append(snapshot)
        if self.matcher is not None:
            self.matcher.add_snapshot(snapshot)

    def _handle_miss_sync(self, route: Any, captured_req: CapturedRequest) -> None:
        """Apply the configured miss policy to an unmatched request (sync API)."""
        self.unmatched_requests.append(captured_req)

        if self.miss_policy is MissPolicy.LENIENT:
            logger.warning(
                "network_mock_miss_fallback_live",
                url=captured_req.url,
                method=captured_req.method,
                policy=self.miss_policy.value,
            )
            route.continue_()
            return

        if self.miss_policy is MissPolicy.RECORD_AND_AUGMENT:
            logger.warning(
                "network_mock_miss_recording",
                url=captured_req.url,
                method=captured_req.method,
                policy=self.miss_policy.value,
            )
            api_response = route.fetch()
            captured_response = self._capture_response(
                api_response.status, dict(api_response.headers), api_response.body()
            )
            self._augment(captured_req, captured_response)
            route.fulfill(response=api_response)
            return

        # Default: strict — fail on miss.
        logger.warning(
            "network_mock_no_match",
            url=captured_req.url,
            method=captured_req.method,
            policy=self.miss_policy.value,
        )
        route.abort("failed")

    async def _handle_miss_async(self, route: Any, captured_req: CapturedRequest) -> None:
        """Apply the configured miss policy to an unmatched request (async API)."""
        self.unmatched_requests.append(captured_req)

        if self.miss_policy is MissPolicy.LENIENT:
            logger.warning(
                "network_mock_miss_fallback_live",
                url=captured_req.url,
                method=captured_req.method,
                policy=self.miss_policy.value,
            )
            await route.continue_()
            return

        if self.miss_policy is MissPolicy.RECORD_AND_AUGMENT:
            logger.warning(
                "network_mock_miss_recording",
                url=captured_req.url,
                method=captured_req.method,
                policy=self.miss_policy.value,
            )
            api_response = await route.fetch()
            captured_response = self._capture_response(
                api_response.status, dict(api_response.headers), await api_response.body()
            )
            self._augment(captured_req, captured_response)
            await route.fulfill(response=api_response)
            return

        # Default: strict — fail on miss.
        logger.warning(
            "network_mock_no_match",
            url=captured_req.url,
            method=captured_req.method,
            policy=self.miss_policy.value,
        )
        await route.abort("failed")
