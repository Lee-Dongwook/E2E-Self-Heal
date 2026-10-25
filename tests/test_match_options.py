"""Tests for the opt-in richer matching modes (MatchOptions).

Covers, per mode, that the default (no options) behavior is unchanged and that
enabling the mode changes matching as intended — including near-miss cases (a
candidate that almost matches) and reorder cases (order-insensitive matching).
"""

import pytest

from app.shadow.match_options import MatchOptions
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.normalizer import RequestNormalizer
from app.shadow.scoring import MatchScorer
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot


def _snapshot(request: CapturedRequest, body: str) -> NetworkSnapshot:
    return NetworkSnapshot(request=request, response=CapturedResponse(status=200, body=body))


def test_ignored_query_params_near_miss_now_matches_fully():
    req1 = CapturedRequest(method="GET", url="http://test.com/s?page=1&debug=xyz")
    req2 = CapturedRequest(method="GET", url="http://test.com/s?page=1&debug=abc")

    # Default: differing 'debug' drags the query score below a full match.
    default_score = MatchScorer().calculate_score(req1, req2)

    # Opt-in: 'debug' ignored, so only 'page' is scored and it matches fully.
    opts = MatchOptions(ignored_query_params=frozenset({"debug"}))
    opted_score = MatchScorer(options=opts).calculate_score(req1, req2)

    assert opted_score > default_score


def test_case_insensitive_query_keys():
    req1 = CapturedRequest(method="GET", url="http://test.com/s?Tab=all")
    req2 = CapturedRequest(method="GET", url="http://test.com/s?tab=all")

    # Default: 'Tab' and 'tab' are distinct keys -> no query overlap.
    default_score = MatchScorer().calculate_score(req1, req2)

    opts = MatchOptions(case_insensitive_query_keys=True)
    opted_score = MatchScorer(options=opts).calculate_score(req1, req2)

    assert opted_score > default_score


# ---------------------------------------------------------------------------
# Header-aware matching (hard constraint)
# ---------------------------------------------------------------------------


def test_required_header_rejects_mismatch():
    snapshots = [
        _snapshot(
            CapturedRequest(
                method="GET",
                url="http://test.com/data",
                headers={"X-Tenant": "acme"},
            ),
            body="acme-data",
        ),
        _snapshot(
            CapturedRequest(
                method="GET",
                url="http://test.com/data",
                headers={"X-Tenant": "globex"},
            ),
            body="globex-data",
        ),
    ]

    opts = MatchOptions(required_headers=frozenset({"X-Tenant"}))
    matcher = SnapshotMatcher(snapshots, options=opts)

    # Near-miss: same path/method, only the required header differentiates them.
    incoming = CapturedRequest(
        method="GET", url="http://test.com/data", headers={"X-Tenant": "globex"}
    )
    assert matcher.match(incoming).body == "globex-data"


def test_required_header_absent_raises_no_match():
    snapshots = [
        _snapshot(
            CapturedRequest(method="GET", url="http://test.com/data", headers={"X-Tenant": "acme"}),
            body="acme-data",
        )
    ]
    opts = MatchOptions(required_headers=frozenset({"X-Tenant"}))
    matcher = SnapshotMatcher(snapshots, options=opts)

    # Incoming lacks the required header entirely -> no candidate qualifies.
    incoming = CapturedRequest(method="GET", url="http://test.com/data")
    with pytest.raises(NoMatchError):
        matcher.match(incoming)


def test_required_header_ignored_by_default():
    # Without the option, the differing header is only soft-scored, so a match
    # is still found (proves the constraint is strictly opt-in).
    snapshots = [
        _snapshot(
            CapturedRequest(method="GET", url="http://test.com/data", headers={"X-Tenant": "acme"}),
            body="acme-data",
        )
    ]
    matcher = SnapshotMatcher(snapshots)
    incoming = CapturedRequest(
        method="GET", url="http://test.com/data", headers={"X-Tenant": "globex"}
    )
    assert matcher.match(incoming).body == "acme-data"


