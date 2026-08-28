"""Persistent storage layer for Shadow Runtime snapshots."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

from app.shadow.interfaces import ISnapshotStore
from app.shadow.redaction import redact_snapshot
from app.shadow.schemas import ShadowSnapshot
from app.shadow.workspace import ShadowWorkspace

logger = structlog.get_logger(__name__)


class SnapshotStoreError(Exception):
    """Base exception for SnapshotStore errors."""


class SnapshotNotFoundError(SnapshotStoreError):
    """Raised when a snapshot is not found on disk."""


class SnapshotCorruptionError(SnapshotStoreError):
    """Raised when a snapshot file is corrupted or invalid."""


class SnapshotStore(ISnapshotStore):
    """Persistent storage layer for Shadow Runtime snapshots.

    Serializes and deserializes ShadowSnapshot objects to/from JSON files
    inside a workspace's snapshots directory.

    Identity rules:
        * The full ``snapshot_id`` is hashed to derive the filename, so distinct
          ids (e.g. ``"team/a"`` vs ``"a"``) can never alias the same path.
        * On save, a dict without ``snapshot_id`` adopts the save key; a supplied
          id that disagrees with the save key is rejected.
        * On read, the loaded object's ``snapshot_id`` must match the lookup key;
          otherwise the file is treated as corrupted.
    """

    def __init__(self, workspace: ShadowWorkspace) -> None:
        self.workspace = workspace
        # Ensure snapshots directory exists
        self.workspace.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def _get_snapshot_path(self, snapshot_id: str) -> Path:
        """Returns the absolute file path for a given snapshot ID.

        The filename is a sha256 of the full ``snapshot_id`` so distinct ids map
        to distinct paths (no collisions from stripping path components) and the
        hex digest keeps the path traversal-safe.
        """
        if not snapshot_id:
            raise SnapshotStoreError("snapshot_id must be a non-empty string")
        safe_id = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()
        return self.workspace.snapshots_dir / f"{safe_id}.json"

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        """Write ``text`` to ``path`` atomically via a sibling temp file.

        The temp file is fully written, flushed, and fsync'd before being
        ``os.replace``'d onto the target, so a reader never observes a partial
        file. The temp file is removed if anything fails before the replace.
        """
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def save_snapshot(self, snapshot_id: str, data: Any) -> None:
        """Persists a ShadowSnapshot object (or dict) to a JSON file on disk."""
        path = self._get_snapshot_path(snapshot_id)

        # If it's a ShadowSnapshot object, convert to dict/JSON
        if isinstance(data, ShadowSnapshot):
            if data.snapshot_id != snapshot_id:
                raise SnapshotStoreError(
                    f"Key/model id mismatch: key={snapshot_id!r}, "
                    f"model.snapshot_id={data.snapshot_id!r}"
                )
            snapshot_dict = redact_snapshot(data).model_dump()
        elif isinstance(data, dict):
            # A dict without an embedded id adopts the save key; a supplied id
            # must match the key exactly.
            payload = dict(data)
            payload.setdefault("snapshot_id", snapshot_id)
            try:
                snapshot = ShadowSnapshot(**payload)
            except Exception as e:
                raise SnapshotStoreError(f"Invalid snapshot dict structure: {e}") from e
            if snapshot.snapshot_id != snapshot_id:
                raise SnapshotStoreError(
                    f"Key/model id mismatch: key={snapshot_id!r}, "
                    f"model.snapshot_id={snapshot.snapshot_id!r}"
                )
            snapshot_dict = redact_snapshot(snapshot).model_dump()
        else:
            raise SnapshotStoreError("Unsupported data type; expected ShadowSnapshot or dict")

        try:
            # Deterministic serialization: sort keys, indent for readability
            serialized = json.dumps(snapshot_dict, sort_keys=True, indent=2)
            self._atomic_write_text(path, serialized)
            logger.info("snapshot_saved", snapshot_id=snapshot_id, path=str(path))
        except SnapshotStoreError:
            raise
        except Exception as e:
            raise SnapshotStoreError(f"Failed to write snapshot to disk: {e}") from e

    def get_snapshot(self, snapshot_id: str) -> ShadowSnapshot:
        """Loads and deserializes a ShadowSnapshot from disk."""
        path = self._get_snapshot_path(snapshot_id)
        if not path.exists():
            logger.warning("snapshot_not_found", snapshot_id=snapshot_id, path=str(path))
            raise SnapshotNotFoundError(f"Snapshot '{snapshot_id}' does not exist at {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise SnapshotStoreError(f"Failed to read snapshot file: {e}") from e

        try:
            snapshot_dict = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(
                "snapshot_file_corrupted",
                snapshot_id=snapshot_id,
                path=str(path),
                error=str(e),
            )
            raise SnapshotCorruptionError(f"Snapshot file is not valid JSON: {e}") from e

        try:
            snapshot = ShadowSnapshot(**snapshot_dict)
        except Exception as e:
            logger.error(
                "snapshot_data_invalid",
                snapshot_id=snapshot_id,
                path=str(path),
                error=str(e),
            )
            raise SnapshotCorruptionError(
                f"Snapshot data does not conform to ShadowSnapshot schema: {e}"
            ) from e

        # Reject files whose embedded id disagrees with the lookup key.
        if snapshot.snapshot_id != snapshot_id:
            logger.error(
                "snapshot_id_mismatch",
                snapshot_id=snapshot_id,
                stored_id=snapshot.snapshot_id,
                path=str(path),
            )
            raise SnapshotCorruptionError(
                f"Snapshot file contents id {snapshot.snapshot_id!r} does not match "
                f"lookup key {snapshot_id!r}; file is corrupted or misplaced."
            )

        return snapshot
