"""Normalizer to scrub dynamic and volatile parameters from network requests."""

import json
import re
import urllib.parse
from typing import Any

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
        normalized_query = {}
        for k, vals in query_params.items():
            k_lower = k.lower()
            if k_lower in DYNAMIC_PARAM_KEYS:
                # Key is completely dynamic, skip it to prevent mismatching on dynamic nonces
                continue
            normalized_query[k] = [self.normalize_value(v) for v in vals]

        return normalized_path, normalized_query

    def normalize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Normalize header values and filter out unstable headers."""
        normalized = {}
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower in DYNAMIC_HEADER_KEYS:
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
            return [self._normalize_json_node(item) for item in node]
        elif isinstance(node, str):
            return self.normalize_value(node)
        else:
            return node
