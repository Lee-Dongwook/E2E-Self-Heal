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
