"""BSII (binary SII) codec.

File layout, versions 1-3:

    "BSII" u32 version
    repeated blocks:
        u32 block_type
        block_type == 0 -> structure definition
            u8  validity (0 ends the file, version >= 2)
            u32 struct_id
            str struct_name
            repeated: u32 value_type (0 ends the list), str value_name
                      types 0x37/0x38 carry an ordinal table
        block_type != 0 -> one data block of that structure
            id  unit name
            values, in declaration order

Every decoded value keeps the byte range it came from, so `encode(decode(x))`
returns `x` unchanged and only edited fields are re-encoded.
"""

from __future__ import annotations

import struct

from .model import Attr, SiiFile, Unit, float_to_text, text_to_float
from .tokens import str_to_token, token_to_str

MAGIC = b"BSII"


class BsiiError(Exception):
    pass


class UnknownValueType(BsiiError):
    def __init__(self, value_type: int, field: str, offset: int):
        super().__init__(
            f"unsupported value type 0x{value_type:02X} for field {field!r} "
            f"at offset 0x{offset:X}"
        )
        self.value_type = value_type
        self.field = field
        self.offset = offset


class _Reader:
    __slots__ = ("d", "p")

    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def u8(self) -> int:
        p = self.p
        self.p = p + 1
        return self.d[p]

    def u16(self) -> int:
        p = self.p
        self.p = p + 2
        return struct.unpack_from("<H", self.d, p)[0]

    def i16(self) -> int:
        p = self.p
        self.p = p + 2
        return struct.unpack_from("<h", self.d, p)[0]

    def u32(self) -> int:
        p = self.p
        self.p = p + 4
        return struct.unpack_from("<I", self.d, p)[0]

    def i32(self) -> int:
        p = self.p
        self.p = p + 4
        return struct.unpack_from("<i", self.d, p)[0]

    def u64(self) -> int:
        p = self.p
        self.p = p + 8
        return struct.unpack_from("<Q", self.d, p)[0]

    def i64(self) -> int:
        p = self.p
        self.p = p + 8
        return struct.unpack_from("<q", self.d, p)[0]

    def f32(self) -> float:
        p = self.p
        self.p = p + 4
        return struct.unpack_from("<f", self.d, p)[0]

    def string(self) -> str:
        n = self.u32()
        p = self.p
        self.p = p + n
        return self.d[p : p + n].decode("utf-8", "surrogateescape")

    @property
    def eof(self) -> bool:
        return self.p >= len(self.d)


# --------------------------------------------------------------------------
# scalar value readers / writers
# --------------------------------------------------------------------------

def _id_text(r: _Reader) -> str:
    """Unit reference: part count then one token per part. 0xFF is the
    anonymous form kept as a single 64-bit value."""
    n = r.u8()
    if n == 0xFF:
        value = r.u64()
        words = [(value >> 48) & 0xFFFF, (value >> 32) & 0xFFFF,
                 (value >> 16) & 0xFFFF, value & 0xFFFF]
        while len(words) > 1 and words[0] == 0:
            words.pop(0)
        return "_nameless." + ".".join("%x" % w for w in words)
    if n == 0:
        return "null"
    return ".".join(token_to_str(r.u64()) for _ in range(n))


def _id_bytes(text: str) -> bytes:
    text = (text or "").strip()
    if not text or text == "null":
        return b"\x00"
    if text.startswith("_nameless."):
        words = [int(w, 16) for w in text[len("_nameless."):].split(".")]
        while len(words) < 4:
            words.insert(0, 0)
        if len(words) > 4:
            raise BsiiError(f"bad nameless id {text!r}")
        value = 0
        for w in words:
            value = (value << 16) | (w & 0xFFFF)
        return b"\xff" + struct.pack("<Q", value)
    parts = text.split(".")
    out = bytes((len(parts),))
    for part in parts:
        out += struct.pack("<Q", str_to_token(part))
    return out


def _string_text(value: str) -> str:
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def _string_bytes(text: str) -> bytes:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    raw = text.encode("utf-8", "surrogateescape")
    return struct.pack("<I", len(raw)) + raw


def _token_text(value: int) -> str:
    return token_to_str(value) if value else '""'


def _token_bytes(text: str) -> bytes:
    text = (text or "").strip()
    if text in ('""', "", "null"):
        return struct.pack("<Q", 0)
    return struct.pack("<Q", str_to_token(text))


def _numbers(text: str) -> list[str]:
    """Split a tuple such as `(&1, &2, &3)|0|(&4; &5, &6, &7)` into pieces."""
    out, cur = [], []
    for ch in text:
        if ch in "(),;|":
            if cur:
                out.append("".join(cur).strip())
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _floats_text(r: _Reader, count: int, sep: str = ", ") -> str:
    return "(" + sep.join(float_to_text(r.f32()) for _ in range(count)) + ")"


