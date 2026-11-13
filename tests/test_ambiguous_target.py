"""The diff check that refuses a test whose element became several look-alikes (Issue #388)."""

import pytest

from app.preprocess.ambiguous_target import ambiguous_replacements

CLICK_TIMEOUT = """    TimeoutError: page.click: Timeout 3000ms exceeded.
    Call log:
      - waiting for locator('#submit-btn')
"""

FILE = "src/SubmitButton.tsx"
SUBMIT = {"tag": "button", "attributes": {"id": "submit-btn", "className": "btn", "type": "submit"}}


def _removed(element: dict) -> dict:
    return {"file": FILE, "line": 0, "previous": element, "current": {}}


def _added(element: dict, line: int, file: str = FILE) -> dict:
    return {"file": file, "line": line, "previous": {}, "current": element}


def _button(id_: str, type_: str = "submit", tag: str = "button") -> dict:
    return {"tag": tag, "attributes": {"id": id_, "className": "btn", "type": type_}}


@pytest.mark.parametrize(
    "context",
    [
        [
            _removed(SUBMIT),
            _added(_button("save-draft"), 4),
            _added(_button("publish"), 7),
        ],
        [
            {"file": FILE, "line": 4, "previous": SUBMIT, "current": _button("save-draft")},
            _added(_button("publish"), 7),
        ],
    ],
    ids=["removed-and-added", "paired-change"],
)
def test_two_replacements_are_ambiguous(context: list[dict]) -> None:
    assert ambiguous_replacements(CLICK_TIMEOUT, context) == ["#save-draft", "#publish"]


def test_a_single_replacement_is_a_rename_not_an_ambiguity() -> None:
    context = [_removed(SUBMIT), _added(_button("submit"), 4)]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == []


def test_additions_of_another_kind_are_not_look_alikes() -> None:
    # A rename next to a new reset button and a new link: only one submit button remains.
    context = [
        _removed(SUBMIT),
        _added(_button("submit"), 4),
        _added(_button("reset", type_="reset"), 7),
        _added(_button("help", tag="a"), 9),
    ]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == []


def test_look_alikes_in_another_file_do_not_count() -> None:
    context = [
        _removed(SUBMIT),
        _added(_button("save-draft"), 4),
        _added(_button("publish"), 7, file="src/Other.tsx"),
    ]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == []


def test_an_element_that_keeps_the_old_name_is_not_removed() -> None:
    # The failing element is still there; a second submit button was merely added beside it.
    context = [
        {"file": FILE, "line": 4, "previous": SUBMIT, "current": {**SUBMIT, "tag": "button"}},
        _added(_button("save-draft"), 7),
        _added(_button("publish"), 9),
    ]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == []


def test_a_locator_that_does_not_name_the_removed_element_does_not_fire() -> None:
    other = "      - waiting for locator('#email')\n"
    context = [_removed(SUBMIT), _added(_button("save-draft"), 4), _added(_button("publish"), 7)]

    assert ambiguous_replacements(other, context) == []


def test_role_and_text_locators_are_left_to_the_repair_loop() -> None:
    by_role = "      - waiting for getByRole('button', { name: 'Submit' })\n"
    context = [_removed(SUBMIT), _added(_button("save-draft"), 4), _added(_button("publish"), 7)]

    assert ambiguous_replacements(by_role, context) == []


def test_a_selector_hint_settles_the_ambiguity() -> None:
    hint = {
        "type": "selector_hint",
        "hint_type": "css",
        "value": "#publish",
        "original": "#submit-btn",
        "confidence": 1.0,
        "priority": "high",
    }
    context = [
        _removed(SUBMIT),
        _added(_button("save-draft"), 4),
        _added(_button("publish"), 7),
        hint,
    ]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == []


def test_look_alikes_without_an_id_are_named_by_test_id_or_line() -> None:
    by_test_id = {"tag": "button", "attributes": {"data-testid": "draft", "type": "submit"}}
    bare = {"tag": "button", "attributes": {"type": "submit"}}
    context = [_removed(SUBMIT), _added(by_test_id, 4), _added(bare, 7)]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == [
        '[data-testid="draft"]',
        "<button> at line 7",
    ]


def test_no_locator_in_the_log_reports_nothing() -> None:
    navigation = "Error: page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:47/\n"
    context = [_removed(SUBMIT), _added(_button("save-draft"), 4), _added(_button("publish"), 7)]

    assert ambiguous_replacements(navigation, context) == []


# --- #458: strict-mode violations, accessible names, submit-like kinds ---------------------

# Captured from a real Playwright run on the demo app with a second "Submit" button added.
STRICT_MODE = """    Error: locator.click: Error: strict mode violation: getByRole('button', { name: 'Submit' }) resolved to 2 elements:
        1) <button class="btn" type="submit" id="submit-btn">Submit</button> aka locator('#submit-btn')
        2) <button class="btn" type="submit" id="submit-footer">Submit</button> aka locator('#submit-footer')
    Call log:
      - waiting for getByRole('button', { name: 'Submit' })
"""


