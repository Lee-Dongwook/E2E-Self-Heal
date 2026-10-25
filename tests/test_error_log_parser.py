import pytest

from app.preprocess.error_log_parser import parse_error_log

SAMPLE_LOG = """
Running 1 test using 1 worker
  1) [chromium] › tests/example.spec.ts:10:15 › submit form
    Error: locator.click: Timeout 5000ms exceeded.
    Call log:
      - waiting for locator('#submit-btn')
      - waiting for element to be visible
    at tests/example.spec.ts:12:9
"""


def test_extracts_core_failure_signals() -> None:
    result = parse_error_log(SAMPLE_LOG)
    assert "Error: locator.click: Timeout 5000ms exceeded." in result
    assert "at tests/example.spec.ts:12" in result
    assert "locator('#submit-btn')" in result


def test_falls_back_to_tail_when_no_match() -> None:
    raw_log = "some unstructured output with no markers"
    assert parse_error_log(raw_log) == raw_log


@pytest.mark.parametrize("raw_log", [None, ""])
def test_returns_empty_for_missing_log(raw_log: str | None) -> None:
    assert parse_error_log(raw_log) == ""


STRICT_MODE_LOG = """
Error: locator.click: Error: strict mode violation: locator('button') resolved to 2 elements
Call log:
  - waiting for locator('button')
  - waiting for element to be visible
at tests/checkout.spec.ts:44:7
"""


def test_strict_mode_violation() -> None:
    result = parse_error_log(STRICT_MODE_LOG)
    assert "strict mode violation" in result
    assert "at tests/checkout.spec.ts:44" in result
    assert "locator('button')" in result


ASSERTION_LOG = """
Error: expect(locator).toBeVisible() failed
Locator: getByRole('heading', { name: 'Dashboard' })
Expected: visible
Received: hidden
Call log:
  - waiting for getByRole('heading', { name: 'Dashboard' })
at e2e/dashboard.spec.ts:18:5
"""


def test_assertion_failure_with_get_by_role() -> None:
    result = parse_error_log(ASSERTION_LOG)
    assert "expect(locator).toBeVisible()" in result
    assert "at e2e/dashboard.spec.ts:18" in result
    assert "Locator: getByRole('heading', { name: 'Dashboard' })" in result
    assert "Expected: visible" in result
    assert "Received: hidden" in result


TEXT_ASSERTION_LOG = """
Error: expect(locator).toHaveText() failed
Locator: getByTestId('total')
Expected string: "$42.00"
Received string: "$0.00"
at tests/cart.spec.ts:31:7
"""


def test_assertion_captures_string_variant_expected_received() -> None:
    result = parse_error_log(TEXT_ASSERTION_LOG)
    assert 'Expected string: "$42.00"' in result
    assert 'Received string: "$0.00"' in result
    assert "Locator: getByTestId('total')" in result


STRICT_MODE_CALL_LOG = """
Error: locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('.item')
  - locator resolved to 3 elements
at tests/list.spec.ts:20:5
"""


def test_strict_mode_resolved_captured_from_call_log() -> None:
    result = parse_error_log(STRICT_MODE_CALL_LOG)
    assert "resolved to 3 elements" in result
    assert "at tests/list.spec.ts:20" in result


def test_strict_mode_resolved_not_duplicated_when_in_error_line() -> None:
    result = parse_error_log(STRICT_MODE_LOG)
    assert result.count("resolved to 2 elements") == 1


def test_long_received_line_is_bounded() -> None:
    long_log = "Received: " + "<div>" * 500 + "\nat tests/x.spec.ts:1:1"
    result = parse_error_log(long_log)
    received_line = next(line for line in result.splitlines() if line.startswith("Received:"))
    assert len(received_line) <= 200