def test_ignored_headers_dropped_from_scoring():
    normalizer = RequestNormalizer(MatchOptions(ignored_headers=frozenset({"X-Request-Id"})))
    normalized = normalizer.normalize_headers(
        {"X-Request-Id": "req-123", "Content-Type": "application/json"}
    )
    assert "x-request-id" not in normalized
    assert normalized["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# Body-aware matching (hard constraint)
# ---------------------------------------------------------------------------


def test_require_body_match_rejects_near_miss():
    snapshots = [
        _snapshot(
            CapturedRequest(method="POST", url="http://test.com/x", body='{"a": 1, "b": 2}'),
            body="stored",
        )
    ]
    opts = MatchOptions(require_body_match=True)
    matcher = SnapshotMatcher(snapshots, options=opts)

    # Near-miss body: 'b' differs -> hard-rejected.
    near_miss = CapturedRequest(method="POST", url="http://test.com/x", body='{"a": 1, "b": 3}')
    with pytest.raises(NoMatchError):
        matcher.match(near_miss)

    # Exact normalized body -> matches.
    exact = CapturedRequest(method="POST", url="http://test.com/x", body='{"a": 1, "b": 2}')
    assert matcher.match(exact).body == "stored"


def test_require_body_match_default_allows_partial():
    snapshots = [
        _snapshot(
            CapturedRequest(method="POST", url="http://test.com/x", body='{"a": 1, "b": 2}'),
            body="stored",
        )
    ]
    matcher = SnapshotMatcher(snapshots)  # default: no hard body constraint
    near_miss = CapturedRequest(method="POST", url="http://test.com/x", body='{"a": 1, "b": 3}')
    assert matcher.match(near_miss).body == "stored"


# ---------------------------------------------------------------------------
# Fuzzy / order-insensitive array matching
# ---------------------------------------------------------------------------


def test_order_insensitive_arrays_reorder_matches():
    req1 = CapturedRequest(method="POST", url="http://test.com/x", body='{"tags": ["a", "b", "c"]}')
    req2 = CapturedRequest(method="POST", url="http://test.com/x", body='{"tags": ["c", "b", "a"]}')

    # Default: reordered arrays are not equal -> body scores partially.
    default_score = MatchScorer().calculate_score(req1, req2)

    # Opt-in: reordered arrays compare equal -> full body score.
    opts = MatchOptions(order_insensitive_arrays=True)
    opted_score = MatchScorer(options=opts).calculate_score(req1, req2)

    assert opted_score > default_score
    # Full match under the fuzzy option: exact_url(150)+query(30)+headers(20)+body(50).
    assert opted_score == 250.0


def test_order_insensitive_arrays_different_elements_still_differ():
    # Reorder-insensitivity must not collapse genuinely different arrays.
    req1 = CapturedRequest(method="POST", url="http://test.com/x", body='{"tags": ["a", "b", "c"]}')
    req2 = CapturedRequest(method="POST", url="http://test.com/x", body='{"tags": ["a", "b", "d"]}')
    opts = MatchOptions(order_insensitive_arrays=True)
    score = MatchScorer(options=opts).calculate_score(req1, req2)

    # Bodies differ, so it falls short of the full 250 exact-match score.
    assert score < 250.0


def test_order_insensitive_arrays_reorder_wins_tie_breaker():
    # Two snapshots for the same endpoint; incoming matches one only after reorder.
    snapshots = [
        _snapshot(
            CapturedRequest(method="POST", url="http://test.com/x", body='{"ids": [3, 2, 1]}'),
            body="reordered-set",
        ),
        _snapshot(
            CapturedRequest(method="POST", url="http://test.com/x", body='{"ids": [9, 8, 7]}'),
            body="other-set",
        ),
    ]
    opts = MatchOptions(order_insensitive_arrays=True)
    matcher = SnapshotMatcher(snapshots, options=opts)

    incoming = CapturedRequest(method="POST", url="http://test.com/x", body='{"ids": [1, 2, 3]}')
    assert matcher.match(incoming).body == "reordered-set"


# ---------------------------------------------------------------------------
# Origin and confidence constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("captured_url", "incoming_url"),
    [
        ("https://captured.example/api", "https://other.example/api"),
        ("https://example.com/api", "http://example.com/api"),
        ("https://example.com:444/api", "https://example.com/api"),
    ],
)
def test_different_origins_do_not_match_by_default(captured_url: str, incoming_url: str):
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url=captured_url), body="captured")]
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url=incoming_url))


def test_default_ports_are_equivalent_origins():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https://example.com:443/api"), body="ok")]
    )

    response = matcher.match(CapturedRequest(method="GET", url="https://example.com/api"))

    assert response.body == "ok"


def test_explicit_zero_port_is_not_treated_as_the_default_port():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https://example.com/api"), body="ok")]
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url="https://example.com:0/api"))


