import pytest
from langgraph.graph import END

import app.nodes.patch_generator as patch_node
from app.graph import route_after_patch
from app.nodes.patch_generator import PatchApplicationError, _apply, _mask_js_non_code
from app.schemas import PatchInstruction, PatchOutput
from app.state import AgentState


def _instruction(
    line: int,
    original: str,
    replacement: str,
    selector: str = "#new",
) -> PatchInstruction:
    return PatchInstruction(
        line=line,
        original=original,
        replacement=replacement,
        reason="test",
        selector=selector,
    )


def _state() -> AgentState:
    return {
        "test_script_path": "test.spec.ts",
        "original_code": "await page.click('#old')\n",
        "current_code": "await page.click('#old')\n",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "selector changed",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }


def test_replaces_target_line_only() -> None:
    code = "await page.click('#first')\nawait page.click('#old')\n"
    result = _apply(
        code,
        [_instruction(2, "await page.click('#old')", "await page.click('#new')")],
    )
    assert result == "await page.click('#first')\nawait page.click('#new')\n"


def test_preserves_trailing_newline_state() -> None:
    code = "await page.click('#old')"  # no trailing newline
    result = _apply(
        code,
        [_instruction(1, "await page.click('#old')", "await page.click('#new')")],
    )
    assert result == "await page.click('#new')"


def test_preserves_crlf_line_endings() -> None:
    code = "await page.click('#old')\r\n"
    result = _apply(
        code,
        [_instruction(1, "await page.click('#old')", "await page.click('#new')")],
    )
    assert result == "await page.click('#new')\r\n"


def test_rejects_out_of_range_line() -> None:
    with pytest.raises(PatchApplicationError, match="outside the current file"):
        _apply(
            "await page.click('#old')\n",
            [_instruction(99, "await page.click('#old')", "await page.click('#new')")],
        )


def test_rejects_mismatched_original_line() -> None:
    with pytest.raises(PatchApplicationError, match="no longer matches"):
        _apply(
            "await page.click('#actual')\n",
            [_instruction(1, "await page.click('#stale')", "await page.click('#new')")],
        )


def test_rejects_duplicate_line_targets() -> None:
    instructions = [
        _instruction(1, "await page.click('#old')", "await page.click('#first')"),
        _instruction(1, "await page.click('#old')", "await page.click('#second')"),
    ]
    with pytest.raises(PatchApplicationError, match="targeted more than once"):
        _apply("await page.click('#old')\n", instructions)


def test_rejects_entire_patch_set_when_one_instruction_is_stale() -> None:
    instructions = [
        _instruction(1, "await page.click('#one')", "await page.click('#new-one')"),
        _instruction(2, "await page.click('#stale')", "await page.click('#new-two')"),
    ]
    with pytest.raises(PatchApplicationError, match="no longer matches"):
        _apply("await page.click('#one')\nawait page.click('#two')\n", instructions)


def test_empty_instructions_is_noop() -> None:
    code = "x\ny\n"
    assert _apply(code, []) == code


def test_rejects_multiline_replacement() -> None:
    instruction = _instruction(
        1,
        "await page.click('#old')",
        "await page.click('#new')\nawait page.goto('/admin')",
    )
    with pytest.raises(PatchApplicationError, match="exactly one line"):
        _apply("await page.click('#old')\n", [instruction])


def test_rejects_assertion_edit() -> None:
    instruction = _instruction(
        1,
        "await expect(page.locator('#old')).toBeVisible()",
        "await expect(page.locator('#new')).toBeHidden()",
    )
    with pytest.raises(PatchApplicationError, match="targets an assertion"):
        _apply("await expect(page.locator('#old')).toBeVisible()\n", [instruction])


