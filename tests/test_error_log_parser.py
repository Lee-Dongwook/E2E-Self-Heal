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
