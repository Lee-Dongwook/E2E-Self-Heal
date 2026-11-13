"""Ambiguous Target Check: did the element the test used become several look-alikes?

When a diff removes the element a failing locator named and adds two or more elements of the
same kind in its place (one ``Submit`` button becomes ``Save draft`` and ``Publish``), any of
them can make the test pass, and picking one is a guess about what the test author meant. A
model asked to repair it picks one anyway, and the repair reads as a success. This check
recognizes the situation from the abstracted DOM diff alone, so the graph can refuse it as
``ambiguous_target`` before any model call.

It is deliberately narrow. It fires in two situations, and never when a ``--selector-hint`` was
given, because a hint is a person's answer to the question:

* **Playwright reports a strict-mode violation**: the failing locator resolved to several
  elements. That is Playwright's own evidence, so it covers every kind of locator, role and
  text ones included, and its ``aka`` lines name each match (#458).
* **The diff shows a look-alike split.** The failing locator names an identifying attribute
  of an element the diff removes (``id``, a class, ``data-testid``, ``name``, or an
  accessible-name attribute: ``aria-label``, ``placeholder``, ``title``, ``alt``), and the same
  file gains at least two elements of the same kind that do not carry the old name. Explicit
  submit controls (``<button type="submit">``, ``<input type="submit">``, ``<input
  type="image">``) are one kind, and an ``<input>`` without a ``type`` is a text input.

A locator that names an element only by its text never matches the diff check, because the
abstracted diff keeps tags and attributes, not text: such tests are left to the repair loop
unless Playwright reports the strict-mode violation itself.

There is no setting to tune or turn this off: a configuration value may never lower a refusal
check (see the guardrails in ``.claude/CLAUDE.md``). The only way past it is ``--selector-hint``.
"""

import re

import structlog

from app.preprocess.selector_diff_check import failing_locator, locator_terms

logger = structlog.get_logger(__name__)

# Attributes whose values a locator names an element by. ``type`` is left out on purpose:
# ``type="submit"`` is shared by every submit button, so it identifies none of them.
_IDENTIFYING = ("id", "className", "class", "data-testid", "name")
# Accessible-name attributes, matched whole: getByRole's ``name``, getByLabel, getByPlaceholder,
# getByTitle and getByAltText name an element by these.
_ACCESSIBLE_NAMES = ("aria-label", "placeholder", "title", "alt")
_MIN_LOOK_ALIKES = 2
_SUBMIT_CONTROLS = {("button", "submit"), ("input", "submit"), ("input", "image")}
_STRICT_MODE = re.compile(
    r"strict mode violation: (?P<locator>.+?) resolved to (?P<count>\d+) elements"
)
# The failure under repair: the first `Error:` line, as parse_error_log selects it.
_FIRST_ERROR = re.compile(r"^\s*Error:.*$", re.MULTILINE)
# "    1) <button ... id="a">Submit</button> aka locator('#a')"
_MATCH_LINE = re.compile(r"^\s*\d+\)\s+(?P<element><.*?)(?:\s+aka\s+(?P<aka>.+?))?\s*$")
_LOCATOR_CALL = re.compile(r"""^locator\((['"])(?P<selector>.+)\1\)$""")
_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _names(element: dict) -> set[str]:
    """Every identifying token of an element: ids, test ids, names, each class, and its
    accessible-name attributes (whole)."""
    attributes = element.get("attributes", {})
    tokens: set[str] = set()
    for key in _IDENTIFYING:
        value = str(attributes.get(key, "")).strip()
        if not value:
            continue
        tokens.update(value.split() if key in ("className", "class") else [value])
    for key in _ACCESSIBLE_NAMES:
        value = str(attributes.get(key, "")).strip()
        if value:
            tokens.add(value)
    return tokens