@pytest.mark.parametrize(
    "assertion",
    [
        "assert.equal(actual, expected)",
        "assert.ok(value)",
        "assert.deepStrictEqual(actual, expected)",
        "expect.soft(value).toBeVisible()",
        "expect.poll(check).toBe(true)",
        "expect(value).toMatch(/text/)",
        "expect(value).toContain('text')",
        "expect(value).toThrow()",
        "expect(value).resolves.toEqual(expected)",
        "expect(value).rejects.toThrow()",
    ],
)
def test_rejects_known_assertion_forms(assertion: str) -> None:
    instruction = _instruction(1, assertion, assertion.replace("value", "other"))

    with pytest.raises(PatchApplicationError, match="targets an assertion"):
        _apply(f"{assertion}\n", [instruction])


def test_masks_assertion_tokens_in_comments_and_strings() -> None:
    comment = _instruction(
        1,
        "await page.locator('#old').click() // expect.soft(value).toMatch('text')",
        "await page.locator('#new').click() // expect.soft(value).toMatch('text')",
    )
    string = _instruction(
        1,
        'await page.fill("#old", "assert.deepStrictEqual(actual, expected)")',
        'await page.fill("#new", "assert.deepStrictEqual(actual, expected)")',
    )

    assert _apply(comment.original + "\n", [comment]) == comment.replacement + "\n"
    assert _apply(string.original + "\n", [string]) == string.replacement + "\n"


def test_rejects_unrelated_code_edit() -> None:
    instruction = _instruction(1, "const retries = 3;", "const retries = 4;", selector="")
    with pytest.raises(PatchApplicationError, match="not limited to a locator or wait"):
        _apply("const retries = 3;\n", [instruction])


def test_allows_wait_condition_edit() -> None:
    instruction = _instruction(
        1,
        "await page.waitForSelector('#old')",
        "await page.waitForSelector('#new')",
        selector="#new",
    )
    assert _apply("await page.waitForSelector('#old')\n", [instruction]) == (
        "await page.waitForSelector('#new')\n"
    )


def test_allows_selector_only_fill_edit() -> None:
    instruction = _instruction(
        1,
        "await page.fill('#old-email', 'user@example.com')",
        "await page.fill('#new-email', 'user@example.com')",
    )

    assert _apply("await page.fill('#old-email', 'user@example.com')\n", [instruction]) == (
        "await page.fill('#new-email', 'user@example.com')\n"
    )


def test_rejects_fill_value_edit() -> None:
    instruction = _instruction(
        1,
        "await page.fill('#email', 'user@example.com')",
        "await page.fill('#email', 'admin@example.com')",
    )

    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.fill('#email', 'user@example.com')\n", [instruction])


def test_rejects_fill_value_edit_when_value_reads_changed_test_id() -> None:
    instruction = _instruction(
        1,
        "await page.fill('#email', await page.getByTestId('old-name').textContent())",
        "await page.fill('#email', await page.getByTestId('new-name').textContent())",
    )

    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply(
            "await page.fill('#email', await page.getByTestId('old-name').textContent())\n",
            [instruction],
        )


def test_rejects_locator_fill_value_edit() -> None:
    instruction = _instruction(
        1,
        "await page.locator('#email').fill('user@example.com')",
        "await page.locator('#email').fill('admin@example.com')",
    )

    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.locator('#email').fill('user@example.com')\n", [instruction])


def test_allows_locator_selector_edit_without_changing_fill_value() -> None:
    instruction = _instruction(
        1,
        "await page.locator('#old-email').fill('user@example.com')",
        "await page.locator('#new-email').fill('user@example.com')",
    )

    assert (
        _apply("await page.locator('#old-email').fill('user@example.com')\n", [instruction])
        == "await page.locator('#new-email').fill('user@example.com')\n"
    )


