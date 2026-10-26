"""Every prompt sent to a model provider carries redacted evidence.

The boundary is only worth having if each prompt site goes through it, so these tests assert on
the *prompt* each node builds rather than on the node's return value. The one deliberate exception
is the test source, which must reach the model byte for byte.
"""

import pytest
from pydantic import SecretStr

import app.config as config_module
from app.evidence_boundary import prepare_evidence, redaction_state_update
from app.nodes.decline_adjudicator import decline_adjudicator
from app.nodes.diagnoser import diagnoser
from app.nodes.patch_generator import patch_generator
from app.nodes.reviewer import reviewer
from app.schemas import DiagnosisVerdict, DiagnosisVerdictOutput, PatchOutput, ReviewOutput
from app.state import AgentState
from tests.factories import make_state

SECRET = "sk-live-9f8e7d6c5b4a"

# Every field a prompt can be assembled from carries the secret, so a node that forgets to route
# one of them through the boundary fails the matching test below.
LEAKY_STATE = make_state(
    test_script_path="tests/login.spec.ts",
    original_code="await page.click('#login');\n",
    current_code="await page.click('#login');\n",
    error_log=(
        "Error: locator('#login') timed out.\n"
        f"  request header was authorization: bearer {SECRET}\n"
        "  at tests/login.spec.ts:4:16"
    ),
    dom_diff_context=[
        {
            "file": "src/Login.tsx",
            "line": 12,
            "previous": {"tag": "input", "attributes": {"data-token": SECRET}},
            "current": {"tag": "input", "attributes": {"data-token": SECRET}},
        }
    ],
    dom_snapshot=f"- textbox: Email\n- button: Submit {SECRET}",
    analysis_report=f"The submit handler quotes the credential {SECRET} inline.",
)


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the secret through configuration, as a user would."""
    monkeypatch.setenv("E2E_TEST_PROVIDER_KEY", SECRET)
    monkeypatch.setattr(config_module.settings, "sensitive_env_vars", "E2E_TEST_PROVIDER_KEY")
    monkeypatch.setattr(config_module.settings, "sensitive_values", SecretStr(""))


def _captured(monkeypatch: pytest.MonkeyPatch, module: str, name: str, answer: object) -> list[str]:
    """Patch ``module.name`` and collect the user prompt it is called with."""
    prompts: list[str] = []

    def fake(_system: str, user: str) -> object:
        prompts.append(user)
        return answer

    monkeypatch.setattr(f"{module}.{name}", fake)
    return prompts


def test_prepare_evidence_redacts_every_field_and_counts_the_replacements() -> None:
    prepared = prepare_evidence(LEAKY_STATE)

    assert SECRET not in prepared.error_log
    assert SECRET not in prepared.aria_snapshot
    assert SECRET not in prepared.analysis_report
    assert SECRET not in str(prepared.dom_diff_context)
    assert prepared.redaction_count >= 4


def test_the_diagnoser_prompt_carries_no_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = _captured(monkeypatch, "app.nodes.diagnoser", "generate_diagnosis", "diagnosis")

    result = diagnoser(LEAKY_STATE)

    assert SECRET not in prompts[0]
    assert "[REDACTED]" in prompts[0]
    assert result["redaction_count"] == prepare_evidence(LEAKY_STATE).redaction_count
    assert result["redaction_applied"] is True


def test_the_decline_adjudicator_prompt_carries_no_configured_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _captured(
        monkeypatch,
        "app.nodes.decline_adjudicator",
        "generate_diagnosis_verdict",
        DiagnosisVerdictOutput(verdict=DiagnosisVerdict.UNCLEAR, rationale="mixed signal"),
    )

    decline_adjudicator(LEAKY_STATE)

    # Both the re-sent diagnosis and the failure evidence cross the boundary.
    assert SECRET not in prompts[0]
    assert "[REDACTED]" in prompts[0]


def test_the_reviewer_prompt_carries_no_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = _captured(
        monkeypatch, "app.nodes.reviewer", "generate_review", ReviewOutput(findings=[])
    )

    reviewer(LEAKY_STATE)

    assert SECRET not in prompts[0]
    assert "[REDACTED]" in prompts[0]


def test_the_patch_generator_prompt_carries_no_configured_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _captured(
        monkeypatch, "app.nodes.patch_generator", "generate_patch", PatchOutput(instructions=[])
    )

    result = patch_generator(LEAKY_STATE)

    assert SECRET not in prompts[0]
    assert "[REDACTED]" in prompts[0]
    assert result["redaction_applied"] is True


def test_the_test_source_reaches_the_model_unredacted(monkeypatch: pytest.MonkeyPatch) -> None:
    # The documented limit of the boundary: patch instructions must match the file byte for
    # byte, so rewriting the code the model reads would make every instruction unappliable.
    source = f"await page.fill('#password', '{SECRET}');\n"
    prompts = _captured(
        monkeypatch, "app.nodes.patch_generator", "generate_patch", PatchOutput(instructions=[])
    )

    patch_generator({**LEAKY_STATE, "current_code": source})

    assert SECRET in prompts[0]


def test_the_reported_line_number_comes_from_the_raw_log() -> None:
    # Redaction rewrites text, so the JSX chunker must read the line index from the raw log.
    prepared = prepare_evidence(LEAKY_STATE)

    assert prepared.error_line == 4


def test_redaction_counters_accumulate_across_nodes() -> None:
    state: AgentState = {**LEAKY_STATE, "redaction_count": 5, "redaction_applied": True}

    update = redaction_state_update(state, prepare_evidence(state))

    assert update["redaction_count"] > 5
    assert update["redaction_applied"] is True


def test_a_state_with_no_secrets_reports_no_redaction() -> None:
    clean: AgentState = {
        **LEAKY_STATE,
        "error_log": "Error: timeout",
        "analysis_report": "",
        "dom_snapshot": "",
        "dom_diff_context": [{"file": "src/Login.tsx", "line": 12}],
    }

    update = redaction_state_update(clean, prepare_evidence(clean))

    assert update == {"redaction_count": 0, "redaction_applied": False}
