"""Matching engine to resolve incoming requests against captured network snapshots."""

import urllib.parse
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot


class NoMatchError(Exception):
    """Raised when the matcher cannot find a matching snapshot for a request."""

    def __init__(
        self, request: CapturedRequest, message: str = "No matching network snapshot found"
    ):
        self.request = request
        super().__init__(f"{message}: {request.method} {request.url}")


class SnapshotMatcher:
    """Matches outgoing intercepted requests against stored NetworkSnapshots."""

    def __init__(self, snapshots: list[NetworkSnapshot]):
        self.snapshots = snapshots

    def match(self, request: CapturedRequest) -> CapturedResponse:
        """Resolves the given captured request to a captured response.

        First attempts an exact match (method + URL).
        Falls back to path-only matching (method + URL path) if exact match fails.
        """
        req_method = request.method.upper()

        # 1. Try exact match (method + URL)
        for snapshot in self.snapshots:
            if (
                snapshot.request.method.upper() == req_method
                and snapshot.request.url == request.url
            ):
                return snapshot.response

        # 2. Try path fallback (method + path)
        req_path = urllib.parse.urlparse(request.url).path
        for snapshot in self.snapshots:
            if snapshot.request.method.upper() == req_method:
                snap_path = urllib.parse.urlparse(snapshot.request.url).path
                if snap_path == req_path:
                    return snapshot.response

        raise NoMatchError(request)
