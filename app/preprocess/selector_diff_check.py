"""Selector Diff Check: does the diff remove or rename what the failing selector matches?

The DOM-diff abstraction keeps tags and attributes, so an element that is merely rendered
later (``{ready && <Button className="cta" />}``) looks like any other changed element, and the
model invents a new selector. This check answers the question deterministically from the raw
diff, so the prompts can state it as a fact.

It extracts every word the failing locator names (ids, classes, attribute values, quoted names
and texts, tags) and reports the selector as untouched only when **no** line removed by the diff
contains **any** of them. A rename always removes a line holding the old name, so a rename is
never reported as untouched.
"""

import re

import structlog

logger = structlog.get_logger(__name__)

# The first Playwright call-log target, e.g. `- waiting for locator('#submit')` or
# `- waiting for getByRole('button', { name: 'Get started' })`. The first one is the call that
# failed; later entries repeat it or belong to retries.
_WAITING_RE = re.compile(r"waiting for (.+)")
_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_WORD_RE = re.compile(r"[A-Za-z_][\w-]*")
# Selector-engine and option words that name no element: matching them against the diff would
# say nothing about whether the element itself changed.
_ENGINE_WORDS = frozenset(
    {"css", "xpath", "text", "role", "name", "has", "nth", "exact", "true", "false", "visible"}
)


def _removed_lines(git_diff: str) -> list[str]:
    """Content of the lines a unified diff removes, without the ``---`` file headers."""
    return [
        line[1:]
        for line in git_diff.splitlines()
        if line.startswith("-") and not line.startswith("--- ")
    ]


def failing_locator(raw_log: str) -> str:
    """The locator of the Playwright call that failed (the first ``waiting for``), or ``""``."""
    match = _WAITING_RE.search(raw_log)
    return match.group(1).strip() if match else ""


def locator_terms(locator: str) -> set[str]:
    """Every phrase and word the locator names: quoted strings whole, plus their words."""
    terms: set[str] = set()
    for single, double in _QUOTED_RE.findall(locator):
        literal = single or double
        phrase = literal.strip().lstrip("#.")
        if phrase and " " in phrase:
            terms.add(phrase)
        terms.update(
            word for word in _WORD_RE.findall(literal) if word.lower() not in _ENGINE_WORDS
        )
    return terms


def _mentions(line: str, term: str) -> bool:
    pattern = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
    return re.search(pattern, line, re.IGNORECASE) is not None


def untouched_failing_selector(raw_log: str, git_diff: str) -> str:
    """Return the failing locator when the diff removes no line naming it, else ``""``.

    ``""`` also covers every case the check cannot decide: no locator in the log (a
    navigation failure, for example), a locator with no element-naming word, or no diff.
    """
    locator = failing_locator(raw_log)
    if not locator or not git_diff.strip():
        return ""
    terms = locator_terms(locator)
    if not terms:
        return ""
    removed = _removed_lines(git_diff)
    if any(_mentions(line, term) for line in removed for term in terms):
        return ""
    logger.info("selector_untouched_by_diff", locator=locator, terms=sorted(terms))
    return locator
