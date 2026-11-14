"""Diff-JSX AST Analyzer: turn a git diff into before/after DOM node trees.

Uses tree-sitter (tree-sitter-typescript) for robust JSX/TSX element and attribute
extraction, with a regex-based fallback if tree-sitter is unavailable.
"""

import difflib
import importlib.util
import re
from collections.abc import Callable
from typing import Any, TypedDict

import structlog

from app.schemas import DomDiff

logger = structlog.get_logger(__name__)


class JsxElement(TypedDict):
    """A single extracted JSX opening/self-closing element."""

    tag: str
    attributes: dict[str, str]
    line: int


_JSX_TAG_RE = re.compile(
    r"<([A-Za-z][\w.\-]*)"
    r"(?:\s+(?:{[^}]*}|[\w\-]+(?:=(?:\"[^\"]*\"|'[^']*'|{[^}]*}))?))*"
    r"\s*/?>"
)

# Match one JSX attribute — ``name``, ``name="v"``, ``name='v'`` or ``name={expr}`` — or a
# spread (``{...props}``). Spreads are skipped so their inner identifiers aren't misread as
# boolean attributes; a bare ``name`` with no value is captured as a boolean attribute
# (value ``""``), matching the tree-sitter backend.
_ATTR_RE = re.compile(
    r"(?P<spread>{[^}]*})"
    r"|(?P<name>[\w\-]+)(?:=(?:\"(?P<dq>[^\"]*)\"|'(?P<sq>[^']*)'|{(?P<br>[^}]*)}))?"
)

# Parse unified diff hunk header: @@ -old_start,old_count +new_start,new_count @@
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Matches a line that ends a complete JSX element (</tag> or />).
_COMPLETE_JSX_END_RE = re.compile(r"(?:</[A-Za-z][\w.\-]*>|/>)\s*$")

