import json
import pytest
from app.shadow.config import ShadowConfig
from app.shadow.workspace import ShadowWorkspace
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot, ShadowSnapshot
from app.shadow.snapshot_store import (
    SnapshotStore,
    SnapshotStoreError,
    SnapshotNotFoundError,
    SnapshotCorruptionError,
)


def test_save_and_load_shadow_snapshot(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    req = CapturedRequest(
        method="GET", url="https://api.example.com/test", headers={"Accept": "*/*"}
    )
    res = CapturedResponse(status=200, headers={"Content-Type": "text/plain"}, body="hello-world")
    net_snap = NetworkSnapshot(request=req, response=res)

    snap = ShadowSnapshot(
        snapshot_id="test_snap_1",
        metadata={"user": "tester", "env": "ci"},
        network_snapshots=[net_snap],
    )

    # Test saving
    store.save_snapshot("test_snap_1", snap)

    # Check path existence
    expected_file = ws.snapshots_dir / "test_snap_1.json"
    assert expected_file.exists()

    # Test loading
    loaded_snap = store.get_snapshot("test_snap_1")
    assert loaded_snap.snapshot_id == "test_snap_1"
    assert loaded_snap.metadata["user"] == "tester"
    assert len(loaded_snap.network_snapshots) == 1
    assert loaded_snap.network_snapshots[0].request.url == "https://api.example.com/test"
    assert loaded_snap.network_snapshots[0].response.body == "hello-world"


def test_save_dict_and_load(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    dict_data = {
        "snapshot_id": "test_snap_dict",
        "metadata": {"source": "manual"},
        "network_snapshots": [
            {
                "request": {
                    "method": "POST",
                    "url": "https://api.example.com/submit",
                    "headers": {},
                },
                "response": {"status": 201, "headers": {}, "body": "created"},
            }
        ],
    }

    # Test saving dictionary data
    store.save_snapshot("test_snap_dict", dict_data)

    # Test loading
    loaded_snap = store.get_snapshot("test_snap_dict")
    assert loaded_snap.snapshot_id == "test_snap_dict"
    assert loaded_snap.network_snapshots[0].request.method == "POST"
    assert loaded_snap.network_snapshots[0].response.status == 201


def test_save_invalid_dict_raises_error(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    invalid_dict = {
        "snapshot_id": "invalid",
        "network_snapshots": [{"request": {"bad_field": True}}],  # Missing required fields
    }

    with pytest.raises(SnapshotStoreError) as exc_info:
        store.save_snapshot("invalid", invalid_dict)
    assert "Invalid snapshot dict structure" in str(exc_info.value)


def test_save_unsupported_type_raises_error(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    with pytest.raises(SnapshotStoreError) as exc_info:
        store.save_snapshot("unsupported", [1, 2, 3])
    assert "Unsupported data type" in str(exc_info.value)


def test_get_snapshot_not_found(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    with pytest.raises(SnapshotNotFoundError) as exc_info:
        store.get_snapshot("does_not_exist")
    assert "does not exist" in str(exc_info.value)


def test_get_snapshot_corrupted_json(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    # Write invalid JSON content manually
    corrupt_file = ws.snapshots_dir / "corrupted.json"
    corrupt_file.write_text("{bad-json:", encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError) as exc_info:
        store.get_snapshot("corrupted")
    assert "is not valid JSON" in str(exc_info.value)


def test_get_snapshot_invalid_schema(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)

    # Write a valid JSON file but with incorrect fields that mismatch the Pydantic schema
    invalid_schema_file = ws.snapshots_dir / "bad_schema.json"
    invalid_schema_file.write_text(json.dumps({"wrong_field": "data"}), encoding="utf-8")

    with pytest.raises(SnapshotCorruptionError) as exc_info:
        store.get_snapshot("bad_schema")
    assert "does not conform to ShadowSnapshot schema" in str(exc_info.value)
