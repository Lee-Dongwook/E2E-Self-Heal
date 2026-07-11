"""Diff-JSX AST Analyzer: turn a git diff into before/after DOM node trees.

Uses tree-sitter (tree-sitter-typescript) for robust JSX/TSX element and attribute
extraction, with a regex-based fallback if tree-sitter is unavailable.
"""

import importlib.util
import re
import structlog
from app.schemas import DomDiff

logger = structlog.get_logger(__name__)

_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
_JSX_TAG_RE = re.compile(
    r"<([A-Za-z][\w.]*)((?:\s+[\w-]+=(?:\"[^\"]*\"|'[^']*'|\{[^}]*\}))*)\s*/?>"
)
_ATTR_RE = re.compile(r"([\w-]+)=(?:\"([^\"]*)\"|'([^']*)'|\{([^}]*)\})")
_JSX_SUFFIXES = (".tsx", ".jsx")

_HAS_TREE_SITTER = (
    importlib.util.find_spec("tree_sitter") is not None
    and importlib.util.find_spec("tree_sitter_typescript") is not None
)


def _parse_element_regex(line: str) -> dict | None:
    tag_match = _JSX_TAG_RE.search(line)
    if not tag_match:
        return None
    attrs: dict[str, str] = {}
    for attr_match in _ATTR_RE.finditer(tag_match.group(2)):
        name = attr_match.group(1)
        value = attr_match.group(2) or attr_match.group(3) or attr_match.group(4) or ""
        attrs[name] = value
    return {"tag": tag_match.group(1), "attributes": attrs}


def _analyze_diff_regex(git_diff: str) -> list[DomDiff]:
    diffs: list[DomDiff] = []
    current_file = ""
    removed: list[dict] = []
    added: list[dict] = []

    def flush(file: str) -> None:
        for previous, current in zip(removed, added):
            diffs.append(DomDiff(file=file, previous=previous, current=current))
        removed.clear()
        added.clear()

    for line in git_diff.splitlines():
        file_match = _FILE_RE.match(line)
        if file_match:
            flush(current_file)
            current_file = file_match.group(1)
            continue
        if not current_file.endswith(_JSX_SUFFIXES):
            continue
        if line.startswith("-") and not line.startswith("---"):
            element = _parse_element_regex(line[1:])
            if element:
                removed.append(element)
        elif line.startswith("+") and not line.startswith("+++"):
            element = _parse_element_regex(line[1:])
            if element:
                added.append(element)
    flush(current_file)
    return diffs


def _extract_jsx_elements_tree_sitter(code_bytes: bytes) -> list[dict]:
    if not _HAS_TREE_SITTER:
        return []

    import tree_sitter_typescript as ts_typescript
    from tree_sitter import Language, Parser

    tsx_language = Language(ts_typescript.language_tsx())
    parser = Parser(tsx_language)
    tree = parser.parse(code_bytes)
    elements = []

    def walk(node):
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
                    tag = code_bytes[child.start_byte : child.end_byte].decode(
                        "utf-8", errors="ignore"
                    )
                    break

            attrs = {}
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
                            val_text = code_bytes[
                                attr_child.start_byte : attr_child.end_byte
                            ].decode("utf-8", errors="ignore")
                            if val_text.startswith(('"', "'")) and val_text.endswith(('"', "'")):
                                value = val_text[1:-1]
                            else:
                                value = val_text
                        elif attr_child.type == "jsx_expression":
                            val_text = code_bytes[
                                attr_child.start_byte : attr_child.end_byte
                            ].decode("utf-8", errors="ignore")
                            if val_text.startswith("{") and val_text.endswith("}"):
                                value = val_text[1:-1]
                            else:
                                value = val_text
                    if name:
                        attrs[name] = value

            elements.append({"tag": tag, "attributes": attrs})
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return elements


def _analyze_diff_tree_sitter(git_diff: str) -> list[DomDiff]:
    diffs: list[DomDiff] = []
    current_file = ""
    hunks = []

    current_removed = []
    current_added = []

    def flush_hunk(file: str) -> None:
        if current_removed or current_added:
            rem_str = "\n".join(current_removed)
            add_str = "\n".join(current_added)
            hunks.append((rem_str, add_str))
            current_removed.clear()
            current_added.clear()

    def process_file_hunks(file: str) -> None:
        flush_hunk(file)
        for rem_str, add_str in hunks:
            rem_elements = _extract_jsx_elements_tree_sitter(rem_str.encode("utf-8"))
            add_elements = _extract_jsx_elements_tree_sitter(add_str.encode("utf-8"))
            for prev, curr in zip(rem_elements, add_elements):
                diffs.append(DomDiff(file=file, previous=prev, current=curr))
        hunks.clear()

    for line in git_diff.splitlines():
        file_match = _FILE_RE.match(line)
        if file_match:
            process_file_hunks(current_file)
            current_file = file_match.group(1)
            continue

        if not current_file.endswith(_JSX_SUFFIXES):
            continue

        if line.startswith("-") and not line.startswith("---"):
            current_removed.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            current_added.append(line[1:])
        elif line.startswith(" "):
            flush_hunk(current_file)
        elif line.startswith("@@"):
            flush_hunk(current_file)

    process_file_hunks(current_file)
    return diffs


def analyze_diff(git_diff: str) -> list[DomDiff]:
    """Parse the JSX/TSX regions of a git diff into lightweight DOM diffs.

    Uses Tree-sitter if available to pair changed elements, and falls back to
    regex line pairing on import or parsing failures.
    """
    if _HAS_TREE_SITTER:
        try:
            diffs = _analyze_diff_tree_sitter(git_diff)
            logger.debug("diff_analyzed_tree_sitter", dom_changes=len(diffs))
            return diffs
        except Exception as exc:
            logger.warning("tree_sitter_diff_analysis_failed_falling_back", error=str(exc))

    diffs = _analyze_diff_regex(git_diff)
    logger.debug("diff_analyzed_regex", dom_changes=len(diffs))
    return diffs