def _floats_bytes(text: str, count: int) -> bytes:
    parts = _numbers(text)
    if len(parts) != count:
        raise BsiiError(f"expected {count} numbers in {text!r}")
    return b"".join(struct.pack("<f", text_to_float(p)) for p in parts)


def _f2(r: _Reader) -> str:
    return _floats_text(r, 2)


def _f3(r: _Reader) -> str:
    return _floats_text(r, 3)


def _f4(r: _Reader) -> str:
    a, b, c, d = (float_to_text(r.f32()) for _ in range(4))
    return f"({a}; {b}, {c}, {d})"


def _i3(r: _Reader) -> str:
    return "(%d, %d, %d)" % (r.i32(), r.i32(), r.i32())


def _i3_bytes(text: str) -> bytes:
    parts = _numbers(text)
    if len(parts) != 3:
        raise BsiiError(f"expected 3 integers in {text!r}")
    return b"".join(struct.pack("<i", int(p)) for p in parts)


def _placement(r: _Reader) -> str:
    """32 bytes: sector-relative position (3 floats), the packed sector the
    position belongs to (kept verbatim), then the orientation quaternion."""
    pos = _floats_text(r, 3)
    sector = float_to_text(r.f32())
    rot = _f4(r)
    return f"{pos}|{sector}|{rot}"


def _placement_bytes(text: str) -> bytes:
    parts = _numbers(text)
    if len(parts) != 8:
        raise BsiiError(f"expected 8 numbers in placement {text!r}")
    return b"".join(struct.pack("<f", text_to_float(p)) for p in parts)


# type -> (reader, writer)
_SCALARS = {
    0x01: (lambda r: _string_text(r.string()), _string_bytes),
    0x03: (lambda r: _token_text(r.u64()), _token_bytes),
    0x05: (lambda r: float_to_text(r.f32()),
           lambda t: struct.pack("<f", text_to_float(t))),
    0x07: (_f2, lambda t: _floats_bytes(t, 2)),
    0x09: (_f3, lambda t: _floats_bytes(t, 3)),
    0x11: (_i3, _i3_bytes),
    0x17: (_f4, lambda t: _floats_bytes(t, 4)),
    0x19: (_placement, _placement_bytes),
    0x25: (lambda r: str(r.i32()), lambda t: struct.pack("<i", int(t))),
    0x27: (lambda r: str(r.u32()), lambda t: struct.pack("<I", int(t) & 0xFFFFFFFF)),
    0x29: (lambda r: str(r.i16()), lambda t: struct.pack("<h", int(t))),
    0x2B: (lambda r: str(r.u16()), lambda t: struct.pack("<H", int(t) & 0xFFFF)),
    0x2F: (lambda r: str(r.u32()), lambda t: struct.pack("<I", int(t) & 0xFFFFFFFF)),
    0x31: (lambda r: str(r.i64()), lambda t: struct.pack("<q", int(t))),
    0x33: (lambda r: str(r.u64()),
           lambda t: struct.pack("<Q", int(t) & 0xFFFFFFFFFFFFFFFF)),
    0x35: (lambda r: "true" if r.u8() else "false",
           lambda t: b"\x01" if t.strip() == "true" else b"\x00"),
    0x39: (_id_text, _id_bytes),
    0x3B: (_id_text, _id_bytes),
    0x3D: (_id_text, _id_bytes),
}

# array type -> the scalar type it repeats
_ARRAYS = {
    0x02: 0x01, 0x04: 0x03, 0x06: 0x05, 0x08: 0x07, 0x0A: 0x09,
    0x12: 0x11, 0x18: 0x17, 0x1A: 0x19, 0x26: 0x25, 0x28: 0x27,
    0x2A: 0x29, 0x2C: 0x2B, 0x32: 0x31, 0x34: 0x33, 0x36: 0x35,
    0x3A: 0x39, 0x3C: 0x3B, 0x3E: 0x3D,
}

ORDINAL_TYPE = 0x37
ORDINAL_ARRAY_TYPE = 0x38


class Field:
    __slots__ = ("type", "name", "ordinals", "reverse")

    def __init__(self, type_: int, name: str, ordinals: dict[int, str] | None = None):
        self.type = type_
        self.name = name
        self.ordinals = ordinals
        self.reverse = ({v: k for k, v in ordinals.items()} if ordinals else None)

    @property
    def is_array(self) -> bool:
        return (self.type in _ARRAYS) or self.type == ORDINAL_ARRAY_TYPE


class StructDef:
    __slots__ = ("struct_id", "name", "fields", "raw")

    def __init__(self, struct_id: int, name: str, fields: list[Field], raw: bytes):
        self.struct_id = struct_id
        self.name = name
        self.fields = fields
        self.raw = raw


