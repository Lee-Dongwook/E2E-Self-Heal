from pathlib import Path

import pytest

from app.config import settings
from app.shadow import (
    CapturedRequest,
    CapturedResponse,
    LocalStorageSnapshot,
    NetworkSnapshot,
    ShadowConfig,
    ShadowRunResult,
    ShadowSnapshot,
    ShadowWorkspace,
)
from app.shadow.runtime import run_shadow
from app.shadow.snapshot_store import SnapshotStore


REPOSITORY_ROOT = Path(__file__).parents[1]
EXAMPLES_DIR = REPOSITORY_ROOT / "examples"
PLAYWRIGHT = EXAMPLES_DIR / "node_modules" / ".bin" / "playwright"
REPLAY_SPEC = EXAMPLES_DIR / "scenarios" / "shadow-replay" / "replay.spec.ts"
REPLAY_CONFIG = EXAMPLES_DIR / "shadow.playwright.config.ts"


@pytest.mark.skipif(
    not PLAYWRIGHT.is_file(), reason="example Playwright dependencies not installed"
)
def test_shadow_replay_uses_the_real_test_context(tmp_path, monkeypatch) -> None:
    config = ShadowConfig(workspace_dir=str(tmp_path / "shadow"))
    store = SnapshotStore(ShadowWorkspace(config))
    store.save_snapshot(
        "cross-process",
        ShadowSnapshot(
            snapshot_id="cross-process",
            network_snapshots=[
                NetworkSnapshot(
                    request=CapturedRequest(
                        method="GET",
                        url="https://shadow.example.test/",
                    ),
                    response=CapturedResponse(
                        status=200,
                        headers={"content-type": "text/html; charset=utf-8"},
                        body="<!doctype html><title>Shadow replay</title>",
                    ),
                ),
                NetworkSnapshot(
                    request=CapturedRequest(
                        method="GET",
                        url="https://api.example.test/data",
                    ),
                    response=CapturedResponse(status=200, body="mocked_body"),
                ),
            ],
            state_snapshots=[
                LocalStorageSnapshot(
                    origin="https://shadow.example.test",
                    items={"theme": "dark"},
                )
            ],
        ),
    )
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "workspace_root", str(REPOSITORY_ROOT))
    monkeypatch.setattr(
        settings,
        "playwright_cmd",
        f"{PLAYWRIGHT} test --config {REPLAY_CONFIG} --workers=1",
    )

    result = run_shadow(
        test_path=REPLAY_SPEC,
        snapshot_id="cross-process",
        config=config,
    )

    assert isinstance(result, ShadowRunResult)
    assert result.is_success is True
    assert result.matched_count == 2
    assert result.missed_count == 0
    assert result.score > 0
    assert not list(REPLAY_SPEC.parent.glob(".e2e-healer-shadow-*"))
