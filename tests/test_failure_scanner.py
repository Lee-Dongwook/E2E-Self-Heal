from app.preprocess.failure_scanner import scan_failing_tests

SUITE_LOG = """
Running 3 tests using 1 worker

  ✓  1 tests/ok.spec.ts:3:1 › passes (1s)
  ✘  2 tests/login.spec.ts:12:3 › user can log in (3s)
  ✘  3 example.spec.ts:7:5 › submits the form (3s)


  1) [chromium] › tests/login.spec.ts:12:3 › user can log in ─────────────────────
     TimeoutError: locator.click: Timeout 3000ms exceeded.
  2) example.spec.ts:7:5 › submits the form ─────────────────────
     TimeoutError: locator.click: Timeout 3000ms exceeded.
  3) tests/login.spec.ts:20:5 › user can log out ─────────────────────
     TimeoutError: locator.click: Timeout 3000ms exceeded.

  3 failed
"""


def test_scans_distinct_failing_files_in_order():
    # login.spec.ts fails twice (1 and 3) -> deduped; first-seen order preserved.
    assert scan_failing_tests(SUITE_LOG) == ["tests/login.spec.ts", "example.spec.ts"]


def test_ignores_the_checkmark_summary_lines():
    # The `✘  2 ...` summary lines must not be scanned — only numbered `N)` entries.
    assert "tests/ok.spec.ts" not in scan_failing_tests(SUITE_LOG)


def test_empty_when_no_failures():
    assert scan_failing_tests("Running 1 test\n  1 passed") == []