def test_a_strict_mode_violation_is_ambiguous_for_a_role_locator_with_no_diff() -> None:
    # Playwright's own evidence; the text-only locator never matches the diff check.
    assert ambiguous_replacements(STRICT_MODE, []) == ["#submit-btn", "#submit-footer"]


def test_strict_mode_candidates_keep_playwrights_locator_when_it_is_not_a_plain_selector() -> None:
    log = (
        "Error: strict mode violation: getByText('Save') resolved to 3 elements:\n"
        "    1) <button>Save</button> aka getByRole('button', { name: 'Save', exact: true })\n"
        "    2) <a href=\"/save\">Save</a> aka getByRole('link', { name: 'Save' })\n"
        "    3) <span>Save</span>\n"
        "Call log:\n"
    )

    assert ambiguous_replacements(log, []) == [
        "getByRole('button', { name: 'Save', exact: true })",
        "getByRole('link', { name: 'Save' })",
        "<span>Save</span>",
    ]


def test_a_strict_mode_violation_without_listed_matches_still_refuses() -> None:
    log = "Error: strict mode violation: locator('.btn') resolved to 4 elements:\nCall log:\n"

    assert ambiguous_replacements(log, []) == ["locator('.btn')"]


def test_a_selector_hint_settles_a_strict_mode_violation_too() -> None:
    hint = {"type": "selector_hint", "selector": "#submit-btn"}

    assert ambiguous_replacements(STRICT_MODE, [hint]) == []


def test_a_timeout_is_not_a_strict_mode_violation() -> None:
    assert ambiguous_replacements(CLICK_TIMEOUT, []) == []


@pytest.mark.parametrize(
    ("log", "attribute", "value"),
    [
        ("getByRole('button', { name: 'Submit order' })", "aria-label", "Submit order"),
        ("getByLabel('Email')", "aria-label", "Email"),
        ("getByPlaceholder('you@example.com')", "placeholder", "you@example.com"),
        ("getByTitle('Close dialog')", "title", "Close dialog"),
        ("getByAltText('Company logo')", "alt", "Company logo"),
    ],
)
def test_an_accessible_name_attribute_identifies_the_removed_element(
    log: str, attribute: str, value: str
) -> None:
    tag = "img" if attribute == "alt" else "button"
    old = {"tag": tag, "attributes": {attribute: value}}
    context = [
        _removed(old),
        _added({"tag": tag, "attributes": {attribute: "First"}}, 4),
        _added({"tag": tag, "attributes": {attribute: "Second"}}, 7),
    ]

    assert ambiguous_replacements(f"      - waiting for {log}\n", context) == [
        "<" + tag + "> at line 4",
        "<" + tag + "> at line 7",
    ]


def test_an_accessible_name_kept_by_one_element_is_a_rename_not_an_ambiguity() -> None:
    by_label = "      - waiting for getByLabel('Email')\n"
    old = {"tag": "input", "attributes": {"aria-label": "Email", "id": "email"}}
    context = [
        _removed(old),
        _added({"tag": "input", "attributes": {"aria-label": "Email", "id": "work-email"}}, 4),
        _added({"tag": "input", "attributes": {"aria-label": "Phone", "id": "phone"}}, 7),
    ]

    assert ambiguous_replacements(by_label, context) == []


def test_a_submit_button_and_a_submit_input_are_look_alikes() -> None:
    draft = {"tag": "input", "attributes": {"id": "save-draft", "type": "submit"}}
    context = [_removed(SUBMIT), _added(draft, 4), _added(_button("publish"), 7)]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == ["#save-draft", "#publish"]


def test_an_input_without_a_type_is_a_text_input() -> None:
    email = "      - waiting for locator('#email')\n"
    old = {"tag": "input", "attributes": {"id": "email", "type": "text"}}
    context = [
        _removed(old),
        _added({"tag": "input", "attributes": {"id": "work-email"}}, 4),
        _added({"tag": "input", "attributes": {"id": "home-email", "type": "text"}}, 7),
    ]

    assert ambiguous_replacements(email, context) == ["#work-email", "#home-email"]


def test_a_rename_beside_a_button_without_a_type_is_still_a_rename() -> None:
    # A <button> with no type is left as its own kind: React code often omits it on plain
    # buttons, so counting it as a submit control would refuse ordinary renames.
    close = {"tag": "button", "attributes": {"id": "close"}}
    context = [_removed(SUBMIT), _added(_button("submit"), 4), _added(close, 7)]

    assert ambiguous_replacements(CLICK_TIMEOUT, context) == []
