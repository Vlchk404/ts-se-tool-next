"""In-memory model of an SII file: an ordered list of units.

Values are kept as the text the game itself would write, and every value also
remembers the raw bytes (BSII) or source line (text SII) it came from. Writing
re-emits those raw bytes for anything the editor did not touch, so a load/save
round trip is byte-identical apart from the fields that were changed.
"""

from __future__ import annotations

import struct


def float_to_text(value: float) -> str:
    """The game writes floats as the hex of their IEEE-754 bits: &3f800000."""
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    return "&%08x" % bits


def text_to_float(text: str) -> float:
    text = text.strip()
    if text.startswith("&"):
        return struct.unpack("<f", struct.pack("<I", int(text[1:], 16)))[0]
    return float(text)


class Attr:
    """One `name: value` line, or one `name: count` + `name[i]: value` group.

    Assigning to `value` or `items` drops `raw`, which is what marks the
    attribute for re-encoding.
    """

    __slots__ = ("name", "_value", "_items", "raw", "field")

    def __init__(self, name: str, value: str | None = None,
                 items: list[str] | None = None, raw=None, field=None):
        self.name = name
        self._value = value
        self._items = items
        self.raw = raw
        self.field = field

    @property
    def value(self) -> str | None:
        return self._value

    @value.setter
    def value(self, new: str) -> None:
        new = str(new)
        if new != self._value or self._items is not None:
            self._value = new
            self._items = None
            self.raw = None

    @property
    def items(self) -> list[str] | None:
        return self._items

    @items.setter
    def items(self, new: list[str] | None) -> None:
        self._items = None if new is None else [str(x) for x in new]
        self._value = None
        self.raw = None

    @property
    def is_array(self) -> bool:
        return self._items is not None

    @property
    def array_like(self) -> bool:
        """True for an array, and for the `name: 0` line a text save writes for
        an empty one — in the text form that is indistinguishable from a plain
        number until something is put into it. For binary saves the structure
        definition settles the question.
        """
        if self._items is not None:
            return True
        if self.field is not None:
            return bool(getattr(self.field, "is_array", False))
        return (self._value or "").strip().isdigit()

    def as_items(self) -> list[str]:
        """The elements, with an empty count-only array reading as empty."""
        return list(self._items) if self._items is not None else []

    @property
    def dirty(self) -> bool:
        return self.raw is None

    def as_int(self, default: int = 0) -> int:
        try:
            text = (self._value or "").strip()
            return int(text, 16) if text.lower().startswith("0x") else int(text)
        except (TypeError, ValueError):
            return default

    def as_float(self, default: float = 0.0) -> float:
        try:
            return text_to_float(self._value or "")
        except (TypeError, ValueError):
            return default

    def as_bool(self) -> bool:
        return (self._value or "").strip() == "true"

    def as_str(self) -> str:
        """Unquote a string value."""
        text = (self._value or "").strip()
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return text

    def set_float(self, value: float) -> None:
        self.value = float_to_text(value)

    def __repr__(self) -> str:
        body = f"[{len(self._items)}]" if self.is_array else f"={self._value}"
        return f"<Attr {self.name}{body}>"


class Unit:
    __slots__ = ("cls", "name", "attrs", "struct_id", "raw_name", "defn")

    def __init__(self, cls: str, name: str, attrs: list[Attr] | None = None,
                 struct_id: int = 0, raw_name=None, defn=None):
        self.cls = cls
        self.name = name
        self.attrs = attrs if attrs is not None else []
        self.struct_id = struct_id
        self.raw_name = raw_name
        self.defn = defn

    def get(self, name: str) -> Attr | None:
        for a in self.attrs:
            if a.name == name:
                return a
        return None

    def all(self, name: str) -> list[Attr]:
        return [a for a in self.attrs if a.name == name]

    def value(self, name: str, default: str = "") -> str:
        a = self.get(name)
        return default if a is None or a.value is None else a.value

    def int_of(self, name: str, default: int = 0) -> int:
        a = self.get(name)
        return default if a is None else a.as_int(default)

    def float_of(self, name: str, default: float = 0.0) -> float:
        a = self.get(name)
        return default if a is None else a.as_float(default)

    def bool_of(self, name: str, default: bool = False) -> bool:
        a = self.get(name)
        return default if a is None else a.as_bool()

    def items_of(self, name: str) -> list[str]:
        a = self.get(name)
        return list(a.items) if a is not None and a.items is not None else []

    def set(self, name: str, value) -> Attr | None:
        """Set a scalar attribute. Unknown names are ignored: the binary layout
        is fixed by the save's own structure definitions, so a field the save
        does not declare cannot be added."""
        a = self.get(name)
        if a is None:
            return None
        a.value = value
        return a

    def set_float(self, name: str, value: float) -> Attr | None:
        a = self.get(name)
        if a is None:
            return None
        a.set_float(value)
        return a

    def set_items(self, name: str, items: list[str]) -> Attr | None:
        a = self.get(name)
        if a is None:
            return None
        a.items = items
        return a

    def __repr__(self) -> str:
        return f"<Unit {self.cls} : {self.name} ({len(self.attrs)} attrs)>"


class SiiFile:
    """An ordered collection of units plus an index by name.

    `blocks` keeps the original file layout for binary saves: a mix of
    structure definitions (opaque byte blobs) and units.
    """

    def __init__(self, units: list[Unit] | None = None):
        self.units: list[Unit] = units or []
        self.blocks: list = []
        self.version: int = 3
        self.source: bytes | None = None
        self.header: str = ""
        self.trailer: str = ""
        self.kind: str = "binary"
        self._by_name: dict[str, Unit] | None = None

    def reindex(self) -> None:
        self._by_name = {}
        for u in self.units:
            self._by_name.setdefault(u.name, u)

    def by_name(self, name: str) -> Unit | None:
        if self._by_name is None:
            self.reindex()
        return self._by_name.get(name)

    def by_class(self, cls: str) -> list[Unit]:
        return [u for u in self.units if u.cls == cls]

    def first(self, cls: str) -> Unit | None:
        for u in self.units:
            if u.cls == cls:
                return u
        return None

    def follow(self, ref: str) -> Unit | None:
        """Resolve a unit reference such as `bank.player` or `_nameless.1a.2b`."""
        ref = (ref or "").strip()
        if not ref or ref == "null":
            return None
        return self.by_name(ref)

    def add(self, unit: Unit) -> None:
        self.units.append(unit)
        self.blocks.append(unit)
        if self._by_name is not None:
            self._by_name.setdefault(unit.name, unit)

    def remove_many(self, units: list[Unit]) -> int:
        """Take units out of the file. Callers must have dropped every pointer
        to them first — the game refuses a file with a dangling pointer just as
        it refuses one with a stray unit."""
        doomed = {id(u) for u in units}
        if not doomed:
            return 0
        self.units = [u for u in self.units if id(u) not in doomed]
        self.blocks = [b for b in self.blocks if id(b) not in doomed]
        self._by_name = None
        return len(doomed)

    def __len__(self) -> int:
        return len(self.units)
