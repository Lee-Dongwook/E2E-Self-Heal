"""Heuristic similarity scoring for healing-history lookup.

No vector DB required — uses string-based heuristics on error signatures and
selector shapes. A future version can swap in embedding-based scoring without
changing the store interface.
"""

from __future__ import annotations

import re
from typing import Optional

from app.memory.schemas import HealingRecord, MemoryMatchResult


def _normalize_error(err: str) -> str:
    """Strip dynamic noise (line numbers, timestamps, temp paths) from error text."""
    # Remove line/column references like "(42:15)" or ":42"
    err = re.sub(r"\(\d+:\d+\)", "", err)
    err = re.sub(r":\d+(?::\d+)?", "", err)
    # Remove UUIDs, hex hashes
    err = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "", err, flags=re.I)
    err = re.sub(r"\b[0-9a-f]{32,40}\b", "", err, flags=re.I)
    # Collapse whitespace
    err = " ".join(err.split())
    return err.lower().strip()


def _selector_shape(selector: str) -> str:
    """Extract a shape fingerprint from a selector for shape-level matching.

    E.g. ``'#submit-btn'`` → ``'id'``, ``'getByRole("button")'`` → ``'getbyrole'``,
    ``'[data-testid="cta"]'`` → ``'data-testid'``.
    """
    s = selector.strip().lower()
    # Playwright getBy* helpers
    if s.startswith("page.getby") or s.startswith("getby"):
        return s.split("(")[0]
    # CSS id
    if s.startswith("#"):
        return "id"
    # CSS class
    if s.startswith("."):
        return "class"
    # Attribute selector
    m = re.search(r"\[([^=\]]+)", s)
    if m:
        return m.group(1).lower()
    # text= or role= engine strings
    if "=" in s:
        return s.split("=")[0]
    return s[:20]


def _token_overlap(a: str, b: str) -> float:
    """Jaccard-like token overlap between two normalized strings."""
    tokens_a = set(_normalize_error(a).split())
    tokens_b = set(_normalize_error(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    inter = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return inter / union if union else 0.0


def _selector_shape_match(a: str, b: str) -> float:
    """Score selector-shape similarity (0.0–1.0)."""
    shape_a = _selector_shape(a)
    shape_b = _selector_shape(b)
    if shape_a == shape_b:
        return 1.0
    # Partial: share a prefix or contain each other
    if shape_a in shape_b or shape_b in shape_a:
        return 0.7
    return 0.0


def compute_similarity(
    error_signature: str,
    broken_selector: str,
    framework: str,
    record: HealingRecord,
) -> float:
    """Return a composite similarity score (0.0–1.0) between a query and a stored record.

    Weights:
    - Error signature overlap: 40%
    - Broken selector shape match: 40%
    - Framework match bonus: 20%
    """
    err_score = _token_overlap(error_signature, record.error_signature)
    sel_score = _selector_shape_match(broken_selector, record.broken_selector)
    fw_score = 1.0 if framework and record.framework and framework == record.framework else 0.0

    composite = (err_score * 0.4) + (sel_score * 0.4) + (fw_score * 0.2)
    return round(min(1.0, max(0.0, composite)), 4)


def find_best_matches(
    error_signature: str,
    broken_selector: str,
    framework: str,
    candidates: list[HealingRecord],
    threshold: float = 0.75,
    top_k: int = 3,
) -> list[MemoryMatchResult]:
    """Rank candidates by composite similarity and return those above ``threshold``."""
    scored: list[MemoryMatchResult] = []
    for record in candidates:
        score = compute_similarity(error_signature, broken_selector, framework, record)
        if score >= threshold:
            scored.append(MemoryMatchResult(record=record, confidence=score))
    scored.sort(key=lambda m: m.confidence, reverse=True)
    return scored[:top_k]