# A JSX/TSX tag opener (``<input``) and the pieces of a line that can hide a ``>`` which
# does not close a tag: string literals and arrow functions.
_TAG_OPENER_RE = re.compile(r"<[A-Za-z]")
_NON_TAG_GT_RE = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`|=>")

# Closes a tag left open at the end of a hunk so the parser still sees its attributes.
_SYNTHETIC_TAG_CLOSE = "/>"

# One hunk line: kind (" ", "-" or "+"), text, old-file line number, new-file line number.
HunkEntry = tuple[str, str, int, int]
# One parse unit: removed-side lines, added-side lines, and whether a tag was left open.
HunkGroup = tuple[list[tuple[str, int]], list[tuple[str, int]], bool]

_JSX_SUFFIXES = (".tsx", ".jsx")

_HAS_TREE_SITTER = (
    importlib.util.find_spec("tree_sitter") is not None
    and importlib.util.find_spec("tree_sitter_typescript") is not None
)

Extractor = Callable[[str, int], list[JsxElement]]

# Sentinel for the "missing side" of an added/deleted pair.
_EMPTY_ELEMENT: JsxElement = {"tag": "", "attributes": {}, "line": 0}


def _chunk_fragment_lines(code_text: str) -> list[tuple[str, int]]:
    """Split a fragment into (chunk_text, start_row) pairs at statement boundaries.

    JavaScript parses ``<`` on a new line as a continuation of the previous
    expression, so consecutive top-level JSX elements collapse into one broken
    parse. Splitting after every line that already ends a complete element lets
    each element (or multi-line element) parse independently, without shifting
    line numbers.
    """
    lines = code_text.split("\n")
    chunks: list[tuple[str, int]] = []
    current: list[str] = []
    start_row = 0
    for i, line in enumerate(lines):
        if current and line.lstrip().startswith("<") and _COMPLETE_JSX_END_RE.search(lines[i - 1]):
            chunks.append(("\n".join(current), start_row))
            current = [line]
            start_row = i
        else:
            current.append(line)
    if current:
        chunks.append(("\n".join(current), start_row))
    return chunks


def _tag_state_after(line: str, inside_tag: bool) -> bool:
    """Whether a JSX opening tag is still open after ``line``, given the state before it."""
    masked = _NON_TAG_GT_RE.sub("", line)
    last_opener = max((m.start() for m in _TAG_OPENER_RE.finditer(masked)), default=-1)
    last_close = masked.rfind(">")
    if last_opener > last_close:
        return True
    if last_close != -1:
        return False
    return inside_tag


def _group_hunk(entries: list[HunkEntry]) -> list[HunkGroup]:
    """Split a hunk into contiguous runs of changed lines, one parse unit each.

    A run that edits attribute lines of a multi-line opening tag (``<input`` on one
    line, ``id="..."`` on the next) has no ``<tag`` of its own, so it is widened over
    the surrounding context lines to the tag's opener and on to the line that closes
    it; runs that fall inside the same tag merge. Context outside such a tag is never
    parsed, so unchanged neighbouring elements are not reported.
    """
    groups: list[HunkGroup] = []
    index = 0
    while index < len(entries):
        if entries[index][0] == " ":
            index += 1
            continue

        start = index
        # Widen back to an opener on a context line if the run starts inside its tag.
        for back in range(index - 1, -1, -1):
            kind, text, _, _ = entries[back]
            if kind != " ":
                break
            masked = _NON_TAG_GT_RE.sub("", text)
            if _TAG_OPENER_RE.search(masked) and _tag_state_after(text, inside_tag=False):
                start = back
                break
            if ">" in masked:
                break

        old_open = new_open = False
        end = start
        # Take the changed run, then keep going while either side leaves a tag open.
        while end < len(entries):
            kind, text, _, _ = entries[end]
            if kind == " " and end >= index and not (old_open or new_open):
                break
            if kind != "+":
                old_open = _tag_state_after(text, old_open)
            if kind != "-":
                new_open = _tag_state_after(text, new_open)
            end += 1

        removed = [(text, old) for kind, text, old, _ in entries[start:end] if kind != "+"]
        added = [(text, new) for kind, text, _, new in entries[start:end] if kind != "-"]
        groups.append((removed, added, old_open or new_open))
        index = end
    return groups


def _extract_jsx_elements_regex(code_text: str, start_line: int) -> list[JsxElement]:
    """Extract JSX elements from a (possibly multi-line) code fragment with line numbers."""
    elements: list[JsxElement] = []
    for match in _JSX_TAG_RE.finditer(code_text):
        tag = match.group(1)
        # Everything after `<tag`; _ATTR_RE skips the trailing `>`/>
        body = match.group(0)[len(tag) + 1 :]
        attrs: dict[str, str] = {}
        for attr_match in _ATTR_RE.finditer(body):
            if attr_match.group("spread"):
                continue
            name = attr_match.group("name")
            if not name:
                continue
            value = attr_match.group("dq") or attr_match.group("sq") or attr_match.group("br") or ""
            attrs[name] = value
        line = start_line + code_text.count("\n", 0, match.start())
        elements.append({"tag": tag, "attributes": attrs, "line": line})
    return elements


def _walk_jsx_nodes(
    node: Any,
    code_bytes: bytes,
    base_line: int,
    elements: list[JsxElement],
) -> None:
    """Recursively collect JSX opening/self-closing elements with line numbers."""
    if node.type in ("jsx_opening_element", "jsx_self_closing_element"):
        tag = ""
        for child in node.children:
            if child.type in (
                "identifier",
                "member_expression",
                "nested_identifier",
                "jsx_namespace_name",
                "jsx_identifier",
                "jsx_member_expression",
            ):
                tag = code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
                break
        attrs: dict[str, str] = {}
        for child in node.children:
            if child.type == "jsx_attribute":
                name = ""
                value = ""
                for attr_child in child.children:
                    if attr_child.type in ("property_identifier", "jsx_namespace_name"):
                        name = code_bytes[attr_child.start_byte : attr_child.end_byte].decode(
                            "utf-8", errors="ignore"
                        )
                    elif attr_child.type == "string":
                        val_text = code_bytes[attr_child.start_byte : attr_child.end_byte].decode(
                            "utf-8", errors="ignore"
                        )
                        if val_text.startswith(('"', "'")) and val_text.endswith(('"', "'")):
                            value = val_text[1:-1]
                        else:
                            value = val_text
                    elif attr_child.type == "jsx_expression":
                        val_text = code_bytes[attr_child.start_byte : attr_child.end_byte].decode(
                            "utf-8", errors="ignore"
                        )
                        if val_text.startswith("{") and val_text.endswith("}"):
                            value = val_text[1:-1]
                        else:
                            value = val_text
                if name:
                    attrs[name] = value
        elements.append({"tag": tag, "attributes": attrs, "line": base_line + node.start_point[0]})

    for child in node.children:
        _walk_jsx_nodes(child, code_bytes, base_line, elements)


def _extract_jsx_elements_tree_sitter(code_text: str, start_line: int) -> list[JsxElement]:
    """Extract JSX elements from a code fragment via tree-sitter with line numbers."""
    if not _HAS_TREE_SITTER:
        return []
    import tree_sitter_typescript as ts_typescript
    from tree_sitter import Language, Parser

    tsx_language = Language(ts_typescript.language_tsx())
    parser = Parser(tsx_language)
    elements: list[JsxElement] = []

    for chunk_text, start_row in _chunk_fragment_lines(code_text):
        code_bytes = chunk_text.encode("utf-8")
        tree = parser.parse(code_bytes)
        _walk_jsx_nodes(tree.root_node, code_bytes, start_line + start_row, elements)
    return elements


def _elem_to_str(elem: JsxElement) -> str:
    """Deterministic string representation for SequenceMatcher.

    A space separates the tag from its attributes so ``<a bc=d>`` and ``<ab c=d>``
    never collide into the same serialization.
    """
    attr_str = ",".join(f"{k}={v}" for k, v in sorted(elem["attributes"].items()))
    return f"<{elem['tag']} {attr_str}>" if attr_str else f"<{elem['tag']}>"


def _pair_elements(
    removed: list[JsxElement], added: list[JsxElement]
) -> list[tuple[JsxElement, JsxElement]]:
    """Pair removed/added elements via SequenceMatcher to handle N!=M hunks."""
    pairs: list[tuple[JsxElement, JsxElement]] = []
    rem_strs = [_elem_to_str(e) for e in removed]
    add_strs = [_elem_to_str(e) for e in added]
    # autojunk=False keeps matching deterministic on large hunks.
    matcher = difflib.SequenceMatcher(None, rem_strs, add_strs, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                pairs.append((removed[i], added[j]))
        elif tag == "replace":
            n = min(i2 - i1, j2 - j1)
            for k in range(n):
                pairs.append((removed[i1 + k], added[j1 + k]))
            for i in range(i1 + n, i2):
                pairs.append((removed[i], _EMPTY_ELEMENT))
            for j in range(j1 + n, j2):
                pairs.append((_EMPTY_ELEMENT, added[j]))
        elif tag == "delete":
            for i in range(i1, i2):
                pairs.append((removed[i], _EMPTY_ELEMENT))
        elif tag == "insert":
            for j in range(j1, j2):
                pairs.append((_EMPTY_ELEMENT, added[j]))
    return pairs


def _strip_timestamp(path: str) -> str:
    """Drop a trailing tab-delimited timestamp from a diff header path."""
    return path.split("\t")[0] if "\t" in path else path


def _analyze(git_diff: str, extract: Extractor) -> list[DomDiff]:
    """Walk diff headers; pair extracted elements per hunk via `extract` with line tracking."""
    diffs: list[DomDiff] = []
    current_file = ""
    pending_minus = ""
    cur_hunk: list[HunkEntry] = []
    hunks: list[HunkGroup] = []
    old_line = 0
    new_line = 0
    in_hunk = False

    def flush_hunk() -> None:
        if cur_hunk:
            hunks.extend(_group_hunk(cur_hunk))
        cur_hunk.clear()

    def process_file() -> None:
        flush_hunk()
        if current_file.endswith(_JSX_SUFFIXES):
            for rem_lines, add_lines, left_open in hunks:
                rem_text = "\n".join(text for text, _ in rem_lines)
                add_text = "\n".join(text for text, _ in add_lines)
                if left_open:
                    # Appended after the last line, so no element's line number shifts.
                    rem_text = f"{rem_text}\n{_SYNTHETIC_TAG_CLOSE}" if rem_lines else rem_text
                    add_text = f"{add_text}\n{_SYNTHETIC_TAG_CLOSE}" if add_lines else add_text

                rem_start = rem_lines[0][1] if rem_lines else 1
                add_start = add_lines[0][1] if add_lines else 1

                rem_el = extract(rem_text, rem_start)
                add_el = extract(add_text, add_start)

                for prev, curr in _pair_elements(rem_el, add_el):
                    # Added/changed elements use the new-file line; deletions stay 0.
                    line = curr["line"] if curr["tag"] else 0
                    prev_clean = (
                        {"tag": prev["tag"], "attributes": prev["attributes"]}
                        if prev["tag"]
                        else {}
                    )
                    curr_clean = (
                        {"tag": curr["tag"], "attributes": curr["attributes"]}
                        if curr["tag"]
                        else {}
                    )
                    diffs.append(
                        DomDiff(
                            file=current_file,
                            line=line,
                            previous=prev_clean,
                            current=curr_clean,
                        )
                    )
        hunks.clear()

    for line in git_diff.splitlines():
        if line.startswith("diff --git "):
            process_file()
            current_file = ""
            pending_minus = ""
            old_line = 0
            new_line = 0
            in_hunk = False
            header = line[len("diff --git ") :]
            sep_index = header.rfind(" b/")
            if sep_index != -1:
                current_file = header[sep_index + 1 :].removeprefix("b/")
            else:
                parts = header.split(" ", 1)
                if len(parts) == 2:
                    current_file = parts[1].removeprefix("b/")
        elif line.startswith("@@ "):
            flush_hunk()
            match = _HUNK_RE.match(line)
            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(3))
                in_hunk = True
        elif not in_hunk:
            # File-level headers only (before the first hunk).
            if line.startswith("--- "):
                path = _strip_timestamp(line[4:].strip()).removeprefix("a/")
                if path != "/dev/null":
                    pending_minus = path
                if not current_file or current_file == "/dev/null":
                    current_file = path
            elif line.startswith("+++ "):
                path = _strip_timestamp(line[4:].strip()).removeprefix("b/")
                if path != "/dev/null":
                    current_file = path
                else:
                    current_file = pending_minus
            elif line.startswith("rename to "):
                process_file()
                current_file = line[10:].strip()
        elif line.startswith("\\"):
            # "\ No newline at end of file" marker carries no position info.
            continue
        elif not current_file.endswith(_JSX_SUFFIXES):
            continue
        elif line.startswith("-"):
            # Removed content line (incl. content that itself starts with ---).
            cur_hunk.append(("-", line[1:], old_line, new_line))
            old_line += 1
        elif line.startswith("+"):
            # Added content line (incl. content that itself starts with +++).
            cur_hunk.append(("+", line[1:], old_line, new_line))
            new_line += 1
        else:
            # Context line: " text" or "" for blank lines. Kept so a run of changes inside
            # a multi-line opening tag can be widened to the whole tag (_group_hunk).
            cur_hunk.append((" ", line[1:], old_line, new_line))
            old_line += 1
            new_line += 1
    process_file()
    return diffs


def _analyze_diff_regex(git_diff: str) -> list[DomDiff]:
    """Regex backend: multi-line-capable element extraction per hunk."""
    return _analyze(git_diff, _extract_jsx_elements_regex)


def _analyze_diff_tree_sitter(git_diff: str) -> list[DomDiff]:
    """Tree-sitter backend: AST element extraction per hunk."""
    return _analyze(git_diff, _extract_jsx_elements_tree_sitter)


def analyze_diff(git_diff: str) -> list[DomDiff]:
    """Parse the JSX/TSX regions of a git diff into lightweight DOM diffs.

    Uses tree-sitter when available and falls back to regex on import or parsing failure.
    """
    if _HAS_TREE_SITTER:
        try:
            diffs = _analyze_diff_tree_sitter(git_diff)
            logger.debug("diff_analyzed_tree_sitter", dom_changes=len(diffs))
            return diffs
        except Exception as exc:  # noqa: BLE001
            logger.warning("tree_sitter_diff_failed_falling_back", error=str(exc))
    diffs = _analyze_diff_regex(git_diff)
    logger.debug("diff_analyzed_regex", dom_changes=len(diffs))
    return diffs
