import app.nodes.patch_generator as patch_node
from app.schemas import PatchOutput
from app.state import AgentState


def _state(**overrides) -> AgentState:
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


def test_patch_generator_uses_detected_framework_guidance(monkeypatch):
    prompts: list[str] = []

    def fake_generate_patch(system_prompt, user_prompt):
        prompts.append(system_prompt)
        return PatchOutput(instructions=[])

    monkeypatch.setattr(patch_node, "generate_patch", fake_generate_patch)

    result = patch_node.patch_generator(_state())

    assert result["current_code"] == "await page.locator('#old').click()\n"
    assert "Detected framework: Vue 3" in prompts[0]
    assert "NEVER change assertions" in prompts[0]


def test_patch_generator_prefers_explicit_framework_hint(monkeypatch):
    prompts: list[str] = []

    def fake_generate_patch(system_prompt, user_prompt):
        prompts.append(system_prompt)
        return PatchOutput(instructions=[])

    monkeypatch.setattr(patch_node, "generate_patch", fake_generate_patch)

    patch_node.patch_generator(_state(detected_framework="react"))

    assert "Detected framework: React" in prompts[0]
