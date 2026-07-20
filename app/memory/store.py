"""Persistent JSONL-backed store for healing-history records."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import structlog

from app.memory.schemas import HealingRecord, MemoryMatchResult

logger = structlog.get_logger(__name__)


class HealingHistoryStore(ABC):
    """Abstract interface for persisting and querying healing-history records."""

    @abstractmethod
    def save(self, record: HealingRecord) -> None:
        """Persist a record."""

    @abstractmethod
    def query(
        self,
        error_signature: str,
        broken_selector: str,
        framework: str = "",
        threshold: float | None = None,
    ) -> list[MemoryMatchResult]:
        """Return candidate matches ordered by relevance (highest first)."""


class JsonlHealingHistoryStore(HealingHistoryStore):
    """Append-only JSONL store — simple, portable, no external dependencies.

    Each line is a single JSON-encoded ``HealingRecord``. Reads are linear scans;
    acceptable for local CLI usage where total record count is in the hundreds.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        logger.info("memory_store_initialized", path=str(path))

    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()

    def save(self, record: HealingRecord) -> None:
        self._ensure_file()
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")
        logger.info("memory_record_saved", record_id=record.id, path=str(self._path))

    def _all_records(self) -> list[HealingRecord]:
        if not self._path.exists():
            return []
        records: list[HealingRecord] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(HealingRecord.model_validate_json(line))
                except Exception:
                    logger.warning("memory_record_parse_failed", line=line[:200])
        return records

    def query(
        self,
        error_signature: str,
        broken_selector: str,
        framework: str = "",
        threshold: float | None = None,
    ) -> list[MemoryMatchResult]:
        from app.config import settings
        from app.memory.similarity import find_best_matches

        if threshold is None:
            threshold = getattr(settings, "memory_similarity_threshold", 0.75)
        candidates = self._all_records()
        return find_best_matches(error_signature, broken_selector, framework, candidates, threshold=threshold)


def get_default_store(path: Optional[Path] = None) -> HealingHistoryStore:
    """Return the default store instance (JSONL at ``.healing_history.jsonl``)."""
    if path is None:
        path = Path(".healing_history.jsonl")
    return JsonlHealingHistoryStore(path)
