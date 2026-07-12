import pytest

from app.shadow.normalizer import RequestNormalizer
from app.shadow.scoring import MatchScorer, ScoringWeights
from app.shadow.matcher import SnapshotMatcher, NoMatchError
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot


def test_request_normalizer_value():
    normalizer = RequestNormalizer()
    assert normalizer.normalize_value("123e4567-e89b-12d3-a456-426614174000") == "<UUID>"
    assert normalizer.normalize_value("2026-07-12T13:00:00Z") == "<TIMESTAMP>"
    assert normalizer.normalize_value("1718291039") == "<TIMESTAMP>"
    assert normalizer.normalize_value("stable-value") == "stable-value"


def test_request_normalizer_url():
    normalizer = RequestNormalizer()

    # Dynamic path segment and query params
    url = "https://example.com/api/v1/users/123e4567-e89b-12d3-a456-426614174000/profile?timestamp=1718291039&nonce=abc&stable=yes"
    path, query = normalizer.normalize_url(url)

    assert path == "/api/v1/users/<UUID>/profile"
    assert "timestamp" not in query  # Ignored completely as it is dynamic
    assert "nonce" not in query  # Ignored completely
    assert query["stable"] == ["yes"]


def test_request_normalizer_headers():
    normalizer = RequestNormalizer()
    headers = {
        "Authorization": "Bearer token123",
        "Content-Type": "application/json",
        "X-Custom-ID": "123e4567-e89b-12d3-a456-426614174000",
    }
    normalized = normalizer.normalize_headers(headers)

    assert "authorization" not in normalized  # Ignored as dynamic
    assert normalized["content-type"] == "application/json"
    assert normalized["x-custom-id"] == "<UUID>"


def test_request_normalizer_body():
    normalizer = RequestNormalizer()

    # JSON body with dynamic key/value
    json_body = (
        '{"id": "123e4567-e89b-12d3-a456-426614174000", "token": "session123", "name": "Alice"}'
    )
    normalized = normalizer.normalize_body(json_body)

    assert normalized["id"] == "<UUID>"
    assert normalized["token"] == "<DYNAMIC>"
    assert normalized["name"] == "Alice"

    # Plain text body
    text_body = "User session expired at 1718291039."
    assert normalizer.normalize_body(text_body) == "User session expired at <TIMESTAMP>."


def test_match_scorer_compatibility():
    scorer = MatchScorer()

    # Different HTTP methods
    req1 = CapturedRequest(method="GET", url="http://test.com/users")
    req2 = CapturedRequest(method="POST", url="http://test.com/users")
    assert scorer.calculate_score(req1, req2) == -1.0

    # Different paths
    req3 = CapturedRequest(method="GET", url="http://test.com/users")
    req4 = CapturedRequest(method="GET", url="http://test.com/posts")
    assert scorer.calculate_score(req3, req4) == -1.0


def test_matching_engine_identical():
    snapshots = [
        NetworkSnapshot(
            request=CapturedRequest(method="GET", url="http://test.com/users"),
            response=CapturedResponse(status=200, body="users-list"),
        )
    ]
    matcher = SnapshotMatcher(snapshots)
    req = CapturedRequest(method="GET", url="http://test.com/users")
    resp = matcher.match(req)
    assert resp.body == "users-list"


def test_matching_engine_dynamic_fields():
    snapshots = [
        NetworkSnapshot(
            request=CapturedRequest(
                method="POST",
                url="http://test.com/api/users/11111111-2222-3333-4444-555555555555/update?ts=1000000000",
                body='{"name": "Bob", "session_id": "sess-1"}',
            ),
            response=CapturedResponse(status=200, body="bob-updated"),
        )
    ]
    matcher = SnapshotMatcher(snapshots)

    # Request with different dynamic UUID, query param, and body session_id
    incoming = CapturedRequest(
        method="POST",
        url="http://test.com/api/users/99999999-aaaa-bbbb-cccc-dddddddddddd/update?ts=2000000000",
        body='{"name": "Bob", "session_id": "sess-2"}',
    )

    resp = matcher.match(incoming)
    assert resp.body == "bob-updated"


