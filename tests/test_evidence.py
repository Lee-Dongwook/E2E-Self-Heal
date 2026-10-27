import hashlib
from pathlib import Path

import pytest

import app.config as config_module
from app.evidence import (
    add_candidate,
    add_loop_event,
    build_evidence_bundle,
    build_unavailable_evidence,
)
from app.schemas import AstLockRejection, EvidenceDomDiff, PatchInstruction, ProviderProvenance
from app.state import AgentState
from app.verify.ast_lock import AstLockVerdict
from tests.factories import make_state


def _state(**overrides: object) -> AgentState:
    defaults = {
        "test_script_path": "tests/login.spec.ts",
        "error_log": "Error: locator('#login') timed out",
    }
    return make_state(**(defaults | overrides))


def test_evidence_bundle_redacts_sensitive_values_and_references_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
                "line": 1,
                "previous": {"tag": "input", "attributes": {"password": "secret"}},
                "current": {
                    "tag": "input",
                    "attributes": {"url": "https://example.test/?token=secret"},
                },
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
    entry = bundle.dom_diff_context[0]
    assert isinstance(entry, EvidenceDomDiff)
    assert "attributes" in entry.previous
    assert entry.previous["attributes"]["password"] == "[REDACTED]"
    assert "secret" not in bundle.parsed_error
    assert bundle.candidates[0].rejection is not None
    assert "secret" not in bundle.candidates[0].rejection
    assert bundle.aria_snapshot is not None
    assert bundle.aria_snapshot.source_path == "test-results/login/error-context.md"
    # URL redaction keeps the marker percent-encoded, so a URL reads the same in a prompt, a
    # log line, and a persisted Shadow snapshot.
    assert (
        bundle.aria_snapshot.sha256
        == hashlib.sha256(b"page: https://example.test/?token=%5BREDACTED%5D").hexdigest()
    )


def _instruction() -> PatchInstruction:
    return PatchInstruction(
        line=1,
        original="page.locator('#old')",
        replacement="page.locator('#new')",
        reason="id renamed",
        selector="#new",
    )


def test_rejected_candidate_carries_the_ast_lock_verdict_into_the_bundle() -> None:
    verdict = AstLockVerdict(
        allowed=False, reason="disallowed_ast_change", node_kind="expression_statement", line=3
    )
    candidates = add_candidate(
        _state(),
        source="llm",
        instructions=[_instruction()],
        outcome="rejected",
        rejection="AST lock rejected the patch",
        ast_lock=verdict,
    )

    bundle = build_evidence_bundle(
        _state(evidence_candidates=candidates),
        initial_error_log="Error: locator('#old') timed out",
    )

    assert bundle.candidates[0].ast_lock == AstLockRejection(
        reason="disallowed_ast_change", node_kind="expression_statement", line=3
    )


def test_an_allowing_verdict_is_not_recorded_as_a_rejection() -> None:
    allowed = AstLockVerdict(allowed=True, reason="structurally_equivalent")

    candidates = add_candidate(
        _state(), source="llm", instructions=[_instruction()], ast_lock=allowed
    )

    assert "ast_lock" not in candidates[0]


def test_candidate_without_a_lock_verdict_has_no_ast_lock_detail() -> None:
    candidates = add_candidate(
        _state(), source="llm", instructions=[_instruction()], outcome="rejected"
    )

    bundle = build_evidence_bundle(
        _state(evidence_candidates=candidates),
        initial_error_log="Error: locator('#old') timed out",
    )

    assert bundle.candidates[0].ast_lock is None


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


# --- provider provenance (#377) -----------------------------------------------------------


def test_evidence_bundle_reports_where_the_evidence_went(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(config_module.settings, "llm_model", "claude-sonnet-4-6")

    bundle = build_evidence_bundle(_state(), initial_error_log="Error: locator('#login') timed out")

    assert bundle.provenance == ProviderProvenance(
        provider="anthropic",
        model="claude-sonnet-4-6",
        endpoint="https://api.anthropic.com",
        mode="remote",
        redaction_applied=False,
        redaction_count=0,
    )


def test_evidence_bundle_carries_the_boundary_redaction_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module.settings, "llm_provider", "ollama")
    monkeypatch.setattr(config_module.settings, "llm_model", "llama3.2")
    monkeypatch.setattr(config_module.settings, "llm_base_url", "http://localhost:11434")

    bundle = build_evidence_bundle(
        _state(redaction_count=2, redaction_applied=True),
        initial_error_log="Error: locator('#login') timed out",
    )

    assert bundle.provenance is not None
    assert bundle.provenance.mode == "local"
    assert bundle.provenance.endpoint == "http://localhost:11434"
    assert bundle.provenance.redaction_count == 2
    assert bundle.provenance.redaction_applied is True


def test_unavailable_evidence_has_no_provenance() -> None:
    # The graph never ran, so nothing was sent anywhere and there is nothing to report.
    bundle = build_unavailable_evidence("Error: suite target missing", [])

    assert bundle.provenance is None
