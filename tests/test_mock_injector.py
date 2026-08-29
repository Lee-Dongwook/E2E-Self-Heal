import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.shadow.config import MissPolicy
from app.shadow.injector import MockInjector
from app.shadow.match_options import MatchOptions
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot


def _make_request(url: str = "https://api.example.com/miss", method: str = "GET") -> MagicMock:
    req = MagicMock()
    req.method = method
    req.url = url
    req.headers = {}
    req.post_data = None
    return req


def test_snapshot_matcher_exact_match():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data?id=1")
    res1 = CapturedResponse(status=200, headers={"Content-Type": "application/json"}, body="{}")

    snapshots = [NetworkSnapshot(request=req1, response=res1)]
    matcher = SnapshotMatcher(snapshots)

    # Perfect match
    match_req = CapturedRequest(method="GET", url="https://api.example.com/data?id=1")
    assert matcher.match(match_req) == res1


def test_snapshot_matcher_path_fallback_with_explicit_threshold():
    req1 = CapturedRequest(method="GET", url="https://api.example.com/data?id=1")
    res1 = CapturedResponse(status=200, headers={"Content-Type": "application/json"}, body="{}")

    snapshots = [NetworkSnapshot(request=req1, response=res1)]
    matcher = SnapshotMatcher(snapshots, options=MatchOptions(min_score=170.0))

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


def test_snapshot_matcher_consumes_queue_entries_before_resorting_on_augmentation():
    request = CapturedRequest(method="GET", url="https://api.example.com/events")
    snapshots = [
        NetworkSnapshot(
            request=request, response=CapturedResponse(status=200, body="first"), sequence=10
        ),
        NetworkSnapshot(
            request=request, response=CapturedResponse(status=200, body="third"), sequence=30
        ),
    ]
    matcher = SnapshotMatcher(snapshots)

    assert matcher.match(request).body == "first"

    matcher.add_snapshot(
        NetworkSnapshot(
            request=request, response=CapturedResponse(status=200, body="second"), sequence=20
        )
    )

    assert matcher.match(request).body == "second"
    assert matcher.match(request).body == "third"


def test_snapshot_matcher_replays_unsequenced_augmentations_after_captured_sequences():
    request = CapturedRequest(method="GET", url="https://api.example.com/events")
    snapshots = [
        NetworkSnapshot(
            request=request, response=CapturedResponse(status=200, body="third"), sequence=30
        ),
        NetworkSnapshot(
            request=request, response=CapturedResponse(status=200, body="first"), sequence=10
        ),
    ]
    matcher = SnapshotMatcher(snapshots)

    matcher.add_snapshot(
        NetworkSnapshot(request=request, response=CapturedResponse(status=200, body="live"))
    )

    assert [matcher.match(request).body for _ in range(3)] == ["first", "third", "live"]


def test_mock_injector_applies_match_options_to_snapshot_lists():
    snapshot = NetworkSnapshot(
        request=CapturedRequest(method="GET", url="https://captured.example/data"),
        response=CapturedResponse(status=200, body="captured"),
    )
    mock_page = MagicMock()
    injector = MockInjector(
        page_or_context=mock_page,
        match_options=MatchOptions(allow_cross_origin=True),
    )

    injector.inject_mock("**/*", [snapshot])

    assert injector.matcher is not None
    response = injector.matcher.match(
        CapturedRequest(method="GET", url="https://other.example/data")
    )
    assert response.body == "captured"


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
    task = injector.inject_mock("**/*", snapshots)
    assert task is not None
    await task

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
    task = injector.inject_mock("**/*", snapshots)
    assert task is not None
    await task

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


def test_mock_injector_async_no_loop():
    # Mock async page where route is a coroutine function
    mock_page = MagicMock()

    async def async_route(pattern, handler):
        pass

    mock_page.route = async_route

    injector = MockInjector(mock_page)
    # Since this test is sync and has no active event loop, inject_mock should raise RuntimeError
    with pytest.raises(RuntimeError, match="Cannot register async route"):
        injector.inject_mock("**/*", [])


# --- Miss policy: strict (default) ---


def test_miss_policy_defaults_to_strict():
    injector = MockInjector(MagicMock())
    assert injector.miss_policy is MissPolicy.STRICT


def test_miss_policy_strict_aborts_sync():
    mock_page = MagicMock()
    mock_route = MagicMock()

    injector = MockInjector(mock_page, miss_policy=MissPolicy.STRICT)
    injector.inject_mock("**/*", [])

    _, handler = mock_page.route.call_args[0]
    handler(mock_route, _make_request())

    mock_route.abort.assert_called_once_with("failed")
    mock_route.continue_.assert_not_called()
    mock_route.fetch.assert_not_called()
    assert len(injector.unmatched_requests) == 1
    assert injector.recorded_snapshots == []


