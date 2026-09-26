import pytest

from app.verify.ast_lock import AstLockAllowlist, check_ast_lock


@pytest.mark.parametrize(
    ("original", "patched"),
    [
        (
            'await page.getByTestId("old-submit").click();',
            'await page.getByTestId("new-submit").click();',
        ),
        (
            'await page.getByRole("button", { name: "Save" }).click();',
            'await page.getByRole("button", { name: "Submit" }).click();',
        ),
        (
            "await page.locator(`article-${user.id}`).click();",
            "await page.locator(`card-${user.id}`).click();",
        ),
        (
            'await page.locator("#submit").click({ timeout: 1_000 });',
            'await page.locator("#submit").click({ timeout: 5_000 });',
        ),
        (
            "await page.waitForTimeout(500);",
            "await page.waitForLoadState(500);",
        ),
    ],
)
def test_allows_explicitly_safe_ast_changes(original: str, patched: str) -> None:
    verdict = check_ast_lock(original, patched)

    assert verdict.allowed is True
    assert verdict.reason == "structurally_equivalent"


@pytest.mark.parametrize(
    ("original", "patched"),
    [
        ("expect(value).toBe(true);", "expect(value).toBe(false);"),
        ("assert.equal(actual, 1);", "assert.equal(actual, 2);"),
        ("assert.ok(value);", "assert.ok(other);"),
        ("expect.soft(value).toMatch(/ok/);", "expect.soft(value).toContain('ok');"),
        ("expect.poll(read).toBe(1);", "expect.poll(read).toBe(2);"),
        (
            "await expect(load()).resolves.toEqual('ok');",
            "await expect(load()).rejects.toThrow();",
        ),
        (
            'const check = expect; check(page.locator("#x")).toBeVisible();',
            'const check = expect; check(page.locator("#x")).toBeHidden();',
        ),
        ("if (ready) run();", "if (forced) run();"),
        ("await page.click('#save');", "page.click('#save');"),
        ("const check = expect; check(value);", "const check = assert; check(value);"),
        ("await page.click('#save');", "await page.click('#save'); cleanup();"),
        (
            "await page.locator(`article-${user.id}`).click();",
            "await page.locator(`article-${admin.id}`).click();",
        ),
        (
            'await page.locator(report("old-selector")).click();',
            'await page.locator(report("new-selector")).click();',
        ),
        (
            'await page.locator(() => "old-selector").click();',
            'await page.locator(() => "new-selector").click();',
        ),
        (
            'await page.locator(function () { return "old-selector"; }).click();',
            'await page.locator(function () { return "new-selector"; }).click();',
        ),
        (
            'await page.locator("#save").click({ force: false });',
            'await page.locator("#save").click({ force: true });',
        ),
    ],
)
def test_rejects_assertion_and_control_flow_changes(original: str, patched: str) -> None:
    verdict = check_ast_lock(original, patched)

    assert verdict.allowed is False
    assert verdict.reason == "disallowed_ast_change"
    assert verdict.node_kind is not None
    assert verdict.line is not None


@pytest.mark.parametrize(
    ("original", "patched", "reason"),
    [
        ("if (", "await page.locator('#save').click();", "original_parse_error"),
        ("await page.locator('#save').click();", "if (", "patched_parse_error"),
    ],
)
def test_rejects_parse_errors_without_fallback(original: str, patched: str, reason: str) -> None:
    verdict = check_ast_lock(original, patched)

    assert verdict.allowed is False
    assert verdict.reason == reason
    assert verdict.node_kind in {"ERROR", "("}


def test_allowlist_is_data() -> None:
    allowlist = AstLockAllowlist(
        locator_methods=frozenset({"findByAutomationId"}),
        timeout_keys=frozenset(),
        wait_methods=frozenset(),
    )

    verdict = check_ast_lock(
        'await page.findByAutomationId("old").click();',
        'await page.findByAutomationId("new").click();',
        allowlist,
    )

    assert verdict.allowed is True
