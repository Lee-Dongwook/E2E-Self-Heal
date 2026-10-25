"""Tests for snapshot-store identity and content integrity (Issue #219)."""

import json
from pathlib import Path

import pytest

from app.shadow.config import ShadowConfig
from app.shadow.content_addressed_snapshot_store import ContentAddressedSnapshotStore
from app.shadow.in_memory_snapshot_store import InMemorySnapshotStore
from app.shadow.schemas import ShadowSnapshot
from app.shadow.snapshot_store import (
    SnapshotCorruptionError,
    SnapshotNotFoundError,
    SnapshotStore,
    SnapshotStoreError,
)
from app.shadow.workspace import ShadowWorkspace
import app.shadow.snapshot_store as snapshot_store_module


def _snapshot(snapshot_id: str, extra: dict | None = None) -> dict:
    payload = {
        "snapshot_id": snapshot_id,
        "metadata": {},
        "network_snapshots": [],
        "state_snapshots": [],
    }
    if extra:
        payload.update(extra)
    return payload


@pytest.fixture
def plain_store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path))))


@pytest.fixture
def ca_store(tmp_path: Path) -> ContentAddressedSnapshotStore:
    return ContentAddressedSnapshotStore(ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path))))


@pytest.fixture(
    params=["plain", "content-addressed", "in-memory"],
    ids=["plain", "content-addressed", "in-memory"],
)
def contract_store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> SnapshotStore | ContentAddressedSnapshotStore | InMemorySnapshotStore:
    if request.param == "plain":
        return SnapshotStore(ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path))))
    if request.param == "content-addressed":
        return ContentAddressedSnapshotStore(
            ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
        )
    return InMemorySnapshotStore()


@pytest.mark.parametrize("data_kind", ["model", "dict"], ids=["model", "dict"])
def test_store_contract_save_and_load(
    contract_store: SnapshotStore | ContentAddressedSnapshotStore | InMemorySnapshotStore,
    data_kind: str,
) -> None:
    snapshot_id = f"contract-{data_kind}"
    data = (
        ShadowSnapshot(snapshot_id=snapshot_id) if data_kind == "model" else _snapshot(snapshot_id)
    )

    contract_store.save_snapshot(snapshot_id, data)

    assert contract_store.get_snapshot(snapshot_id).snapshot_id == snapshot_id


def test_store_contract_missing_snapshot(
    contract_store: SnapshotStore | ContentAddressedSnapshotStore | InMemorySnapshotStore,
) -> None:
    with pytest.raises(SnapshotStoreError, match="does not exist"):
        contract_store.get_snapshot("missing")


def test_store_contract_invalid_dict(
    contract_store: SnapshotStore | ContentAddressedSnapshotStore | InMemorySnapshotStore,
) -> None:
    with pytest.raises(SnapshotStoreError, match="Invalid snapshot dict structure"):
        contract_store.save_snapshot("invalid", {"network_snapshots": [{"request": {}}]})


def test_store_contract_unsupported_type(
    contract_store: SnapshotStore | ContentAddressedSnapshotStore | InMemorySnapshotStore,
) -> None:
    with pytest.raises(SnapshotStoreError, match="Unsupported data type"):
        contract_store.save_snapshot("unsupported", [1, 2, 3])


# ---------- Plain store ----------


def test_plain_distinct_full_ids_cannot_alias(plain_store: SnapshotStore) -> None:
    """Distinct full ids like 'team/a' and 'a' must not share a file."""
    plain_store.save_snapshot("team/a", _snapshot("team/a"))
    plain_store.save_snapshot("a", _snapshot("a"))

    assert plain_store.get_snapshot("team/a").snapshot_id == "team/a"
    assert plain_store.get_snapshot("a").snapshot_id == "a"

    path_a = plain_store._get_snapshot_path("a")
    path_team_a = plain_store._get_snapshot_path("team/a")
    assert path_a != path_team_a
    assert path_a.exists()
    assert path_team_a.exists()


def test_plain_rejects_key_model_id_mismatch_on_save(plain_store: SnapshotStore) -> None:
    """Saving a snapshot whose embedded id disagrees with the key must error."""
    with pytest.raises(SnapshotStoreError, match="Key/model id mismatch"):
        plain_store.save_snapshot("wrong-key", _snapshot("actual-id"))


def test_plain_dict_without_id_adopts_save_key(plain_store: SnapshotStore) -> None:
    """A dict without snapshot_id adopts the save key (documented behavior)."""
    plain_store.save_snapshot("adopted", {"metadata": {"m": 1}})
    assert plain_store.get_snapshot("adopted").snapshot_id == "adopted"


def test_plain_reader_never_sees_partial_json(plain_store: SnapshotStore) -> None:
    """Atomic write: a crash before rename must leave the old snapshot intact."""
    plain_store.save_snapshot("stable", _snapshot("stable"))

    original = snapshot_store_module.os.replace

    def boom(src: str, dst: str) -> None:
        raise OSError("simulated crash before rename")

    snapshot_store_module.os.replace = boom  # type: ignore[assignment]
    try:
        with pytest.raises(SnapshotStoreError):
            plain_store.save_snapshot("stable", _snapshot("stable", {"metadata": {"x": 1}}))
    finally:
        snapshot_store_module.os.replace = original  # type: ignore[assignment]

    loaded = plain_store.get_snapshot("stable")
    assert loaded.snapshot_id == "stable"
    assert loaded.metadata == {}


