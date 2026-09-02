"""Text SII codec.

Handles files such as `info.sii`, `profile.sii` and saves written with
`g_save_format 2`. Lines the editor does not touch are copied out verbatim,
including their original line endings, indentation and comments.
"""

from __future__ import annotations

import re

from .model import Attr, SiiFile, Unit

MAGIC = "SiiNunit"

_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z_][\w.]*)"
    r"(?:\[(?P<index>\d*)\])?[ \t]*:[ \t]*(?P<value>.*?)[ \t]*$"
)
_UNIT = re.compile(
    r"^[ \t]*(?P<cls>[A-Za-z_][\w.]*)[ \t]*:[ \t]*(?P<name>[^\s{]+)[ \t]*\{[ \t]*$"
)


class TextSiiError(Exception):
    pass


class _TextAttr(Attr):
    """An attribute that remembers which source lines it owns."""

    __slots__ = ("lines", "indent", "count_text")

    def __init__(self, name, value=None, items=None, indent=" ", lines=None):
        super().__init__(name, value, items)
        self.lines = lines if lines is not None else []
        self.indent = indent
        self.count_text = None
        self.raw = True  # anything not reassigned is written from the source


def decode(data: bytes) -> SiiFile:
    text = data.decode("utf-8", "surrogateescape")
    if not text.lstrip().startswith(MAGIC):
        raise TextSiiError("not a text SII payload")
    lines = text.splitlines(keepends=True)

    out = SiiFile()
    out.kind = "text"
    out.source = data
    out.blocks = lines  # the raw canvas we write back from

    unit: Unit | None = None
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "}":
            unit = None
            continue
        if stripped.endswith("{"):
            m = _UNIT.match(body)
            if m:
                unit = Unit(m.group("cls"), m.group("name"))
                out.units.append(unit)
            continue
        if unit is None:
            continue
        m = _LINE.match(body)
        if not m:
            continue
        name = m.group("name")
        value = m.group("value")
        index = m.group("index")
        if index is None:
            attr = _TextAttr(name, value, indent=m.group("indent"), lines=[i])
            unit.attrs.append(attr)
        else:
            attr = unit.get(name)
            if attr is None or not isinstance(attr, _TextAttr):
                attr = _TextAttr(name, None, [], indent=m.group("indent"), lines=[i])
                unit.attrs.append(attr)
            if not attr.is_array:
                # the previous `name: N` line was the element count
                attr.count_text = attr.value
                attr._value = None
                attr._items = []
                attr.raw = True
            attr._items.append(value)
            attr.lines.append(i)
    return out


def _render(attr: _TextAttr) -> list[str]:
    if attr.is_array:
        out = ["%s%s: %d\r\n" % (attr.indent, attr.name, len(attr.items))]
        out += ["%s%s[%d]: %s\r\n" % (attr.indent, attr.name, i, v)
                for i, v in enumerate(attr.items)]
        return out
    return ["%s%s: %s\r\n" % (attr.indent, attr.name, attr.value)]


def encode(file: SiiFile) -> bytes:
    if file.kind != "text":
        raise TextSiiError("not a text SII file")
    lines = list(file.blocks)
    replacements: dict[int, list[str]] = {}
    dropped: set[int] = set()
    for unit in file.units:
        for attr in unit.attrs:
            if not isinstance(attr, _TextAttr) or attr.raw:
                continue
            owned = attr.lines
            if not owned:
                continue
            replacements[owned[0]] = _render(attr)
            dropped.update(owned[1:])
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in dropped:
            continue
        out.extend(replacements.get(i, [line]))
    return "".join(out).encode("utf-8", "surrogateescape")
