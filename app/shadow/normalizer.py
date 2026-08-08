"""Normalizer to scrub dynamic and volatile parameters from network requests."""

import json
import re
import urllib.parse
from typing import Any

from app.shadow.match_options import MatchOptions

# Regex patterns for dynamic fields
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
ISO_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
EPOCH_TIMESTAMP_RE = re.compile(r"\b\d{10,13}\b")

# Keys that are typically dynamic and should be normalized/stripped
DYNAMIC_PARAM_KEYS = {
    "timestamp",
    "time",
    "nonce",
    "sig",
    "signature",
    "token",
    "session_id",
    "_",
    "ts",
}
DYNAMIC_HEADER_KEYS = {
    "authorization",
    "cookie",
    "x-csrf-token",
    "date",
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "referer",
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "accept-language",
}


class RequestNormalizer:
    """Normalizes request parameters, paths, headers, and bodies to allow fuzzy matching."""

    def __init__(self, options: MatchOptions | None = None):
        self.options = options or MatchOptions()

    def normalize_value(self, val: str) -> str:
        """Replace UUIDs, timestamps, and nonces with placeholder tokens in strings."""
        if not isinstance(val, str):
            return str(val)
        val = UUID_RE.sub("<UUID>", val)
        val = ISO_TIMESTAMP_RE.sub("<TIMESTAMP>", val)
        val = EPOCH_TIMESTAMP_RE.sub("<TIMESTAMP>", val)
        return val

    def normalize_url(self, url: str) -> tuple[str, dict[str, list[str]]]:
        """Normalize URL path and query parameters. Returns (normalized_path, normalized_query)."""
        parsed = urllib.parse.urlparse(url)

        # Normalize path segments
        path_segments = parsed.path.split("/")
        normalized_segments = [self.normalize_value(seg) for seg in path_segments]
        normalized_path = "/".join(normalized_segments)

        # Normalize query params
        query_params = urllib.parse.parse_qs(parsed.query)
        extra_ignored = {p.lower() for p in self.options.ignored_query_params}
        normalized_query = {}
        for k, vals in query_params.items():
            k_lower = k.lower()
            if k_lower in DYNAMIC_PARAM_KEYS or k_lower in extra_ignored:
                # Key is completely dynamic (or explicitly ignored), skip it to
                # prevent mismatching on dynamic nonces.
                continue
            key = k_lower if self.options.case_insensitive_query_keys else k
            normalized_query[key] = [self.normalize_value(v) for v in vals]

        return normalized_path, normalized_query

    def normalize_headers(self, headers: list[tuple[str, str]] | dict[str, str]) -> dict[str, str]:
        """Normalize header values and filter out unstable headers."""
        extra_ignored = {h.lower() for h in self.options.ignored_headers}
        normalized = {}
        items = headers if isinstance(headers, list) else headers.items()
        for k, v in items:
            k_lower = k.lower()
            if k_lower in DYNAMIC_HEADER_KEYS or k_lower in extra_ignored:
                continue
            normalized[k_lower] = self.normalize_value(v)
        return normalized

    def normalize_body(self, body: str | None) -> Any:
        """Normalize request body (recursively if JSON, text replacement if string)."""
        if not body:
            return ""

        # Try to parse as JSON
        try:
            parsed_json = json.loads(body)
            return self._normalize_json_node(parsed_json)
        except json.JSONDecodeError:
            # Fallback to plain text regex scrubbing
            return self.normalize_value(body)

    def _normalize_json_node(self, node: Any) -> Any:
        """Recursively normalizes keys/values in a parsed JSON structure."""
        if isinstance(node, dict):
            new_dict = {}
            for k, v in node.items():
                k_lower = k.lower()
                if k_lower in DYNAMIC_PARAM_KEYS:
                    new_dict[k] = "<DYNAMIC>"
                else:
                    new_dict[k] = self._normalize_json_node(v)
            return new_dict
        elif isinstance(node, list):
            items = [self._normalize_json_node(item) for item in node]
            if self.options.order_insensitive_arrays:
                # Canonicalize element order so a reordered array still compares
                # equal. Serialize each element to a stable key for sorting.
                items = sorted(
                    items, key=lambda item: json.dumps(item, sort_keys=True, default=str)
                )
            return items
        elif isinstance(node, str):
            return self.normalize_value(node)
        else:
            return node
