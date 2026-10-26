"""Builders shared by the test modules.

Plain functions in a module rather than conftest fixtures, so they also work in module-level
constants and parametrize tables.
"""

from typing import cast

from app.schemas import DiagnosisVerdict, DiagnosisVerdictOutput
from app.state import AgentState

_STATE_KEYS = AgentState.__required_keys__ | AgentState.__optional_keys__


def make_state(**fields: object) -> AgentState:
    """A repair-graph state with every required key; ``fields`` set or override any key.

    A key ``AgentState`` does not declare is rejected, so a typo cannot silently leave the
    real key at its default.
    """
    unknown = fields.keys() - _STATE_KEYS
    if unknown:
        raise KeyError(f"not AgentState keys: {sorted(unknown)}")
    state: dict[str, object] = {
        "test_script_path": "t.spec.ts",
        "original_code": "",
        "current_code": "",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    state.update(fields)
    return cast(AgentState, state)


def make_verdict(verdict: DiagnosisVerdict) -> DiagnosisVerdictOutput:
    """The decline adjudicator's structured answer, with a fixed rationale."""
    return DiagnosisVerdictOutput(verdict=verdict, rationale="the handler is gone")
