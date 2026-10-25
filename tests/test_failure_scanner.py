import pytest

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


def test_scans_distinct_failing_files_in_order() -> None:
    assert scan_failing_tests(SUITE_LOG) == ["tests/login.spec.ts", "example.spec.ts"]


def test_empty_when_no_failures() -> None:
    assert scan_failing_tests("Running 1 test\n  1 passed") == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/my suite/login.spec.ts", "tests/my suite/login.spec.ts"),
        ("tests/@smoke/login.spec.ts", "tests/@smoke/login.spec.ts"),
        ("tests/100%/login.spec.ts", "tests/100%/login.spec.ts"),
    ],
    ids=["spaces", "at-sign", "percent"],
)
def test_failure_path_preserves_special_characters(path: str, expected: str) -> None:
    log = f"  1) [chromium] › {path}:12:3 › smoke test\n"
    assert scan_failing_tests(log) == [expected]
