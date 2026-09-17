from pathlib import Path

from app.evidence import add_candidate, add_loop_event, build_evidence_bundle
from app.schemas import PatchInstruction
from app.state import AgentState


def _state(**overrides: object) -> AgentState:
    state: AgentState = {
        "test_script_path": "tests/login.spec.ts",
        "original_code": "",
        "current_code": "",
        "error_log": "Error: locator('#login') timed out",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_evidence_bundle_redacts_sensitive_values_and_references_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    snapshot_path = tmp_path / "test-results" / "login" / "error-context.md"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("snapshot")
    monkeypatch.setattr("app.evidence.workspace_root", lambda: tmp_path)
    state = _state(
        dom_snapshot="page: https://example.test/?token=secret",
        dom_snapshot_source=str(snapshot_path),
        dom_diff_context=[
            {
                "file": "src/login.tsx",
                "password": "secret",
                "url": "https://example.test/?token=secret",
            }
        ],
        evidence_candidates=[
            {
                "loop_count": 0,
                "source": "llm",
                "instructions": [],
                "outcome": "rejected",
                "rejection": "provider at https://example.test/?api_key=secret",
            }
        ],
    )

    bundle = build_evidence_bundle(
        state,
        initial_error_log="Error: locator('#login') at https://example.test/?token=secret",
    )

    assert bundle.failing_selector == "#login"
    assert bundle.dom_diff_context[0]["password"] == "[REDACTED]"
    assert "secret" not in bundle.parsed_error
    assert bundle.candidates[0].rejection is not None
    assert "secret" not in bundle.candidates[0].rejection
    assert bundle.aria_snapshot is not None
    assert bundle.aria_snapshot.source_path == "test-results/login/error-context.md"
    assert len(bundle.aria_snapshot.sha256) == 64


def test_candidate_and_loop_history_preserve_attempt_order() -> None:
    instruction = PatchInstruction(
        line=1,
        original="page.locator('#old')",
        replacement="page.locator('#new')",
        reason="id renamed",
        selector="#new",
    )
    state = _state()
    first_candidates = add_candidate(
        state, source="memory", instructions=[instruction], memory_score=0.98
    )
    state.update({"evidence_candidates": first_candidates})
    state.update({"loop_count": 1})
    second_candidates = add_candidate(state, source="llm", instructions=[instruction])
    history = add_loop_event(state, "patch_generator", "candidate_generated", instruction_count=1)

    bundle = build_evidence_bundle(
        _state(evidence_candidates=second_candidates, evidence_history=history, loop_count=1),
        initial_error_log="Error: locator('#old') timed out",
    )

    assert [(candidate.loop_count, candidate.source) for candidate in bundle.candidates] == [
        (0, "memory"),
        (1, "llm"),
    ]
    assert bundle.loop_history[0].stage == "patch_generator"
