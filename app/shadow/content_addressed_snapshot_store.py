"""Content-addressed implementation of ISnapshotStore.

Stores each snapshot payload under a hash of its content, so identical payloads
collapse to a single object; a per-id reference maps each ``snapshot_id`` to the
hash it points at. The ``snapshot_id`` is excluded from the hashed content, so
identical captures saved under different ids share one object.

On-disk layout under the workspace ``snapshots`` directory::

    snapshots/
        objects/
            <sha256>.json     # canonical, deduplicated content blob (no snapshot_id)
        refs/
            <snapshot_id>.ref # single line: the content hash it resolves to

Integrity: a save that hits an existing object re-hashes it before a new ref
points at it, and a read re-hashes the loaded content, so altered JSON under the
old hash is rejected.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import structlog

from app.shadow.interfaces import ISnapshotStore
from app.shadow.schemas import ShadowSnapshot
from app.shadow.snapshot_store import (
    SnapshotCorruptionError,
    SnapshotNotFoundError,
    SnapshotStoreError,
)
from app.shadow.workspace import ShadowWorkspace

logger = structlog.get_logger(__name__)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class ContentAddressedSnapshotStore(ISnapshotStore):
    """Hash-keyed snapshot store that deduplicates identical payloads.

    Honors the same schema contract as
    :class:`~app.shadow.snapshot_store.SnapshotStore`.
    """

    def __init__(self, workspace: ShadowWorkspace) -> None:
        self.workspace = workspace
        self.objects_dir = workspace.snapshots_dir / "objects"
        self.refs_dir = workspace.snapshots_dir / "refs"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    def _ref_path(self, snapshot_id: str) -> Path:
        """Return the hashed, traversal-safe reference path for ``snapshot_id``."""
        if not snapshot_id:
            raise SnapshotStoreError("snapshot_id must be a non-empty string")
        safe_id = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()
        return self.refs_dir / f"{safe_id}.ref"

    def _object_path(self, content_hash: str) -> Path:
        """Return the object blob path for a content hash."""
        return self.objects_dir / f"{content_hash}.json"

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        """Write ``text`` to ``path`` atomically via a sibling temp file."""
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
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

    @staticmethod
    def _to_content(data: Any, snapshot_id: str) -> dict[str, Any]:
        """Validate input and reduce it to hashable content without the ``snapshot_id``."""
        if isinstance(data, ShadowSnapshot):
            if data.snapshot_id != snapshot_id:
                raise SnapshotStoreError(
                    f"Key/model id mismatch: key={snapshot_id!r}, "
                    f"model.snapshot_id={data.snapshot_id!r}"
                )
            snapshot = data
        elif isinstance(data, dict):
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
        else:
            raise SnapshotStoreError("Unsupported data type; expected ShadowSnapshot or dict")
        content = snapshot.model_dump()
        content.pop("snapshot_id", None)
        return content

    @staticmethod
    def _hash_content(content: dict[str, Any]) -> str:
        """Compute a stable sha256 over canonical JSON of the content."""
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_object(object_path: Path) -> dict[str, Any]:
        """Read and parse a JSON object blob, converting IO/parse errors to store errors."""
        try:
            raw = object_path.read_text(encoding="utf-8")
        except OSError as e:
            raise SnapshotStoreError(
                f"Failed to read or parse snapshot object at '{object_path}': {e}"
            ) from e
        except UnicodeDecodeError as e:
            raise SnapshotCorruptionError(
                f"Failed to read or parse snapshot object at '{object_path}': {e}"
            ) from e
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SnapshotCorruptionError(
                f"Failed to read or parse snapshot object at '{object_path}': {e}"
            ) from e

    def _verify_object_hash(
        self, object_path: Path, expected_hash: str, *, snapshot_id: str
    ) -> dict[str, Any]:
        """Read an object and confirm its content hashes to ``expected_hash``.

        Raises ``SnapshotCorruptionError`` on a hash mismatch so neither a
        dedupe-save nor a read can silently re-use an altered object. The blob
        excludes ``snapshot_id`` (that is what enables deduplication), so schema
        validation of the re-attached id happens in ``get_snapshot``.
        """
        content = self._read_object(object_path)

        actual = self._hash_content(content)
        if actual != expected_hash:
            logger.error(
                "snapshot_object_hash_mismatch",
                snapshot_id=snapshot_id,
                expected=expected_hash,
                actual=actual,
                path=str(object_path),
            )
            raise SnapshotCorruptionError(
                f"Object '{expected_hash}' content hash does not match its filename "
                f"(actual={actual}); the object has been altered."
            )
        return content

    def save_snapshot(self, snapshot_id: str, data: Any) -> None:
        """Store a snapshot by content hash and point ``snapshot_id`` at it."""
        content = self._to_content(data, snapshot_id)
        content_hash = self._hash_content(content)
        object_path = self._object_path(content_hash)

        if object_path.exists():
            # Verify the object before dedupe-skipping, so a corrupted one cannot
            # get a new ref pointing at it.
            self._verify_object_hash(object_path, content_hash, snapshot_id=snapshot_id)
            deduped = True
        else:
            try:
                serialized = json.dumps(content, sort_keys=True, indent=2)
                self._atomic_write_text(object_path, serialized)
            except SnapshotStoreError:
                raise
            except Exception as e:
                raise SnapshotStoreError(f"Failed to write snapshot object to disk: {e}") from e
            deduped = False

        try:
            self._atomic_write_text(self._ref_path(snapshot_id), content_hash)
        except SnapshotStoreError:
            raise
        except Exception as e:
            raise SnapshotStoreError(f"Failed to write snapshot reference to disk: {e}") from e

        logger.info(
            "snapshot_saved",
            snapshot_id=snapshot_id,
            content_hash=content_hash,
            deduped=deduped,
        )

    def get_snapshot(self, snapshot_id: str) -> ShadowSnapshot:
        """Resolve ``snapshot_id`` to its content and return a ShadowSnapshot."""
        ref_path = self._ref_path(snapshot_id)
        if not ref_path.exists():
            logger.warning("snapshot_not_found", snapshot_id=snapshot_id)
            raise SnapshotNotFoundError(f"Snapshot '{snapshot_id}' does not exist.")

        content_hash = ref_path.read_text(encoding="utf-8").strip()
        if not _SHA256_HEX.match(content_hash):
            logger.error(
                "snapshot_ref_invalid",
                snapshot_id=snapshot_id,
                content_hash=content_hash,
            )
            raise SnapshotCorruptionError(
                f"Snapshot '{snapshot_id}' has an invalid content hash reference."
            )

        object_path = self._object_path(content_hash)
        if not object_path.exists():
            logger.error(
                "snapshot_object_missing",
                snapshot_id=snapshot_id,
                content_hash=content_hash,
            )
            raise SnapshotCorruptionError(
                f"Snapshot '{snapshot_id}' references missing object '{content_hash}'."
            )

        content = self._verify_object_hash(object_path, content_hash, snapshot_id=snapshot_id)

        try:
            return ShadowSnapshot(snapshot_id=snapshot_id, **content)
        except Exception as e:
            logger.exception(
                "snapshot_data_invalid",
                snapshot_id=snapshot_id,
                content_hash=content_hash,
            )
            raise SnapshotCorruptionError(
                f"Snapshot data does not conform to ShadowSnapshot schema: {e}"
            ) from e