def test_cross_origin_matching_requires_explicit_opt_in():
    matcher = SnapshotMatcher(
        [
            _snapshot(
                CapturedRequest(method="GET", url="https://captured.example/api"),
                body="captured",
            )
        ],
        options=MatchOptions(allow_cross_origin=True),
    )

    response = matcher.match(CapturedRequest(method="GET", url="https://other.example/api"))

    assert response.body == "captured"


def test_malformed_port_is_a_safe_miss():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https://example.com/api"), body="ok")]
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url="https://example.com:bad/api"))


def test_malformed_ipv6_is_a_safe_miss():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https://[::1]/api"), body="ok")]
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url="https://[::1/api"))


def test_cross_origin_opt_in_does_not_bypass_url_validation():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https://example.com/api"), body="ok")],
        options=MatchOptions(allow_cross_origin=True),
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url="https://[::1/api"))


def test_equivalent_ipv6_spellings_share_an_origin():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https://[::1]/api"), body="ok")]
    )

    response = matcher.match(CapturedRequest(method="GET", url="https://[0:0:0:0:0:0:0:1]/api"))

    assert response.body == "ok"


def test_absolute_url_without_a_host_is_a_safe_miss():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="https:///api"), body="ok")]
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url="https:///api"))


def test_scheme_less_urls_are_safe_misses():
    matcher = SnapshotMatcher(
        [_snapshot(CapturedRequest(method="GET", url="/api?page=1"), body="ok")]
    )

    with pytest.raises(NoMatchError):
        matcher.match(CapturedRequest(method="GET", url="/api?page=1"))


@pytest.mark.parametrize("min_score", [-1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_minimum_scores_are_rejected(min_score: float):
    with pytest.raises(ValueError, match="finite, non-negative"):
        MatchOptions(min_score=min_score)


def test_explicit_scorer_supplies_matcher_options_when_options_are_omitted():
    options = MatchOptions(allow_cross_origin=True, min_score=120.0)
    matcher = SnapshotMatcher(
        [
            _snapshot(
                CapturedRequest(method="GET", url="https://captured.example/api"),
                body="captured",
            )
        ],
        scorer=MatchScorer(options=options),
    )

    response = matcher.match(CapturedRequest(method="GET", url="https://other.example/api"))

    assert response.body == "captured"


def test_conflicting_explicit_scorer_and_options_are_rejected():
    with pytest.raises(ValueError, match="same MatchOptions"):
        SnapshotMatcher(
            [],
            scorer=MatchScorer(options=MatchOptions(allow_cross_origin=False)),
            options=MatchOptions(allow_cross_origin=True),
        )


def test_default_threshold_rejects_low_confidence_query_and_body_mismatch():
    captured = CapturedRequest(
        method="POST",
        url="https://example.com/api?page=1",
        body='{"kind": "captured"}',
    )
    incoming = CapturedRequest(
        method="POST",
        url="https://example.com/api?page=2",
        body='{"kind": "incoming"}',
    )
    matcher = SnapshotMatcher([_snapshot(captured, body="captured")])

    assert MatchScorer().calculate_score(incoming, captured) == 120.0
    with pytest.raises(NoMatchError):
        matcher.match(incoming)


def test_default_threshold_rejects_query_only_mismatch():
    captured = CapturedRequest(method="GET", url="https://example.com/api?page=1")
    incoming = CapturedRequest(method="GET", url="https://example.com/api?page=2")
    matcher = SnapshotMatcher([_snapshot(captured, body="captured")])

    assert MatchScorer().calculate_score(incoming, captured) == 170.0
    with pytest.raises(NoMatchError):
        matcher.match(incoming)


def test_minimum_score_boundary_is_inclusive_and_configurable():
    captured = CapturedRequest(
        method="POST",
        url="https://example.com/api?page=1",
        body='{"kind": "captured"}',
    )
    incoming = CapturedRequest(
        method="POST",
        url="https://example.com/api?page=2",
        body='{"kind": "incoming"}',
    )
    snapshots = [_snapshot(captured, body="captured")]

    accepted = SnapshotMatcher(snapshots, options=MatchOptions(min_score=120.0))
    rejected = SnapshotMatcher(snapshots, options=MatchOptions(min_score=120.01))

    assert accepted.match(incoming).body == "captured"
    with pytest.raises(NoMatchError):
        rejected.match(incoming)