def _kind(element: dict) -> tuple[str, str]:
    tag = element.get("tag", "")
    type_ = str(element.get("attributes", {}).get("type", "")).strip().lower()
    if tag == "input" and not type_:
        type_ = "text"  # the HTML default
    if (tag, type_) in _SUBMIT_CONTROLS:
        return "submit-control", "submit"
    return tag, type_


def _selector_for(element: dict, line: int) -> str:
    """A selector a person could pass back as ``--selector-hint`` to choose this element."""
    attributes = element.get("attributes", {})
    if attributes.get("id"):
        return f"#{attributes['id']}"
    if attributes.get("data-testid"):
        return f'[data-testid="{attributes["data-testid"]}"]'
    if attributes.get("name"):
        return f'[name="{attributes["name"]}"]'
    classes = str(attributes.get("className") or attributes.get("class") or "").split()
    if classes:
        return "." + ".".join(classes)
    return f"<{element.get('tag', '')}> at line {line}"


def _strict_mode_matches(raw_log: str) -> list[str]:
    """The elements Playwright says the failing locator resolved to, or ``[]``.

    Only the failure being repaired counts: the violation must be on the log's first
    ``Error:`` line, the one ``parse_error_log`` hands the model. A later test's violation in
    the same log says nothing about this one. Each match is named by Playwright's own
    suggested locator (``aka``), unwrapped to a bare selector when it is a plain
    ``locator('...')``, so it can go straight into ``--selector-hint``; an element without one
    is named by its HTML.
    """
    error = _FIRST_ERROR.search(raw_log)
    if error is None:
        return []
    violation = _STRICT_MODE.search(error.group(0))
    if violation is None or int(violation.group("count")) < _MIN_LOOK_ALIKES:
        return []
    candidates: list[str] = []
    for line in raw_log[error.end() :].splitlines()[1:]:
        match = _MATCH_LINE.match(line)
        if match is None:
            break
        aka = (match.group("aka") or "").strip()
        call = _LOCATOR_CALL.match(aka)
        candidate = call.group("selector") if call else aka or match.group("element")
        if candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        # The matches weren't listed (a trimmed log): the count alone still settles it.
        candidates = [violation.group("locator")]
    logger.info(
        "ambiguous_target_detected",
        source="strict_mode_violation",
        locator=violation.group("locator"),
        matches=int(violation.group("count")),
        candidates=candidates,
    )
    return candidates


def ambiguous_replacements(raw_log: str, dom_diff_context: list[dict]) -> list[str]:
    """Selectors of the elements the failing locator could now mean, or ``[]``.

    A non-empty result means the test cannot say which of them it meant, so a repair would be
    a guess. ``[]`` covers every case the check cannot decide.
    """
    if any(entry.get("type") == "selector_hint" for entry in dom_diff_context):
        return []
    strict = _strict_mode_matches(raw_log)
    if strict:
        return strict
    locator = failing_locator(raw_log)
    # Whole quoted values too: an accessible name such as "you@example.com" is one value, but
    # locator_terms keeps only its words.
    terms = locator_terms(locator) | {
        (single or double).strip() for single, double in _QUOTED.findall(locator)
    } - {""}
    if not terms:
        return []
    for removed in dom_diff_context:
        previous, current = removed.get("previous") or {}, removed.get("current") or {}
        matched = _names(previous) & terms
        if not previous or not matched or matched & _names(current):
            continue
        look_alikes = [
            (entry.get("current") or {}, int(entry.get("line", 0)))
            for entry in dom_diff_context
            if entry.get("file") == removed.get("file")
            and (entry is removed or not entry.get("previous"))
        ]
        candidates = [
            _selector_for(element, line)
            for element, line in look_alikes
            if element and _kind(element) == _kind(previous) and not _names(element) & matched
        ]
        if len(candidates) >= _MIN_LOOK_ALIKES:
            logger.info(
                "ambiguous_target_detected",
                source="diff",
                removed=sorted(matched),
                candidates=candidates,
                file=removed.get("file"),
            )
            return candidates
    return []
