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


def test_extracts_error_reason():
    result = parse_error_log(SAMPLE_LOG)
    assert "Error: locator.click: Timeout 5000ms exceeded." in result


def test_extracts_source_location():
    result = parse_error_log(SAMPLE_LOG)
    assert "at tests/example.spec.ts:12" in result


def test_extracts_call_log_locators():
    result = parse_error_log(SAMPLE_LOG)
    assert "locator('#submit-btn')" in result


def test_falls_back_to_tail_when_no_match():
    result = parse_error_log("some unstructured output with no markers")
    assert result  # never returns empty


def test_returns_empty_on_none():
    assert parse_error_log(None) == ""


def test_returns_empty_on_empty_string():
    assert parse_error_log("") == ""


STRICT_MODE_LOG = """
Error: locator.click: Error: strict mode violation: locator('button') resolved to 2 elements
Call log:
  - waiting for locator('button')
  - waiting for element to be visible
at tests/checkout.spec.ts:44:7
"""


def test_strict_mode_violation():
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


def test_assertion_failure_with_get_by_role():
    result = parse_error_log(ASSERTION_LOG)
    assert "expect(locator).toBeVisible()" in result
    assert "at e2e/dashboard.spec.ts:18" in result
    assert "getByRole('heading'" in result


GET_BY_TEXT_LOG = """
Error: locator.fill: Timeout 30000ms exceeded.
Call log:
  - waiting for getByText('Sign in')
  - waiting for element to be enabled
at tests/auth/login.spec.ts:9:11
"""


def test_get_by_text_locator():
    result = parse_error_log(GET_BY_TEXT_LOG)
    assert "Timeout 30000ms exceeded" in result
    assert "getByText('Sign in')" in result
    assert "at tests/auth/login.spec.ts:9" in result