def test_plain_rejects_stored_id_mismatch_on_read(plain_store: SnapshotStore) -> None:
    """A file whose embedded id disagrees with its lookup key is treated as corrupted."""
    plain_store.save_snapshot("original", _snapshot("original"))
    path = plain_store._get_snapshot_path("original")

    tampered = _snapshot("tampered-id")
    path.write_text(json.dumps(tampered, sort_keys=True, indent=2), encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError, match="does not match"):
        plain_store.get_snapshot("original")


def test_plain_rejects_empty_id(plain_store: SnapshotStore) -> None:
    with pytest.raises(SnapshotStoreError):
        plain_store.save_snapshot("", _snapshot(""))
    with pytest.raises(SnapshotStoreError):
        plain_store.get_snapshot("")


# ---------- Content-addressed store ----------


def test_ca_distinct_full_ids_cannot_alias(ca_store: ContentAddressedSnapshotStore) -> None:
    """Distinct ids must resolve to distinct ref files (no path collisions)."""
    ca_store.save_snapshot("team/a", _snapshot("team/a"))
    ca_store.save_snapshot("a", _snapshot("a"))

    ref_a = ca_store._ref_path("a")
    ref_team_a = ca_store._ref_path("team/a")
    assert ref_a != ref_team_a
    assert ref_a.exists()
    assert ref_team_a.exists()

    assert ca_store.get_snapshot("team/a").snapshot_id == "team/a"
    assert ca_store.get_snapshot("a").snapshot_id == "a"


def test_ca_dict_without_id_adopts_save_key(ca_store: ContentAddressedSnapshotStore) -> None:
    """A dict without snapshot_id adopts the save key (documented behavior)."""
    ca_store.save_snapshot("adopted", {"metadata": {"m": 1}})
    assert ca_store.get_snapshot("adopted").snapshot_id == "adopted"


def test_ca_read_rejects_altered_content(ca_store: ContentAddressedSnapshotStore) -> None:
    """Editing the JSON under an existing hash must be detected on read."""
    ca_store.save_snapshot("x", _snapshot("x", {"metadata": {"v": 1}}))

    ref_path = ca_store._ref_path("x")
    content_hash = ref_path.read_text(encoding="utf-8").strip()
    obj_path = ca_store._object_path(content_hash)
    assert obj_path.exists()

    altered = json.loads(obj_path.read_text(encoding="utf-8"))
    altered["metadata"] = {"v": 999, "injected": True}
    obj_path.write_text(json.dumps(altered, sort_keys=True, indent=2), encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError, match="has been altered"):
        ca_store.get_snapshot("x")


def test_ca_save_rejects_corrupted_existing_object(
    ca_store: ContentAddressedSnapshotStore,
) -> None:
    """A save that would dedupe onto a corrupted existing object must fail."""
    ca_store.save_snapshot("x", _snapshot("x", {"metadata": {"v": 1}}))

    ref_path = ca_store._ref_path("x")
    content_hash = ref_path.read_text(encoding="utf-8").strip()
    obj_path = ca_store._object_path(content_hash)
    obj_path.write_text('{"broken": true}', encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError):
        ca_store.save_snapshot("y", _snapshot("y", {"metadata": {"v": 1}}))


def test_ca_rejects_key_model_id_mismatch_on_save(
    ca_store: ContentAddressedSnapshotStore,
) -> None:
    with pytest.raises(SnapshotStoreError, match="Key/model id mismatch"):
        ca_store.save_snapshot("wrong-key", _snapshot("actual-id"))


def test_ca_ref_with_invalid_hash_is_corruption(
    ca_store: ContentAddressedSnapshotStore,
) -> None:
    """A ref file that no longer contains a valid sha256 is reported as corruption."""
    ca_store.save_snapshot("x", _snapshot("x"))
    ref_path = ca_store._ref_path("x")
    ref_path.write_text("not-a-sha256-at-all", encoding="utf-8")
    with pytest.raises(SnapshotCorruptionError, match="invalid content hash"):
        ca_store.get_snapshot("x")


def test_ca_missing_object_is_corruption(ca_store: ContentAddressedSnapshotStore) -> None:
    ca_store.save_snapshot("x", _snapshot("x"))
    ref_path = ca_store._ref_path("x")
    content_hash = ref_path.read_text(encoding="utf-8").strip()
    ca_store._object_path(content_hash).unlink()
    with pytest.raises(SnapshotCorruptionError, match="missing object"):
        ca_store.get_snapshot("x")


def test_ca_nonexistent_snapshot_not_found(ca_store: ContentAddressedSnapshotStore) -> None:

    with pytest.raises(SnapshotNotFoundError):
        ca_store.get_snapshot("never-saved")


def test_plain_corrupted_json_is_rejected(plain_store: SnapshotStore) -> None:
    corrupt_file = plain_store._get_snapshot_path("corrupted")
    corrupt_file.write_text("{bad-json:", encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError, match="not valid JSON"):
        plain_store.get_snapshot("corrupted")


def test_plain_invalid_schema_is_rejected(plain_store: SnapshotStore) -> None:
    invalid_schema_file = plain_store._get_snapshot_path("bad_schema")
    invalid_schema_file.write_text(json.dumps({"wrong_field": "data"}), encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError, match="does not conform"):
        plain_store.get_snapshot("bad_schema")