def test_rejects_click_force_option_edit() -> None:
    instruction = _instruction(
        1,
        "await page.click('#a', { force: true })",
        "await page.click('#a', { force: false })",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.click('#a', { force: true })\n", [instruction])


def test_rejects_click_trial_option_edit() -> None:
    instruction = _instruction(
        1,
        "await page.click('#a', { trial: true })",
        "await page.click('#a', { trial: false })",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.click('#a', { trial: true })\n", [instruction])


def test_rejects_dblclick_delay_option_edit() -> None:
    instruction = _instruction(
        1,
        "await page.dblclick('#a', { delay: 100 })",
        "await page.dblclick('#a', { delay: 500 })",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.dblclick('#a', { delay: 100 })\n", [instruction])


def test_rejects_check_no_wait_after_option_edit() -> None:
    instruction = _instruction(
        1,
        "await page.check('#a', { noWaitAfter: true })",
        "await page.check('#a', { noWaitAfter: false })",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.check('#a', { noWaitAfter: true })\n", [instruction])


def test_rejects_locator_bound_click_force_option_edit() -> None:
    instruction = _instruction(
        1,
        "await page.getByRole('button').click({ force: true })",
        "await page.getByRole('button').click({ force: false })",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.getByRole('button').click({ force: true })\n", [instruction])


def test_rejects_hover_position_option_edit() -> None:
    instruction = _instruction(
        1,
        "await page.hover('#a', { position: { x: 1, y: 2 } })",
        "await page.hover('#a', { position: { x: 3, y: 4 } })",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.hover('#a', { position: { x: 1, y: 2 } })\n", [instruction])


def test_allows_click_selector_only_edit() -> None:
    instruction = _instruction(
        1,
        "await page.click('#old', { force: true })",
        "await page.click('#new', { force: true })",
    )
    assert _apply("await page.click('#old', { force: true })\n", [instruction]) == (
        "await page.click('#new', { force: true })\n"
    )


def test_allows_locator_selector_edit_on_click() -> None:
    instruction = _instruction(
        1,
        "await page.getByRole('button', { name: 'Old' }).click()",
        "await page.getByRole('button', { name: 'New' }).click()",
    )
    assert (
        _apply("await page.getByRole('button', { name: 'Old' }).click()\n", [instruction])
        == "await page.getByRole('button', { name: 'New' }).click()\n"
    )


def test_ignores_action_call_in_line_comment() -> None:
    instruction = _instruction(
        1,
        "await page.click('#old') // page.click('#x",
        "await page.click('#new') // page.click('#x",
    )
    assert _apply("await page.click('#old') // page.click('#x\n", [instruction]) == (
        "await page.click('#new') // page.click('#x\n"
    )


def test_ignores_action_call_in_block_comment() -> None:
    instruction = _instruction(
        1,
        "await page.click('#old') /* page.click('#x */",
        "await page.click('#new') /* page.click('#x */",
    )
    assert _apply("await page.click('#old') /* page.click('#x */\n", [instruction]) == (
        "await page.click('#new') /* page.click('#x */\n"
    )


def test_rejects_value_edit_when_value_string_mentions_action() -> None:
    instruction = _instruction(
        1,
        "await page.fill('#msg', 'page.click(\"#x\")')",
        "await page.fill('#msg', 'page.click(\"#y\")')",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.fill('#msg', 'page.click(\"#x\")')\n", [instruction])


def test_rejects_value_edit_when_value_template_mentions_action() -> None:
    instruction = _instruction(
        1,
        "await page.fill('#msg', `page.click(\"#x\")`)",
        "await page.fill('#msg', `page.click(\"#y\")`)",
    )
    with pytest.raises(PatchApplicationError, match="argument other than the selector"):
        _apply("await page.fill('#msg', `page.click(\"#x\")`)\n", [instruction])


def test_allows_selector_edit_when_value_mentions_action() -> None:
    instruction = _instruction(
        1,
        "await page.fill('#old', 'page.click(\"#x\")')",
        "await page.fill('#new', 'page.click(\"#x\")')",
    )
    assert _apply("await page.fill('#old', 'page.click(\"#x\")')\n", [instruction]) == (
        "await page.fill('#new', 'page.click(\"#x\")')\n"
    )


def test_rejects_edit_on_line_with_only_commented_locator() -> None:
    instruction = _instruction(
        1,
        "const retries = 3; // page.getByRole('button')",
        "const retries = 99; // page.getByRole('button')",
        selector="",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply("const retries = 3; // page.getByRole('button')\n", [instruction])


def test_rejects_edit_on_line_with_only_string_locator() -> None:
    instruction = _instruction(
        1,
        "const sel = \"page.getByRole('button')\";",
        "const sel = \"page.getByRole('button2')\";",
        selector="",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply("const sel = \"page.getByRole('button')\";\n", [instruction])


def test_rejects_edit_on_line_with_only_template_locator() -> None:
    instruction = _instruction(
        1,
        "const sel = `page.getByRole('button')`;",
        "const sel = `page.getByRole('button2')`;",
        selector="",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply("const sel = `page.getByRole('button')`;\n", [instruction])


def test_allows_selector_edit_with_assertion_token_in_comment() -> None:
    instruction = _instruction(
        1,
        "await page.click('#old') // expect(false)",
        "await page.click('#new') // expect(false)",
    )
    assert _apply("await page.click('#old') // expect(false)\n", [instruction]) == (
        "await page.click('#new') // expect(false)\n"
    )


def test_rejects_edit_on_line_continuing_block_comment() -> None:
    # The locator token looks like real code to a line-local mask, but the line is inside
    # a block comment opened on an earlier line — it must not be a patchable target.
    code = "/* multi-line comment\npage.getByRole('button')\n*/\n"
    instruction = _instruction(
        2,
        "page.getByRole('button')",
        "page.getByRole('other')",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply(code, [instruction])


def test_rejects_edit_on_line_continuing_template_literal() -> None:
    code = "const tpl = `multi-line template\npage.getByRole('button')\n`;\n"
    instruction = _instruction(
        2,
        "page.getByRole('button')",
        "page.getByRole('other')",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply(code, [instruction])


def test_allows_real_locator_line_after_block_comment() -> None:
    # A genuine locator line that FOLLOWS a closed block comment still gates normally.
    code = "/* header */\nawait page.click('#old')\n"
    instruction = _instruction(2, "await page.click('#old')", "await page.click('#new')")
    assert _apply(code, [instruction]) == "/* header */\nawait page.click('#new')\n"


# --- _mask_js_non_code state-machine unit tests -------------------------


def test_mask_handles_nested_template_interpolation() -> None:
    # `` `a${`b`}c` `` — the inner backtick is inside ${} interpolation and must not
    # terminate the outer template.
    source = "`a${`b`}c`"
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_handles_strings_and_braces_in_template_expression() -> None:
    # A } inside a string inside ${...} must not close the expression.
    source = '`a${"}"}b`'
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_keeps_template_expressions_blanked() -> None:
    # Conservative: interpolation is blanked with the literal segments, so a call inside
    # ${...} is not visible to the action-call regexes.
    source = "`x${page.click('#a')}`"
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_handles_backslash_escapes() -> None:
    source = r"'a\'b'"
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_handles_unterminated_constructs() -> None:
    for source in ("/* unterminated", "'unterminated", "`unterminated"):
        assert _mask_js_non_code(source) == " " * len(source)


def test_mask_line_comment_preserves_newline() -> None:
    assert _mask_js_non_code("// note\ncode") == " " * len("// note") + "\ncode"


def test_mask_block_comment_swallows_newlines() -> None:
    assert _mask_js_non_code("/* a\nb */") == " " * len("/* a\nb */")


def test_mask_handles_regex_in_template_expression() -> None:
    # A } inside a regex literal must not close the ${...} expression early (CodeRabbit):
    # under the previous scanner the inner backtick after the regex leaked as code.
    source = "`${/}/.test(x) ? `page.getByRole('a')` : `page.getByRole('b')`}`"
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_handles_regex_char_class_in_template_expression() -> None:
    source = "`${/[/}]/.test(x)}`"
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_handles_escaped_brace_in_regex_expression() -> None:
    source = r"`${/\}/.test(x)}`"
    assert _mask_js_non_code(source) == " " * len(source)


def test_mask_division_in_expression_keeps_following_code() -> None:
    # a / b inside ${...} is division, not a regex: the expression must still close and
    # code after the template must stay visible.
    source = "`${a / b}` + page.click('#x')"
    masked = _mask_js_non_code(source)
    assert masked.startswith(" " * len("`${a / b}`"))
    assert masked.endswith("+ page.click(" + " " * len("'#x'") + ")")


def test_rejects_edit_on_line_with_regex_template_locator() -> None:
    # A locator that appears only inside a nested template whose ${...} holds a regex must
    # not pass the scope gate — the regex's } would otherwise close the expression early.
    code = "const sel = `${/}/.test(x) ? `page.getByRole('button')` : `nope`}`;\n"
    instruction = _instruction(
        1,
        "const sel = `${/}/.test(x) ? `page.getByRole('button')` : `nope`}`;",
        "const sel = `${/}/.test(x) ? `page.getByRole('other')` : `nope`}`;",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply(code, [instruction])


def test_rejects_edit_on_line_with_only_nested_template_locator() -> None:
    # A locator that appears only inside a nested template interpolation must not pass the
    # scope gate — under the old scanner the inner backtick ended the template early and
    # the locator leaked as real code.
    code = "const sel = `outer${`page.getByRole('button')`}`;\n"
    instruction = _instruction(
        1,
        "const sel = `outer${`page.getByRole('button')`}`;",
        "const sel = `outer${`page.getByRole('other')`}`;",
    )
    with pytest.raises(PatchApplicationError, match="not limited to a locator"):
        _apply(code, [instruction])


def test_patch_generator_returns_rejection_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = PatchOutput(
        instructions=[_instruction(1, "await page.click('#stale')", "await page.click('#new')")],
    )
    monkeypatch.setattr(patch_node, "generate_patch", lambda system, user: output)

    state = _state()
    result = patch_node.patch_generator(state)

    assert result["current_code"] == state["current_code"]
    assert result["patch_instructions"] == {}
    assert result["patch_application_report"]["ok"] is False
    assert result["loop_count"] == 1
    assert "[PATCH APPLICATION FEEDBACK]" in result["analysis_report"]

    state["patch_application_report"] = result["patch_application_report"]
    state["loop_count"] = result["loop_count"]
    assert route_after_patch(state) == "patch_generator"


def test_patch_generator_reports_success_and_routes_to_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = PatchOutput(
        instructions=[_instruction(1, "await page.click('#old')", "await page.click('#new')")],
    )
    monkeypatch.setattr(patch_node, "generate_patch", lambda system, user: output)

    state = _state()
    result = patch_node.patch_generator(state)

    assert result["current_code"] == "await page.click('#new')\n"
    assert result["patch_application_report"] == {"ok": True}

    state["boundary_report"] = result["boundary_report"]
    state["patch_application_report"] = result["patch_application_report"]
    assert route_after_patch(state) == "shadow_verifier"


def test_generation_failure_clears_previous_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_generation(system: str, user: str) -> PatchOutput:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(patch_node, "generate_patch", fail_generation)
    state = _state()
    state["patch_application_report"] = {"ok": False, "error": "stale rejection"}

    result = patch_node.patch_generator(state)

    assert result["current_code"] == state["current_code"]
    assert result["patch_application_report"] == {"ok": True}

    state["patch_application_report"] = result["patch_application_report"]
    assert route_after_patch(state) == "shadow_verifier"


def test_boundary_violation_ends_immediately() -> None:
    # A boundary violation is permanent (the target path can't change mid-run), so the
    # router must end rather than loop back and burn the loop budget on a dead condition.
    state = _state()
    state["boundary_report"] = {"ok": False, "error": "outside architecture boundary"}
    state["patch_application_report"] = {"ok": True}
    assert route_after_patch(state) == END
    # Ends even well below the loop cap.
    state["loop_count"] = 0
    assert route_after_patch(state) == END
