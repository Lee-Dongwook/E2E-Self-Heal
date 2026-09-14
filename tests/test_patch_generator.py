import pytest

import app.nodes.patch_generator as patch_node
from app.config import settings
from app.graph import refusal_finalizer
from app.schemas import PatchInstruction, PatchOutput, RefusalReason
from app.state import AgentState


def _state(**overrides: object) -> AgentState:
    base: AgentState = {
        "test_script_path": "tests/login.spec.ts",
        "original_code": "await page.locator('#old').click()\n",
        "current_code": "await page.locator('#old').click()\n",
        "error_log": "Timeout waiting for #old",
        "dom_diff_context": [{"file": "src/components/LoginForm.vue"}],
        "dom_snapshot": "",
        "analysis_report": "selector changed",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_patch_generator_uses_detected_framework_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def fake_generate_patch(system_prompt: str, user_prompt: str) -> PatchOutput:
        prompts.append(system_prompt)
        return PatchOutput(instructions=[])

    monkeypatch.setattr(patch_node, "generate_patch", fake_generate_patch)

    result = patch_node.patch_generator(_state())

    assert result["current_code"] == "await page.locator('#old').click()\n"
    assert "Detected framework: Vue 3" in prompts[0]
    assert "NEVER change assertions" in prompts[0]


def test_patch_generator_prefers_explicit_framework_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    def fake_generate_patch(system_prompt: str, user_prompt: str) -> PatchOutput:
        prompts.append(system_prompt)
        return PatchOutput(instructions=[])

    monkeypatch.setattr(patch_node, "generate_patch", fake_generate_patch)

    patch_node.patch_generator(_state(detected_framework="react"))

    assert "Detected framework: React" in prompts[0]


def test_patch_generator_records_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_provider_error(_system_prompt: str, _user_prompt: str) -> PatchOutput:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(patch_node, "generate_patch", raise_provider_error)

    result = patch_node.patch_generator(_state())

    assert result["patch_provider_report"] == {"ok": False}


def test_patch_generator_marks_guardrail_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    output = PatchOutput(
        instructions=[
            PatchInstruction(
                line=1,
                original="await page.locator('#old').click()",
                replacement="expect(page.locator('#new')).toBeVisible()",
                reason="unsafe assertion change",
            )
        ]
    )
    monkeypatch.setattr(patch_node, "generate_patch", lambda _system, _user: output)

    result = patch_node.patch_generator(_state())

    assert result["patch_application_report"]["guardrail_violation"] is True


def test_patch_generator_clears_stale_selector_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        patch_node, "generate_patch", lambda _system, _user: PatchOutput(instructions=[])
    )
    state = _state(verification_report={"ok": False}, loop_count=settings.max_loops)

    result = patch_node.patch_generator(state)
    state.update(result)

    assert state["verification_report"] == {}
    assert refusal_finalizer(state)["refusal_reason"] is RefusalReason.LOOP_CAP_REACHED