def _ordinal_text(field: Field, index: int) -> str:
    if field.ordinals and index in field.ordinals:
        return field.ordinals[index]
    return str(index)


def _ordinal_index(field: Field, text: str) -> int:
    text = text.strip()
    if field.reverse and text in field.reverse:
        return field.reverse[text]
    return int(text)


def _read_value(r: _Reader, field: Field) -> Attr:
    start = r.p
    t = field.type
    if t == ORDINAL_TYPE:
        attr = Attr(field.name, _ordinal_text(field, r.u32()), field=field)
    elif t == ORDINAL_ARRAY_TYPE:
        count = r.u32()
        attr = Attr(field.name, None,
                    [_ordinal_text(field, r.u32()) for _ in range(count)],
                    field=field)
    elif t in _SCALARS:
        attr = Attr(field.name, _SCALARS[t][0](r), field=field)
    elif t in _ARRAYS:
        reader = _SCALARS[_ARRAYS[t]][0]
        count = r.u32()
        attr = Attr(field.name, None, [reader(r) for _ in range(count)], field=field)
    else:
        raise UnknownValueType(t, field.name, start)
    attr.raw = r.d[start : r.p]
    return attr


def _write_value(attr: Attr, field: Field) -> bytes:
    if attr.raw is not None:
        return attr.raw
    t = field.type
    if t == ORDINAL_TYPE:
        return struct.pack("<I", _ordinal_index(field, attr.value or "0"))
    if t == ORDINAL_ARRAY_TYPE:
        items = attr.items or []
        return struct.pack("<I", len(items)) + b"".join(
            struct.pack("<I", _ordinal_index(field, i)) for i in items
        )
    if t in _SCALARS:
        return _SCALARS[t][1](attr.value or "")
    if t in _ARRAYS:
        writer = _SCALARS[_ARRAYS[t]][1]
        items = attr.items or []
        return struct.pack("<I", len(items)) + b"".join(writer(i) for i in items)
    raise UnknownValueType(t, field.name, 0)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def decode(data: bytes) -> SiiFile:
    r = _Reader(data)
    if data[:4] != MAGIC:
        raise BsiiError("not a BSII payload")
    r.p = 4
    version = r.u32()
    if version not in (1, 2, 3):
        raise BsiiError(f"unsupported BSII version {version}")

    out = SiiFile()
    out.kind = "binary"
    out.version = version
    structs: dict[int, StructDef] = {}

    while not r.eof:
        start = r.p
        block = r.u32()
        if block == 0:
            if version >= 2:
                if r.u8() == 0:
                    out.trailer = data[start:]
                    break
            struct_id = r.u32()
            name = r.string()
            fields: list[Field] = []
            while True:
                vtype = r.u32()
                if vtype == 0:
                    break
                vname = r.string()
                ordinals = None
                if vtype in (ORDINAL_TYPE, ORDINAL_ARRAY_TYPE):
                    ordinals = {}
                    for _ in range(r.u32()):
                        key = r.u32()
                        ordinals[key] = r.string()
                fields.append(Field(vtype, vname, ordinals))
            sdef = StructDef(struct_id, name, fields, data[start : r.p])
            structs[struct_id] = sdef
            out.blocks.append(sdef)
            continue

        sdef = structs.get(block)
        if sdef is None:
            raise BsiiError(
                f"data block references unknown structure {block} "
                f"at offset 0x{start:X}"
            )
        id_start = r.p
        unit = Unit(sdef.name, _id_text(r), struct_id=block, defn=sdef)
        unit.raw_name = data[id_start : r.p]
        for field in sdef.fields:
            unit.attrs.append(_read_value(r, field))
        out.units.append(unit)
        out.blocks.append(unit)

    out.source = data
    return out


def encode(file: SiiFile) -> bytes:
    parts = [MAGIC, struct.pack("<I", file.version)]
    for block in file.blocks:
        if isinstance(block, StructDef):
            parts.append(block.raw)
            continue
        unit: Unit = block
        parts.append(struct.pack("<I", unit.struct_id))
        parts.append(unit.raw_name if unit.raw_name is not None
                     else _id_bytes(unit.name))
        defn = unit.defn
        for attr in unit.attrs:
            field = attr.field or (defn.fields[unit.attrs.index(attr)] if defn else None)
            if field is None:
                raise BsiiError(f"no field definition for {unit.cls}.{attr.name}")
            parts.append(_write_value(attr, field))
    if file.trailer:
        parts.append(file.trailer)
    elif file.version >= 2:
        parts.append(b"\x00\x00\x00\x00\x00")
    return b"".join(parts)
