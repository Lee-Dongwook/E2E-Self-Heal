from pathlib import Path

from app.prompts.patch_generator import (
    build_system_prompt,
    detect_framework,
    selector_strategy_for,
)


def test_strategy_guidance_varies_by_framework() -> None:
    react = selector_strategy_for("react")
    vue = selector_strategy_for("vue")
    svelte = selector_strategy_for("svelte")

    assert react.framework == "react"

    assert vue.framework == "vue"

    assert svelte.framework == "svelte"


def test_unknown_framework_uses_generic_strategy() -> None:
    strategy = selector_strategy_for("solid")

    assert strategy.framework == "generic"


def test_build_system_prompt_preserves_guardrails() -> None:
    prompt = build_system_prompt("vue")

    assert "You may ONLY fix failing locators" in prompt
    assert "NEVER change assertions" in prompt
    assert "preserve every input value" in prompt
    assert "Detected framework: Vue 3" in prompt


def test_guidance_never_recommends_assertions() -> None:
    for name in ("react", "vue", "svelte", "generic", "unknown", None):
        prompt = build_system_prompt(name)

        assert "expect(" not in prompt
        assert "assertion" not in prompt.split("Framework-specific selector guidance:")[-1]


def test_detect_framework_from_dom_diff_file() -> None:
    result = detect_framework(
        "tests/login.spec.ts",
        "await page.getByRole('button').click()",
        [{"file": "src/components/LoginForm.svelte"}],
    )

    assert result == "svelte"


def test_detect_framework_avoids_react_substring_collision() -> None:
    result = detect_framework(
        "tests/form.spec.ts",
        "await page.locator('#submit').click()",
        [{"file": "src/components/reactive-form.vue"}],
    )

    assert result == "vue"


def test_detect_framework_ignores_plain_english_next() -> None:
    result = detect_framework(
        "tests/wizard.spec.ts",
        "// click the next button after the next step loads",
        [{"file": "src/pages/Wizard.vue"}],
    )

    assert result == "vue"


def test_detect_framework_from_nearby_package_json(tmp_path: Path) -> None:
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