def test_matching_engine_no_match():
    snapshots = [
        NetworkSnapshot(
            request=CapturedRequest(method="GET", url="http://test.com/users"),
            response=CapturedResponse(status=200, body="users-list"),
        )
    ]
    matcher = SnapshotMatcher(snapshots)
    req = CapturedRequest(method="GET", url="http://test.com/posts")

    with pytest.raises(NoMatchError):
        matcher.match(req)


def test_matching_engine_deterministic_conflict_resolution():
    # Multiple snapshots for the same endpoint, differing by stable body content
    snapshots = [
        NetworkSnapshot(
            request=CapturedRequest(
                method="POST",
                url="http://test.com/items",
                body='{"type": "book", "id": "1"}',
            ),
            response=CapturedResponse(status=200, body="book-item"),
        ),
        NetworkSnapshot(
            request=CapturedRequest(
                method="POST",
                url="http://test.com/items",
                body='{"type": "toy", "id": "2"}',
            ),
            response=CapturedResponse(status=200, body="toy-item"),
        ),
        NetworkSnapshot(
            request=CapturedRequest(
                method="POST",
                url="http://test.com/items",
                body='{"type": "book", "id": "3"}',  # same type, different id
            ),
            response=CapturedResponse(status=200, body="another-book-item"),
        ),
    ]

    matcher = SnapshotMatcher(snapshots)

    # Incoming query matches 'toy' more closely
    incoming = CapturedRequest(
        method="POST",
        url="http://test.com/items",
        body='{"type": "toy", "id": "99"}',
    )
    resp = matcher.match(incoming)
    assert resp.body == "toy-item"

    # Incoming query matches 'book' more closely. Should match first book snapshot (idx 0)
    # due to stable index sorting tie-breaker (same score for type=book and mismatching id).
    incoming2 = CapturedRequest(
        method="POST",
        url="http://test.com/items",
        body='{"type": "book", "id": "99"}',
    )
    resp2 = matcher.match(incoming2)
    assert resp2.body == "book-item"


def test_matching_engine_url_vs_path_tie_breaker():
    snapshots = [
        # Snapshot 0: matches normalized path only (different query param)
        NetworkSnapshot(
            request=CapturedRequest(method="GET", url="http://test.com/users?tab=active"),
            response=CapturedResponse(status=200, body="active-users"),
        ),
        # Snapshot 1: matches exact URL (including query param)
        NetworkSnapshot(
            request=CapturedRequest(method="GET", url="http://test.com/users?tab=all"),
            response=CapturedResponse(status=200, body="all-users"),
        ),
    ]

    matcher = SnapshotMatcher(snapshots)

    incoming = CapturedRequest(method="GET", url="http://test.com/users?tab=all")
    resp = matcher.match(incoming)
    assert resp.body == "all-users"


def test_custom_scoring_weights():
    # 1. Default weights score
    req1 = CapturedRequest(method="GET", url="http://test.com/users")
    req2 = CapturedRequest(method="GET", url="http://test.com/users")

    scorer_default = MatchScorer()
    default_score = scorer_default.calculate_score(req1, req2)
    # Default exact_url_bonus (150) + query_max (30) + headers_max (20) + body_max (50) = 250
    assert default_score == 250.0

    # 2. Custom weights score
    custom_weights = ScoringWeights(
        exact_url_bonus=10.0,
        base_url_match=5.0,
        query_max=1.0,
        headers_max=1.0,
        body_max=1.0,
    )
    scorer_custom = MatchScorer(weights=custom_weights)
    custom_score = scorer_custom.calculate_score(req1, req2)
    # 10 + 1 + 1 + 1 = 13
    assert custom_score == 13.0
