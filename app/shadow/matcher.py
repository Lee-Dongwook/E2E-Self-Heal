"""Matching engine to resolve incoming requests against captured network snapshots."""

from collections import defaultdict
import json
import threading
import urllib.parse
from typing import Any

from app.shadow.match_options import MatchOptions
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
        options: MatchOptions | None = None,
    ):
        self.snapshots = snapshots
        if scorer is not None and options is not None and scorer.options != options:
            raise ValueError("scorer and matcher must use the same MatchOptions")
        self.options = options or (scorer.options if scorer is not None else MatchOptions())
        self.scorer = scorer or MatchScorer(options=self.options)
        self._lock = threading.Lock()
        self._snapshot_queues: dict[str, list[tuple[tuple[int, int], NetworkSnapshot]]] = (
            defaultdict(list)
        )
        self._queue_positions: dict[str, int] = defaultdict(int)
        for index, snapshot in enumerate(snapshots):
            self._add_snapshot(snapshot, index)

    def add_snapshot(self, snapshot: NetworkSnapshot) -> None:
        """Add a snapshot so a later request can reserve it for replay.

        This is primarily used by record-and-augment mode.  Callers must add
        snapshots through this method rather than mutating ``snapshots`` directly,
        so the per-request replay queues stay in sync.
        """
        with self._lock:
            self.snapshots.append(snapshot)
            self._add_snapshot(snapshot, len(self.snapshots) - 1)

    def _add_snapshot(self, snapshot: NetworkSnapshot, index: int) -> None:
        signature = self._request_signature(snapshot.request)
        queue = self._snapshot_queues[signature]
        queue.append((self._sequence_key(snapshot, index), snapshot))
        queue.sort(key=lambda item: item[0])
        # Reserved items are removed from the queue in ``_best``.  Keep the
        # cursor at the start so re-sorting after an augmentation cannot make
        # it point past (or at a different) pending item.
        self._queue_positions[signature] = 0

    @staticmethod
    def _sequence_key(snapshot: NetworkSnapshot, index: int) -> tuple[int, int]:
        """Order sequenced entries first, then unsequenced entries by insertion order."""
        if snapshot.sequence is not None:
            return (0, snapshot.sequence)
        return (1, index)

    def _request_signature(self, request: CapturedRequest) -> str:
        """Return a stable key for requests equivalent after normalisation.

        The matcher deliberately excludes volatile fields through its configured
        normalizer.  This lets repeated polling requests with changing tokens use
        one captured-response queue while keeping independently shaped requests
        separate.
        """
        parsed = urllib.parse.urlparse(request.url)
        path, query = self.scorer.normalizer.normalize_url(request.url)
        payload: dict[str, Any] = {
            "method": request.method.upper(),
            "origin": self.scorer._origin(request.url),
            "path": path,
            "query": {key: sorted(values) for key, values in sorted(query.items())},
            "headers": self.scorer.normalizer.normalize_headers(request.headers),
            "body": self.scorer.normalizer.normalize_body(request.body),
            # Preserve a malformed URL's raw form.  Valid URL matching uses the
            # normalized origin/path/query values above.
            "raw_url": request.url if parsed.scheme == "" or parsed.hostname is None else None,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def _best(self, request: CapturedRequest) -> tuple[NetworkSnapshot, float]:
        """Scores all snapshots and returns the winning (snapshot, score) pair.

        Deterministic conflict resolution/tie-breaking, sorting candidates by:
        1. Score descending (highest score first)
        2. Exact URL match (True comes before False)
        3. Exact URL path match (True comes before False)
        4. Original snapshot index ascending (stable, deterministic ordering)

        The winning request signature reserves one response at a time in captured
        ``sequence`` order (or source-list order when sequence is unavailable).
        """
        with self._lock:
            candidates = []

            for idx, snapshot in enumerate(self.snapshots):
                score = self.scorer.calculate_score(request, snapshot.request)
                if score >= 0 and score >= self.options.min_score:
                    candidates.append((score, idx, snapshot))

            if not candidates:
                raise NoMatchError(request)

            def sort_key(item: tuple[float, int, NetworkSnapshot]) -> tuple[float, int, int, int]:
                score, idx, snapshot = item
                exact_url = request.url == snapshot.request.url

                p1 = urllib.parse.urlparse(request.url).path
                p2 = urllib.parse.urlparse(snapshot.request.url).path
                exact_path = p1 == p2

                # Sort is ascending by default. To put highest scores first, we negate score.
                # To put exact matches (True) first, we negate the boolean value (-1 for True, 0 for False).
                return (-score, -int(exact_url), -int(exact_path), idx)

            candidates.sort(key=sort_key)
            score, _, best_snapshot = candidates[0]
            signature = self._request_signature(best_snapshot.request)
            position = self._queue_positions[signature]
            queue = self._snapshot_queues[signature]
            if position >= len(queue):
                raise NoMatchError(request, "Matching network snapshot queue exhausted")

            _, snapshot = queue.pop(position)
            self._queue_positions[signature] = 0
            return snapshot, score

    def match(self, request: CapturedRequest) -> CapturedResponse:
        """Resolves the given captured request to the best-matching captured response.

        Scans all snapshots, scores them using the MatchScorer, and reserves the next response
        from the winning request's queue. Repeated equivalent requests are returned in captured
        sequence order; once exhausted, the matcher raises ``NoMatchError`` rather than replaying
        a stale response.
        """
        snapshot, _ = self._best(request)
        return snapshot.response

    def match_with_score(self, request: CapturedRequest) -> tuple[CapturedResponse, float]:
        """Resolves the given captured request and returns the response plus its similarity score."""
        snapshot, score = self._best(request)
        return snapshot.response, score
