from app.prompts.patch_generator import (
    build_system_prompt,
    detect_framework,
    selector_strategy_for,
)


def test_strategy_guidance_varies_by_framework():
    react = selector_strategy_for("react")
    vue = selector_strategy_for("vue")
    svelte = selector_strategy_for("svelte")

    assert react.framework == "react"
    assert "React" in react.guidance
    assert "CSS-module" in react.guidance

    assert vue.framework == "vue"
    assert "Vue 3" in vue.guidance
    assert "data-v-*" in vue.guidance

    assert svelte.framework == "svelte"
    assert "Svelte" in svelte.guidance
    assert "compiled Svelte class names" in svelte.guidance


def test_unknown_framework_uses_generic_strategy():
    strategy = selector_strategy_for("solid")

    assert strategy.framework == "generic"
    assert "generic or unknown" in strategy.guidance


def test_build_system_prompt_preserves_guardrails():
    prompt = build_system_prompt("vue")

    assert "You may ONLY fix failing locators" in prompt
    assert "NEVER change assertions" in prompt
    assert "Detected framework: Vue 3" in prompt


def test_detect_framework_from_dom_diff_file():
    result = detect_framework(
        "tests/login.spec.ts",
        "await page.getByRole('button').click()",
        [{"file": "src/components/LoginForm.svelte"}],
    )

    assert result == "svelte"


def test_detect_framework_from_nearby_package_json(tmp_path):
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (root / "package.json").write_text('{"dependencies": {"vue": "^3.5.0"}}')

    result = detect_framework(
        str(tests / "login.spec.ts"),
        "await page.locator('#submit').click()",
        [],
    )

    assert result == "vue"
