"""Selector classification used by the healing-history store."""

import re
from enum import Enum


class SelectorKind(str, Enum):
    """Categorized types of CSS/DOM selectors."""

    CSS_ID = "css_id"
    CSS_CLASS = "css_class"
    ROLE = "role"
    TEST_ID = "test_id"
    XPATH = "xpath"
    TEXT = "text"
    OTHER = "other"


def classify_selector_kind(selector: str) -> SelectorKind:
    """Heuristically classify a selector string into a SelectorKind."""
    selector = selector.strip().lower()

    selector = re.sub(r"^(page|locator|expect)\.", "", selector)

    if selector.startswith(("xpath=", "//")):
        return SelectorKind.XPATH

    # Strip a "css=" prefix so the payload drives ID vs class classification
    # (e.g. "css=#main" -> CSS_ID) instead of bucketing all css= selectors as CSS_CLASS.
    selector = selector.removeprefix("css=")

    if selector.startswith(("#", "[id=")):
        return SelectorKind.CSS_ID

    # Exclude the .xpath engine prefix from class matching.
    if selector.startswith("[class") or (
        selector.startswith(".") and not selector.startswith(".xpath")
    ):
        return SelectorKind.CSS_CLASS

    if selector.startswith(("[data-testid=", "[data-test=", "getbytestid")):
        return SelectorKind.TEST_ID

    if selector.startswith(("role=", "getbyrole")):
        return SelectorKind.ROLE

    if selector.startswith(("text=", "getbytext", "getbylabel")):
        return SelectorKind.TEXT

    if selector.startswith("["):
        return SelectorKind.CSS_CLASS

    return SelectorKind.OTHER
