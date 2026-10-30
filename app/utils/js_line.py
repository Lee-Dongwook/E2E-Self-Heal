"""Compare and restyle single JavaScript/TypeScript lines without changing what they mean.

The Patch Generator tells the engine which line to replace by quoting it. Models often re-type
that line rather than copy it: single quotes for double, the indentation dropped, spaces inside
parentheses. These helpers let ``_apply`` recognise such a quote as the line it names (#404),
and write the replacement in the file's own formatting rather than the model's.

Only formatting is forgiven. Two lines match when they have the same code *tokens* outside
string literals (spacing between tokens is ignored, but ``await page`` never equals
``awaitpage``, nor ``++`` equal ``+ +``) and the same string literal *values*, whatever quote
delimits them. A line with a template literal, a comment, a regex or a division (any ``/`` or
backtick outside a quoted string) is never matched loosely; the caller falls back to exact
comparison, so anything this scanner cannot read with certainty stays strict.
"""

import re
from typing import NamedTuple

_QUOTES = ("'", '"')
# Identifiers, numbers, multi-character operators (longest first), then any other character.
_TOKEN = re.compile(
    r"[A-Za-z_$][\w$]*|\d[\w.]*|\.\.\.|===|!==|\*\*=|<<=|>>=|=>|==|!=|<=|>=|&&|\|\||\?\?|\?\."
    r"|\+\+|--|\*\*|<<|>>|[-+*%&|^]=|\S"
)


class _Segment(NamedTuple):
    """A run of code, or one quoted string literal (``quote`` set, ``text`` its raw body)."""

    text: str
    quote: str | None = None


def _scan(line: str) -> list[_Segment] | None:
    """Split ``line`` into code runs and quoted literals, or ``None`` if it cannot be read safely."""
    segments: list[_Segment] = []
    code: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char in ("`", "/"):
            return None  # template, comment, regex or division: not handled, stay strict
        if char not in _QUOTES:
            code.append(char)
            index += 1
            continue
        end = index + 1
        while end < len(line) and line[end] != char:
            end += 2 if line[end] == "\\" else 1
        if end >= len(line):
            return None  # unterminated literal
        if code:
            segments.append(_Segment("".join(code)))
            code = []
        segments.append(_Segment(line[index + 1 : end], char))
        index = end + 1
    if code:
        segments.append(_Segment("".join(code)))
    return segments


def _decode(body: str) -> str:
    """Return a literal's body with escaped quotes unescaped; other escape pairs stay as written.

    ``'it\\'s'`` and ``"it's"`` decode alike. Every remaining backslash starts a pair, which
    :func:`_encode` relies on.
    """
    out: list[str] = []
    index = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            pair = body[index + 1]
            out.append(pair if pair in _QUOTES else "\\" + pair)
            index += 2
            continue
        out.append(body[index])
        index += 1
    return "".join(out)


def _encode(value: str, quote: str) -> str:
    """Write a decoded literal value as a literal delimited by ``quote``."""
    out: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            out.append(value[index : index + 2])
            index += 2
            continue
        out.append("\\" + quote if value[index] == quote else value[index])
        index += 1
    return quote + "".join(out) + quote


def _tokens(code: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(code))


def _key(segments: list[_Segment]) -> tuple[tuple[str, ...], ...]:
    """What a line means for matching: code tokens, and literal values."""
    return tuple(
        ("value", _decode(segment.text)) if segment.quote else ("code", *_tokens(segment.text))
        for segment in segments
        if segment.quote or segment.text.strip()
    )


def same_line_ignoring_format(line: str, quoted: str) -> bool:
    """Whether ``quoted`` is ``line`` re-typed, differing only in spacing and quote style."""
    line_segments = _scan(line)
    quoted_segments = _scan(quoted)
    if line_segments is None or quoted_segments is None:
        return False
    return _key(line_segments) == _key(quoted_segments)


def _significant(segments: list[_Segment]) -> list[_Segment]:
    return [segment for segment in segments if segment.quote or segment.text.strip()]


def _transplant(
    line: list[_Segment], original: list[_Segment], replacement: list[_Segment]
) -> str | None:
    """Rebuild ``line`` with the literal values the replacement changed, or ``None``.

    Applies when the model's original and replacement have the same code and differ only in
    literal values, the usual selector fix. Everything else on the line, code spacing and the
    quotes of untouched literals included, is the file's own text.
    """
    if _key(line) != _key(original):
        return None
    before, after = _significant(original), _significant(replacement)
    if len(after) != len(before):
        return None
    for old, new in zip(before, after, strict=True):
        if (old.quote is None) != (new.quote is None):
            return None
        if old.quote is None and _tokens(old.text) != _tokens(new.text):
            return None
    pieces: list[str] = []
    position = 0
    for segment in line:
        if segment.quote is None and not segment.text.strip():
            pieces.append(segment.text)  # whitespace between significant segments
            continue
        old, new = before[position], after[position]
        position += 1
        if segment.quote is None:
            pieces.append(segment.text)
        elif _decode(old.text) == _decode(new.text):
            pieces.append(segment.quote + segment.text + segment.quote)
        else:
            value = _decode(new.text)
            quote = (new.quote or segment.quote) if segment.quote in value else segment.quote
            pieces.append(_encode(value, quote))
    return "".join(pieces)


def restyle_like(line: str, original: str, replacement: str) -> str:
    """Write ``replacement`` in the formatting of ``line``, which ``original`` re-types.

    When the replacement only changes literal values, ``line`` itself is rebuilt with the new
    values, so its spacing and quotes survive exactly. Otherwise the replacement is written
    with ``line``'s indentation and trailing whitespace, its literals converted to ``line``'s
    quote when every literal there uses the same one and the value does not contain it. The
    result means what ``replacement`` means; callers still validate the line written.
    """
    # Spaces and tabs only: a newline in the replacement must survive to the scope gate that
    # rejects multi-line replacements.
    body = replacement.strip(" \t")
    line_segments = _scan(line)
    original_segments = _scan(original)
    body_segments = _scan(body)
    if line_segments is not None and original_segments is not None and body_segments is not None:
        rebuilt = _transplant(line_segments, original_segments, body_segments)
        if rebuilt is not None:
            return rebuilt
    indent = line[: len(line) - len(line.lstrip(" \t"))]
    trailing = line[len(line.rstrip(" \t")) :]
    file_quotes = {segment.quote for segment in line_segments or [] if segment.quote}
    if body_segments is None or len(file_quotes) != 1:
        return indent + body + trailing
    (quote,) = file_quotes
    pieces: list[str] = []
    for segment in body_segments:
        if segment.quote is None:
            pieces.append(segment.text)
            continue
        value = _decode(segment.text)
        if segment.quote == quote or quote in value:
            pieces.append(segment.quote + segment.text + segment.quote)
        else:
            pieces.append(_encode(value, quote))
    return indent + "".join(pieces) + trailing
