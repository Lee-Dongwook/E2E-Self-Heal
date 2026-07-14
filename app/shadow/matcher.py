"""Matching engine to resolve incoming requests against captured network snapshots."""

import urllib.parse

from app.shadow.scoring import MatchScorer
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot


class NoMatchError(Exception):
    """Raised when the matcher cannot find a matching snapshot for a request."""

    def __init__(
        self, request: CapturedRequest, message: str = "No matching network snapshot found"
    ):
        self.request = request
        super().__init__(f"{message}: {request.method} {request.url}")


class SnapshotMatcher:
    """Matches outgoing intercepted requests against stored NetworkSnapshots using similarity scoring."""

    def __init__(
        self,
        snapshots: list[NetworkSnapshot],
        scorer: MatchScorer | None = None,
    ):
        self.snapshots = snapshots
        self.scorer = scorer or MatchScorer()

    def match(self, request: CapturedRequest) -> CapturedResponse:
        """Resolves the given captured request to the best-matching captured response.

        Scans all snapshots, scores them using the MatchScorer, and returns the response
        of the highest scoring candidate. Resolves ties deterministically.
        """
        candidates = []

        for idx, snapshot in enumerate(self.snapshots):
            score = self.scorer.calculate_score(request, snapshot.request)
            if score >= 0:
                candidates.append((score, idx, snapshot))

        if not candidates:
            raise NoMatchError(request)

        # Deterministic conflict resolution/tie-breaking:
        # Sort candidates by:
        # 1. Score descending (highest score first)
        # 2. Exact URL match (True comes before False)
        # 3. Exact URL path match (True comes before False)
        # 4. Original snapshot index ascending (stable, deterministic ordering)
        def sort_key(item):
            score, idx, snapshot = item
            exact_url = request.url == snapshot.request.url

            p1 = urllib.parse.urlparse(request.url).path
            p2 = urllib.parse.urlparse(snapshot.request.url).path
            exact_path = p1 == p2

            # Sort is ascending by default. To put highest scores first, we negate score.
            # To put exact matches (True) first, we negate the boolean value (-1 for True, 0 for False).
            return (-score, -int(exact_url), -int(exact_path), idx)

        candidates.sort(key=sort_key)
        best_candidate = candidates[0]

        return best_candidate[2].response

    def match_with_score(self, request: CapturedRequest) -> tuple[CapturedResponse, float]:
        """Resolves the given captured request and returns the response plus its similarity score."""
        candidates = []

        for idx, snapshot in enumerate(self.snapshots):
            score = self.scorer.calculate_score(request, snapshot.request)
            if score >= 0:
                candidates.append((score, idx, snapshot))

        if not candidates:
            raise NoMatchError(request)

        def sort_key(item):
            score, idx, snapshot = item
            exact_url = request.url == snapshot.request.url

            p1 = urllib.parse.urlparse(request.url).path
            p2 = urllib.parse.urlparse(snapshot.request.url).path
            exact_path = p1 == p2

            return (-score, -int(exact_url), -int(exact_path), idx)

        candidates.sort(key=sort_key)
        best_candidate = candidates[0]

        return best_candidate[2].response, best_candidate[0]