# --- Miss policy: lenient (fall back to live network) ---


def test_miss_policy_lenient_continues_sync():
    mock_page = MagicMock()
    mock_route = MagicMock()

    injector = MockInjector(mock_page, miss_policy=MissPolicy.LENIENT)
    injector.inject_mock("**/*", [])

    _, handler = mock_page.route.call_args[0]
    handler(mock_route, _make_request())

    mock_route.continue_.assert_called_once_with()
    mock_route.abort.assert_not_called()
    mock_route.fulfill.assert_not_called()
    assert len(injector.unmatched_requests) == 1
    assert injector.recorded_snapshots == []


@pytest.mark.anyio
async def test_miss_policy_lenient_continues_async():
    mock_page = MagicMock()

    async def async_route(pattern, handler):
        mock_page.handler = handler

    mock_page.route = async_route

    injector = MockInjector(mock_page, miss_policy=MissPolicy.LENIENT)
    task = injector.inject_mock("**/*", [])
    assert task is not None
    await task

    mock_route = AsyncMock()
    await mock_page.handler(mock_route, _make_request())

    mock_route.continue_.assert_awaited_once_with()
    mock_route.abort.assert_not_called()
    assert len(injector.unmatched_requests) == 1


# --- Miss policy: record-and-augment (capture the miss) ---


def test_miss_policy_record_and_augment_sync():
    req = CapturedRequest(method="GET", url="https://api.example.com/known")
    res = CapturedResponse(status=200, body="cached")
    existing = [NetworkSnapshot(request=req, response=res)]

    mock_page = MagicMock()
    mock_route = MagicMock()
    api_response = MagicMock()
    api_response.status = 201
    api_response.headers = {"Content-Type": "application/json"}
    api_response.body.return_value = b'{"live": true}'
    mock_route.fetch.return_value = api_response

    injector = MockInjector(mock_page, miss_policy=MissPolicy.RECORD_AND_AUGMENT)
    injector.inject_mock("**/*", existing)

    _, handler = mock_page.route.call_args[0]
    handler(mock_route, _make_request(url="https://api.example.com/miss"))

    # Live network was fetched and used to fulfil the request.
    mock_route.fetch.assert_called_once_with()
    mock_route.fulfill.assert_called_once_with(response=api_response)
    mock_route.abort.assert_not_called()

    # The miss was captured into the snapshot set (recorded + active matcher).
    assert len(injector.recorded_snapshots) == 1
    recorded = injector.recorded_snapshots[0]
    assert recorded.request.url == "https://api.example.com/miss"
    assert recorded.response.status == 201
    assert recorded.response.body == '{"live": true}'
    assert recorded.response.is_base64 is False
    assert injector.matcher is not None
    assert injector.matcher.snapshots[-1] is recorded
    assert len(injector.matcher.snapshots) == 2
    assert len(injector.unmatched_requests) == 1


def test_miss_policy_record_and_augment_encodes_binary_sync():
    raw_bytes = b"\xff\xfe\x00binary"

    mock_page = MagicMock()
    mock_route = MagicMock()
    api_response = MagicMock()
    api_response.status = 200
    api_response.headers = {}
    api_response.body.return_value = raw_bytes
    mock_route.fetch.return_value = api_response

    injector = MockInjector(mock_page, miss_policy=MissPolicy.RECORD_AND_AUGMENT)
    injector.inject_mock("**/*", [])

    _, handler = mock_page.route.call_args[0]
    handler(mock_route, _make_request())

    recorded = injector.recorded_snapshots[0]
    assert recorded.response.is_base64 is True
    assert recorded.response.body is not None
    assert base64.b64decode(recorded.response.body) == raw_bytes


@pytest.mark.anyio
async def test_miss_policy_record_and_augment_async():
    mock_page = MagicMock()

    async def async_route(pattern, handler):
        mock_page.handler = handler

    mock_page.route = async_route

    injector = MockInjector(mock_page, miss_policy=MissPolicy.RECORD_AND_AUGMENT)
    task = injector.inject_mock("**/*", [])
    assert task is not None
    await task

    mock_route = AsyncMock()
    api_response = MagicMock()
    api_response.status = 200
    api_response.headers = {"X-Live": "yes"}
    api_response.body = AsyncMock(return_value=b"live-body")
    mock_route.fetch = AsyncMock(return_value=api_response)

    await mock_page.handler(mock_route, _make_request())

    mock_route.fetch.assert_awaited_once_with()
    mock_route.fulfill.assert_awaited_once_with(response=api_response)
    assert len(injector.recorded_snapshots) == 1
    assert injector.recorded_snapshots[0].response.body == "live-body"
    assert injector.matcher is not None
    assert len(injector.matcher.snapshots) == 1
