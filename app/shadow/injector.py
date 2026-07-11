"""Mock injector to intercept outgoing Playwright network requests and replay matching snapshots."""

import asyncio
import base64
import inspect
from typing import Any

import structlog

from app.shadow.interfaces import IMockInjector
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.schemas import CapturedRequest, NetworkSnapshot

logger = structlog.get_logger(__name__)


class MockInjector(IMockInjector):
    """Playwright-specific mock injector.

    Intercepts outgoing network requests and fulfills them using a matching snapshot response.
    """

    def __init__(self, page_or_context: Any = None):
        self.page_or_context = page_or_context
        self.unmatched_requests: list[CapturedRequest] = []
        self.matcher: SnapshotMatcher | None = None

    def inject_mock(self, target: Any, mock_data: Any) -> None:
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
            self.matcher = SnapshotMatcher(snapshots)
        else:
            self.matcher = SnapshotMatcher([mock_data])

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
            captured_req = CapturedRequest(
                method=request.method,
                url=request.url,
                headers=request.headers,
                body=request.post_data,
            )
            try:
                assert self.matcher is not None
                response = self.matcher.match(captured_req)
                body = response.body
                if response.is_base64 and body:
                    body_bytes = base64.b64decode(body)
                else:
                    body_bytes = body.encode("utf-8") if body is not None else None

                route.fulfill(
                    status=response.status,
                    headers=response.headers,
                    body=body_bytes,
                )
            except NoMatchError:
                self.unmatched_requests.append(captured_req)
                logger.warning("network_mock_no_match", url=request.url, method=request.method)
                route.abort("failed")

        async def handle_request_async(route: Any, request: Any) -> None:
            captured_req = CapturedRequest(
                method=request.method,
                url=request.url,
                headers=request.headers,
                body=request.post_data,
            )
            try:
                assert self.matcher is not None
                response = self.matcher.match(captured_req)
                body = response.body
                if response.is_base64 and body:
                    body_bytes = base64.b64decode(body)
                else:
                    body_bytes = body.encode("utf-8") if body is not None else None

                await route.fulfill(
                    status=response.status,
                    headers=response.headers,
                    body=body_bytes,
                )
            except NoMatchError:
                self.unmatched_requests.append(captured_req)
                logger.warning("network_mock_no_match", url=request.url, method=request.method)
                await route.abort("failed")

        # 4. Bind the route to the Playwright target (supports both sync and async APIs)
        if inspect.iscoroutinefunction(self.page_or_context.route):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.page_or_context.route(pattern, handle_request_async))
            except RuntimeError:
                asyncio.run(self.page_or_context.route(pattern, handle_request_async))
        else:
            self.page_or_context.route(pattern, handle_request_sync)
