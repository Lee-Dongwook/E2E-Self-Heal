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
        ("await page.click('#old');", "await page.click('#new');"),
        (
            "await page.fill('#old', 'user@example.com');",
            "await page.fill('#new', 'user@example.com');",
        ),
        ("await page.waitForSelector('#old');", "await page.waitForSelector('#new');"),
        ("await page.click(`#old-${id}`);", "await page.click(`#new-${id}`);"),
        ("await page.click(/* why */ '#old');", "await page.click(/* why */ '#new');"),
        # A timeout-only options object is a wait condition: it may be added or removed.
        ("await page.click('#a');", "await page.click('#a', { timeout: 10_000 });"),
        ("await page.click('#a', { timeout: 10_000 });", "await page.click('#a');"),
        (
            "await page.locator('#a').click();",
            "await page.locator('#a').click({ timeout: 10_000 });",
        ),
        ("await page.waitForURL('/done');", "await page.waitForURL('/done', { timeout: 9 });"),
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
        # Only the selector (first) argument of a ``page`` action is editable.
        (
            "await page.fill('#email', 'user@example.com');",
            "await page.fill('#email', 'admin@example.com');",
        ),
        ("await page.click('#a', { force: true });", "await page.click('#a', { force: false });"),
        ("await page.press('#a', 'Enter');", "await page.press('#a', 'Escape');"),
        # A non-``page`` receiver has no selector argument, so its arguments stay locked.
        ("await frame.click('#old');", "await frame.click('#new');"),
        (
            "await page.locator('#a').fill('user@example.com');",
            "await page.locator('#a').fill('admin@example.com');",
        ),
        # A computed selector is executable code, not a literal.
        ("await page.click(pick('#old'));", "await page.click(pick('#new'));"),
        ("await page.click(() => '#old');", "await page.click(() => '#new');"),
        # Only a trailing ``{ timeout: <number> }`` on an action or wait may be added.
        ("await page.click('#a');", "await page.click('#a', { force: true });"),
        ("await page.click('#a');", "await page.click('#a', { timeout: 1, force: true });"),
        ("await page.click('#a');", "await page.click('#a', { timeout: slow() });"),
        ("await page.click('#a');", "await page.click('#a', { timeout: 1 }, 2);"),
        ("await expect(x).toBeVisible();", "await expect(x).toBeVisible({ timeout: 9 });"),
        ("await submit('#a');", "await submit('#a', { timeout: 9 });"),
        (
            "await page.click('#a');",
            "await page.waitForSelector('#a', { timeout: 9 }); await page.click('#a');",
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


# Each case: (name, description, original, patched, expected_allowed)
AST_LOCK_CASES = [
    # ==================== REJECT CASES ====================
    (
        "001_expect_matcher_changed",
        "expect matcher changed",
        """import { test, expect } from '@playwright/test';

test('button visibility', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#submit')).toBeVisible();
});
""",
        """import { test, expect } from '@playwright/test';

test('button visibility', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#submit')).toBeHidden();
});
""",
        False,
    ),
    (
        "002_expect_argument_changed",
        "expect argument changed",
        """import { test, expect } from '@playwright/test';

test('button text', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#submit')).toHaveText('Submit');
});
""",
        """import { test, expect } from '@playwright/test';

test('button text', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#submit')).toHaveText('Send');
});
""",
        False,
    ),
    (
        "003_if_condition_altered",
        "if condition altered",
        """import { test } from '@playwright/test';

test('conditional click', async ({ page }) => {
  await page.goto('https://example.com');
  const isVisible = await page.locator('#button').isVisible();
  if (isVisible) {
    await page.locator('#button').click();
  }
});
""",
        """import { test } from '@playwright/test';

test('conditional click', async ({ page }) => {
  await page.goto('https://example.com');
  const isVisible = await page.locator('#button').isVisible();
  if (!isVisible) {
    await page.locator('#button').click();
  }
});
""",
        False,
    ),
    (
        "004_ternary_altered",
        "ternary altered",
        """import { test } from '@playwright/test';

test('ternary selector', async ({ page }) => {
  await page.goto('https://example.com');
  const selector = isMobile ? '#mobile-btn' : '#desktop-btn';
  await page.locator(selector).click();
});
""",
        """import { test } from '@playwright/test';

test('ternary selector', async ({ page }) => {
  await page.goto('https://example.com');
  const selector = isMobile ? '#mobile-button' : '#desktop-btn';
  await page.locator(selector).click();
});
""",
        False,
    ),
    (
        "005_await_removed",
        "await removed",
        """import { test } from '@playwright/test';

test('click button', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('#submit').click();
});
""",
        """import { test } from '@playwright/test';

test('click button', async ({ page }) => {
  await page.goto('https://example.com');
  page.locator('#submit').click();
});
""",
        False,
    ),
    (
        "006_statement_added",
        "statement added",
        """import { test } from '@playwright/test';

test('click and wait', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('#submit').click();
});
""",
        """import { test } from '@playwright/test';

test('click and wait', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('#submit').click();
  await page.waitForTimeout(1000);
});
""",
        False,
    ),
    (
        "007_statement_removed",
        "statement removed",
        """import { test } from '@playwright/test';

test('click and wait', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('#submit').click();
  await page.waitForTimeout(1000);
});
""",
        """import { test } from '@playwright/test';

test('click and wait', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('#submit').click();
});
""",
        False,
    ),
    (
        "008_alias_assertion",
        "alias assertion",
        """import { test, expect } from '@playwright/test';

const check = expect;

test('aliased assertion', async ({ page }) => {
  await page.goto('https://example.com');
  check(page.locator('#count')).toHaveText('5');
});
""",
        """import { test, expect } from '@playwright/test';

const check = expect;

test('aliased assertion', async ({ page }) => {
  await page.goto('https://example.com');
  check(page.locator('#count')).toHaveText('10');
});
""",
        False,
    ),
    (
        "009_assert_dotted_form",
        "assert.equal dotted form",
        """import { test } from '@playwright/test';
import assert from 'assert';

test('node assert', async ({ page }) => {
  await page.goto('https://example.com');
  const count = await page.locator('#items').count();
  assert.equal(count, 5);
});
""",
        """import { test } from '@playwright/test';
import assert from 'assert';

test('node assert', async ({ page }) => {
  await page.goto('https://example.com');
  const count = await page.locator('#items').count();
  assert.equal(count, 10);
});
""",
        False,
    ),
    (
        "010_expect_soft",
        "expect.soft",
        """import { test, expect } from '@playwright/test';

test('soft assertion', async ({ page }) => {
  await page.goto('https://example.com');
  await expect.soft(page.locator('#button')).toBeVisible();
});
""",
        """import { test, expect } from '@playwright/test';

test('soft assertion', async ({ page }) => {
  await page.goto('https://example.com');
  await expect.soft(page.locator('#button')).toBeHidden();
});
""",
        False,
    ),
    (
        "011_expect_poll",
        "expect.poll",
        """import { test, expect } from '@playwright/test';

test('poll assertion', async ({ page }) => {
  await page.goto('https://example.com');
  await expect.poll(() => page.locator('#count').textContent()).toBe('5');
});
""",
        """import { test, expect } from '@playwright/test';

test('poll assertion', async ({ page }) => {
  await page.goto('https://example.com');
  await expect.poll(() => page.locator('#count').textContent()).toBe('10');
});
""",
        False,
    ),
    (
        "012_to_match_chain",
        "toMatch chain",
        """import { test, expect } from '@playwright/test';

test('text match', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#message')).toMatch(/hello/i);
});
""",
        """import { test, expect } from '@playwright/test';

test('text match', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#message')).toMatch(/goodbye/i);
});
""",
        False,
    ),
    (
        "013_to_contain_chain",
        "toContain chain",
        """import { test, expect } from '@playwright/test';

test('array contains', async ({ page }) => {
  await page.goto('https://example.com');
  const items = await page.locator('.item').allTextContents();
  expect(items).toContain('Apple');
});
""",
        """import { test, expect } from '@playwright/test';

test('array contains', async ({ page }) => {
  await page.goto('https://example.com');
  const items = await page.locator('.item').allTextContents();
  expect(items).toContain('Orange');
});
""",
        False,
    ),
    (
        "014_resolves_chain",
        "resolves chain",
        """import { test, expect } from '@playwright/test';

test('promise resolves', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#button').click()).resolves.toBeUndefined();
});
""",
        """import { test, expect } from '@playwright/test';

test('promise resolves', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#button').click()).resolves.toBeNull();
});
""",
        False,
    ),
    (
        "015_rejects_chain",
        "rejects chain",
        """import { test, expect } from '@playwright/test';

test('promise rejects', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#missing')).rejects.toThrow();
});
""",
        """import { test, expect } from '@playwright/test';

test('promise rejects', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page.locator('#missing')).rejects.toBeDefined();
});
""",
        False,
    ),
    (
        "016_call_target_changed",
        "call target changed",
        """import { test } from '@playwright/test';

test('button action', async ({ page }) => {
  await page.goto('https://example.com');
  await page.click('#submit');
});
""",
        """import { test } from '@playwright/test';

test('button action', async ({ page }) => {
  await page.goto('https://example.com');
  await page.dblclick('#submit');
});
""",
        False,
    ),
    # ==================== ACCEPT CASES ====================
    (
        "017_data_testid_renamed",
        "data-testid renamed",
        """import { test } from '@playwright/test';

test('button click', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('[data-testid="old-button"]').click();
});
""",
        """import { test } from '@playwright/test';

test('button click', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('[data-testid="new-button"]').click();
});
""",
        True,
    ),
    (
        "018_accessible_name_changed",
        "accessible name changed in getByRole",
        """import { test } from '@playwright/test';

test('role button', async ({ page }) => {
  await page.goto('https://example.com');
  await page.getByRole('button', { name: 'Submit' }).click();
});
""",
        """import { test } from '@playwright/test';

test('role button', async ({ page }) => {
  await page.goto('https://example.com');
  await page.getByRole('button', { name: 'Send' }).click();
});
""",
        True,
    ),
    (
        "019_timeout_raised",
        "timeout option raised",
        """import { test } from '@playwright/test';

test('wait for element', async ({ page }) => {
  await page.goto('https://example.com');
  await page.waitForSelector('#button', { timeout: 5000 });
});
""",
        """import { test } from '@playwright/test';

test('wait for element', async ({ page }) => {
  await page.goto('https://example.com');
  await page.waitForSelector('#button', { timeout: 10000 });
});
""",
        True,
    ),
    (
        "020_selector_swapped",
        "selector string swapped within locator",
        """import { test } from '@playwright/test';

test('submit button', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('.submit-button').click();
});
""",
        """import { test } from '@playwright/test';

test('submit button', async ({ page }) => {
  await page.goto('https://example.com');
  await page.locator('[type="submit"]').click();
});
""",
        True,
    ),
]


@pytest.mark.parametrize(
    "name,description,original,patched,expected_allowed",
    AST_LOCK_CASES,
    ids=[case[0] for case in AST_LOCK_CASES],
)
def test_ast_lock_corpus(name, description, original, patched, expected_allowed):
    """Verify each fixture pair gets the expected AST lock verdict."""
    verdict = check_ast_lock(original, patched)

    assert verdict.allowed == expected_allowed, (
        f"Case {name} ({description}): "
        f"expected {'ACCEPT' if expected_allowed else 'REJECT'}, "
        f"got {'ACCEPT' if verdict.allowed else 'REJECT'} "
        f"(reason: {verdict.reason}, node: {verdict.node_kind}, line: {verdict.line})"
    )
