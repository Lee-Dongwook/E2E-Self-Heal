"""Tests for the content-addressed ISnapshotStore implementation."""

import pytest

from app.shadow.config import ShadowConfig
from app.shadow.content_addressed_snapshot_store import ContentAddressedSnapshotStore
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot, ShadowSnapshot
from app.shadow.snapshot_store import SnapshotCorruptionError
from app.shadow.workspace import ShadowWorkspace


def _make_store(tmp_path) -> ContentAddressedSnapshotStore:
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    return ContentAddressedSnapshotStore(ws)


def _make_snapshot(snapshot_id: str, body: str = "hello-world") -> ShadowSnapshot:
    req = CapturedRequest(
        method="GET", url="https://api.example.com/test", headers={"Accept": "*/*"}
    )
    res = CapturedResponse(status=200, headers={"Content-Type": "text/plain"}, body=body)
    net_snap = NetworkSnapshot(request=req, response=res)
    return ShadowSnapshot(
        snapshot_id=snapshot_id,
        metadata={"user": "tester", "env": "ci"},
        network_snapshots=[net_snap],
    )


def test_identical_payloads_map_to_one_object(tmp_path):
    """Two ids with identical content share a single stored object (dedup)."""
    store = _make_store(tmp_path)

    store.save_snapshot("snap_a", _make_snapshot("snap_a", body="same-body"))
    store.save_snapshot("snap_b", _make_snapshot("snap_b", body="same-body"))

    objects = list(store.objects_dir.glob("*.json"))
    refs = list(store.refs_dir.glob("*.ref"))

    # One deduplicated object, two references pointing at it.
    assert len(objects) == 1
    assert len(refs) == 2

    # Both ids resolve to the same content hash.
    hash_a = store._ref_path("snap_a").read_text(encoding="utf-8").strip()
    hash_b = store._ref_path("snap_b").read_text(encoding="utf-8").strip()
    assert hash_a == hash_b

    # Each id round-trips with its own identity preserved.
    assert store.get_snapshot("snap_a").snapshot_id == "snap_a"
    assert store.get_snapshot("snap_b").snapshot_id == "snap_b"


def test_differing_payloads_map_to_distinct_objects(tmp_path):
    store = _make_store(tmp_path)

    store.save_snapshot("snap_a", _make_snapshot("snap_a", body="body-one"))
    store.save_snapshot("snap_b", _make_snapshot("snap_b", body="body-two"))

    objects = list(store.objects_dir.glob("*.json"))
    assert len(objects) == 2


def test_saving_same_id_twice_updates_reference(tmp_path):
    store = _make_store(tmp_path)

    store.save_snapshot("snap", _make_snapshot("snap", body="first"))
    store.save_snapshot("snap", _make_snapshot("snap", body="second"))

    loaded = store.get_snapshot("snap")
    assert loaded.network_snapshots[0].response.body == "second"


def test_get_snapshot_dangling_reference_raises_corruption(tmp_path):
    store = _make_store(tmp_path)

    # A ref pointing at a valid-looking hash whose object was never written.
    store._ref_path("dangling").write_text("de" * 32, encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError):
        store.get_snapshot("dangling")


def test_get_snapshot_corrupted_object_raises_corruption(tmp_path):
    store = _make_store(tmp_path)

    store.save_snapshot("snap", _make_snapshot("snap"))
    content_hash = store._ref_path("snap").read_text(encoding="utf-8").strip()
    (store.objects_dir / f"{content_hash}.json").write_text("{bad-json:", encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError):
        store.get_snapshot("snap")
