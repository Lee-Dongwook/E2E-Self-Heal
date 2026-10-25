"""Tests for selector-kind classification (Issue #122)."""

from app.registry import SelectorKind, classify_selector_kind


def test_classify_selector_kind():
    """Test selector classification with expanded coverage."""
    # CSS ID
    assert classify_selector_kind("#submit-btn") == SelectorKind.CSS_ID
    assert classify_selector_kind("[id='submit']") == SelectorKind.CSS_ID

    # CSS Class
    assert classify_selector_kind(".btn-primary") == SelectorKind.CSS_CLASS
    assert classify_selector_kind("[class='btn']") == SelectorKind.CSS_CLASS

    # Test ID
    assert classify_selector_kind("[data-testid='login']") == SelectorKind.TEST_ID
    assert classify_selector_kind("[data-test='login']") == SelectorKind.TEST_ID
    assert classify_selector_kind("getByTestId('login')") == SelectorKind.TEST_ID

    # Role
    assert classify_selector_kind("role=button[name='Submit']") == SelectorKind.ROLE
    assert classify_selector_kind("getByRole('button')") == SelectorKind.ROLE
    assert classify_selector_kind("page.getByRole('button')") == SelectorKind.ROLE

    # XPath - support xpath= prefix
    assert classify_selector_kind("//div[@id='main']") == SelectorKind.XPATH
    assert classify_selector_kind("xpath=//div[@id='main']") == SelectorKind.XPATH

    # Text
    assert classify_selector_kind("text='Click me'") == SelectorKind.TEXT
    assert classify_selector_kind("getByText('Submit')") == SelectorKind.TEXT
    assert classify_selector_kind("getByLabel('Email')") == SelectorKind.TEXT

    # Attribute selectors
    assert classify_selector_kind("[class*=btn]") == SelectorKind.CSS_CLASS

    # Other
    assert classify_selector_kind("div > span") == SelectorKind.OTHER


def test_classify_selector_kind_not_reachable_branches():
    """Ensure .xpath is NOT matched as CSS class (was unreachable before fix)."""
    # This should be OTHER, not CSS_CLASS (the .xpath branch was unreachable)
    assert classify_selector_kind(".xpath(//div)") == SelectorKind.OTHER


def test_classify_selector_kind_css_prefix():
    """The "css=" prefix should defer to the underlying payload, not force CSS_CLASS."""
    assert classify_selector_kind("css=#main") == SelectorKind.CSS_ID
    assert classify_selector_kind("css=.btn-primary") == SelectorKind.CSS_CLASS
