"""SCS token codec.

An "encoded string" in SII files is a 64-bit value holding up to 12 characters
of the alphabet below, packed as little-endian base-38 digits (0 = end).
"""

CHARSET = "0123456789abcdefghijklmnopqrstuvwxyz_"
_INDEX = {c: i + 1 for i, c in enumerate(CHARSET)}


def token_to_str(value: int) -> str:
    out = []
    v = value & 0xFFFFFFFFFFFFFFFF
    for _ in range(12):
        if v == 0:
            break
        digit = v % 38
        v //= 38
        if digit == 0 or digit > len(CHARSET):
            break
        out.append(CHARSET[digit - 1])
    return "".join(out)


def str_to_token(text: str) -> int:
    if len(text) > 12:
        raise ValueError(f"token too long: {text!r}")
    value = 0
    for ch in reversed(text.lower()):
        idx = _INDEX.get(ch)
        if idx is None:
            raise ValueError(f"character {ch!r} cannot be part of a token")
        value = value * 38 + idx
    return value


def is_token(text: str) -> bool:
    return bool(text) and len(text) <= 12 and all(c in _INDEX for c in text)
