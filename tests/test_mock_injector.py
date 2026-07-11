import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.shadow.injector import MockInjector
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot


def test_snapshot_matcher_exact_match():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data?id=1")
    res1 = CapturedResponse(status=200, headers={"Content-Type": "application/json"}, body="{}")

    snapshots = [NetworkSnapshot(request=req1, response=res1)]
    matcher = SnapshotMatcher(snapshots)

    # Perfect match
    match_req = CapturedRequest(method="GET", url="https://api.example.com/data?id=1")
    assert matcher.match(match_req) == res1


def test_snapshot_matcher_path_fallback():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data?id=1")
    res1 = CapturedResponse(status=200, headers={"Content-Type": "application/json"}, body="{}")

    snapshots = [NetworkSnapshot(request=req1, response=res1)]
    matcher = SnapshotMatcher(snapshots)

    # Different query string, same path
    match_req = CapturedRequest(method="GET", url="https://api.example.com/data?id=2")
    assert matcher.match(match_req) == res1


def test_snapshot_matcher_no_match():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data")
    res1 = CapturedResponse(status=200, body="ok")

    snapshots = [NetworkSnapshot(request=req1, response=res1)]
    matcher = SnapshotMatcher(snapshots)

    # Different method
    req_post = CapturedRequest(method="POST", url="https://api.example.com/data")
    with pytest.raises(NoMatchError):
        matcher.match(req_post)

    # Different path
    req_other_path = CapturedRequest(method="GET", url="https://api.example.com/other")
    with pytest.raises(NoMatchError):
        matcher.match(req_other_path)


def test_mock_injector_sync_fulfill():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data")
    res1 = CapturedResponse(status=200, headers={"X-Test": "yes"}, body="hello")

    snapshots = [NetworkSnapshot(request=req1, response=res1)]

    # Mock sync page
    mock_page = MagicMock()
    # Define route mock
    mock_route = MagicMock()
    mock_request = MagicMock()
    mock_request.method = "GET"
    mock_request.url = "https://api.example.com/data"
    mock_request.headers = {}
    mock_request.post_data = None

    injector = MockInjector(page_or_context=mock_page)
    injector.inject_mock("**/*", snapshots)

    # Retrieve handler registered via route
    mock_page.route.assert_called_once()
    pattern, handler = mock_page.route.call_args[0]
    assert pattern == "**/*"

    # Call the sync handler
    handler(mock_route, mock_request)

    # Verify fulfillment
    mock_route.fulfill.assert_called_once_with(
        status=200,
        headers={"X-Test": "yes"},
        body=b"hello",
    )


def test_mock_injector_sync_fulfill_base64():
    raw_bytes = b"binary-data"
    b64_str = base64.b64encode(raw_bytes).decode("utf-8")
    req = CapturedRequest(method="GET", url="https://api.example.com/img")
    res = CapturedResponse(status=200, body=b64_str, is_base64=True)

    snapshots = [NetworkSnapshot(request=req, response=res)]

    mock_page = MagicMock()
    mock_route = MagicMock()
    mock_request = MagicMock()
    mock_request.method = "GET"
    mock_request.url = "https://api.example.com/img"
    mock_request.headers = {}
    mock_request.post_data = None

    injector = MockInjector(mock_page)
    injector.inject_mock("**/*", snapshots)

    pattern, handler = mock_page.route.call_args[0]
    handler(mock_route, mock_request)

    mock_route.fulfill.assert_called_once_with(
        status=200,
        headers={},
        body=raw_bytes,
    )


def test_mock_injector_sync_abort_on_no_match():
    snapshots = []
    mock_page = MagicMock()
    mock_route = MagicMock()
    mock_request = MagicMock()
    mock_request.method = "GET"
    mock_request.url = "https://api.example.com/not-found"
    mock_request.headers = {}
    mock_request.post_data = None

    injector = MockInjector(mock_page)
    injector.inject_mock("**/*", snapshots)

    pattern, handler = mock_page.route.call_args[0]
    handler(mock_route, mock_request)

    # Verify aborted
    mock_route.abort.assert_called_once_with("failed")
    # Verify unmatched request was captured
    assert len(injector.unmatched_requests) == 1
    assert injector.unmatched_requests[0].url == "https://api.example.com/not-found"


@pytest.mark.anyio
async def test_mock_injector_async_fulfill():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data")
    res1 = CapturedResponse(status=200, headers={"X-Test": "yes"}, body="hello")
    snapshots = [NetworkSnapshot(request=req1, response=res1)]

    # Mock async page where route is a coroutine function
    mock_page = MagicMock()

    async def async_route(pattern, handler):
        mock_page.handler = handler

    mock_page.route = async_route

    injector = MockInjector(mock_page)
    injector.inject_mock("**/*", snapshots)
    await asyncio.sleep(0)

    # Mock route & request for async api
    mock_route = AsyncMock()
    mock_request = MagicMock()
    mock_request.method = "GET"
    mock_request.url = "https://api.example.com/data"
    mock_request.headers = {}
    mock_request.post_data = None

    # Call the async handler
    await mock_page.handler(mock_route, mock_request)

    # Verify async fulfillment was awaited
    mock_route.fulfill.assert_called_once_with(
        status=200,
        headers={"X-Test": "yes"},
        body=b"hello",
    )


@pytest.mark.anyio
async def test_mock_injector_async_abort_on_no_match():
    snapshots = []
    mock_page = MagicMock()

    async def async_route(pattern, handler):
        mock_page.handler = handler

    mock_page.route = async_route

    injector = MockInjector(mock_page)
    injector.inject_mock("**/*", snapshots)
    await asyncio.sleep(0)

    mock_route = AsyncMock()
    mock_request = MagicMock()
    mock_request.method = "GET"
    mock_request.url = "https://api.example.com/not-found"
    mock_request.headers = {}
    mock_request.post_data = None

    await mock_page.handler(mock_route, mock_request)

    mock_route.abort.assert_called_once_with("failed")
    assert len(injector.unmatched_requests) == 1
    assert injector.unmatched_requests[0].url == "https://api.example.com/not-found"
