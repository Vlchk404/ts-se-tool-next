"""Pure-Python AES-256-CBC decryption.

The last resort: used only when neither `ets2se.wincrypt` (Windows CNG) nor
`cryptography` is available, so the editor keeps working on a bare Python
install on any platform.

Implemented with the four inverse T-tables, which fold the inverse S-box and the
inverse MixColumns multiplication into one lookup per byte per round. That is
~57x faster than the textbook byte-at-a-time form — the difference between a
save opening in a second and the window hanging long enough for Windows to offer
to close it.
"""

from __future__ import annotations

import struct

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
         0x6C, 0xD8, 0xAB, 0x4D]

_MASK = 0xFFFFFFFF


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _mul(a: int, b: int) -> int:
    out = 0
    for _ in range(8):
        if b & 1:
            out ^= a
        b >>= 1
        a = _xtime(a)
    return out


def _build_tables():
    td0 = [0] * 256
    td1 = [0] * 256
    td2 = [0] * 256
    td3 = [0] * 256
    for i in range(256):
        s = _INV_SBOX[i]
        a, b, c, d = _mul(s, 14), _mul(s, 9), _mul(s, 13), _mul(s, 11)
        td0[i] = (a << 24) | (b << 16) | (c << 8) | d
        td1[i] = (d << 24) | (a << 16) | (b << 8) | c
        td2[i] = (c << 24) | (d << 16) | (a << 8) | b
        td3[i] = (b << 24) | (c << 16) | (d << 8) | a
    return td0, td1, td2, td3


_TD0, _TD1, _TD2, _TD3 = _build_tables()


def _sub_word(w: int) -> int:
    return ((_SBOX[(w >> 24) & 0xFF] << 24) | (_SBOX[(w >> 16) & 0xFF] << 16) |
            (_SBOX[(w >> 8) & 0xFF] << 8) | _SBOX[w & 0xFF])


def _expand_key(key: bytes):
    """Return (equivalent inverse cipher round keys, round count)."""
    nk = len(key) // 4
    rounds = nk + 6
    w = [struct.unpack_from(">I", key, 4 * i)[0] for i in range(nk)]
    for i in range(nk, 4 * (rounds + 1)):
        t = w[i - 1]
        if i % nk == 0:
            t = ((t << 8) | (t >> 24)) & _MASK
            t = _sub_word(t) ^ (_RCON[i // nk - 1] << 24)
        elif nk > 6 and i % nk == 4:
            t = _sub_word(t)
        w.append(w[i - nk] ^ t)
    # The equivalent inverse cipher wants InvMixColumns applied to every round
    # key except the first and the last, so the round loop stays table-only.
    dk = list(w)
    for r in range(1, rounds):
        for c in range(4):
            k = dk[4 * r + c]
            dk[4 * r + c] = (_TD0[_SBOX[(k >> 24) & 0xFF]] ^
                             _TD1[_SBOX[(k >> 16) & 0xFF]] ^
                             _TD2[_SBOX[(k >> 8) & 0xFF]] ^
                             _TD3[_SBOX[k & 0xFF]])
    return dk, rounds


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Decrypt with no padding; trailing bytes past the last full block are
    ignored, matching the previous implementation."""
    dk, rounds = _expand_key(key)
    blocks = len(data) // 16
    out = bytearray(blocks * 16)
    unpack = struct.Struct(">4I").unpack_from
    pack = struct.Struct(">4I").pack_into
    inv = _INV_SBOX
    td0, td1, td2, td3 = _TD0, _TD1, _TD2, _TD3
    last = 4 * rounds

    p0, p1, p2, p3 = unpack(iv, 0)
    off = 0
    for _ in range(blocks):
        c0, c1, c2, c3 = unpack(data, off)
        s0 = c0 ^ dk[last]
        s1 = c1 ^ dk[last + 1]
        s2 = c2 ^ dk[last + 2]
        s3 = c3 ^ dk[last + 3]
        for r in range(rounds - 1, 0, -1):
            k = 4 * r
            t0 = (td0[(s0 >> 24) & 0xFF] ^ td1[(s3 >> 16) & 0xFF] ^
                  td2[(s2 >> 8) & 0xFF] ^ td3[s1 & 0xFF] ^ dk[k])
            t1 = (td0[(s1 >> 24) & 0xFF] ^ td1[(s0 >> 16) & 0xFF] ^
                  td2[(s3 >> 8) & 0xFF] ^ td3[s2 & 0xFF] ^ dk[k + 1])
            t2 = (td0[(s2 >> 24) & 0xFF] ^ td1[(s1 >> 16) & 0xFF] ^
                  td2[(s0 >> 8) & 0xFF] ^ td3[s3 & 0xFF] ^ dk[k + 2])
            t3 = (td0[(s3 >> 24) & 0xFF] ^ td1[(s2 >> 16) & 0xFF] ^
                  td2[(s1 >> 8) & 0xFF] ^ td3[s0 & 0xFF] ^ dk[k + 3])
            s0, s1, s2, s3 = t0, t1, t2, t3
        # final round: inverse S-box only, no MixColumns
        f0 = ((inv[(s0 >> 24) & 0xFF] << 24) | (inv[(s3 >> 16) & 0xFF] << 16) |
              (inv[(s2 >> 8) & 0xFF] << 8) | inv[s1 & 0xFF])
        f1 = ((inv[(s1 >> 24) & 0xFF] << 24) | (inv[(s0 >> 16) & 0xFF] << 16) |
              (inv[(s3 >> 8) & 0xFF] << 8) | inv[s2 & 0xFF])
        f2 = ((inv[(s2 >> 24) & 0xFF] << 24) | (inv[(s1 >> 16) & 0xFF] << 16) |
              (inv[(s0 >> 8) & 0xFF] << 8) | inv[s3 & 0xFF])
        f3 = ((inv[(s3 >> 24) & 0xFF] << 24) | (inv[(s2 >> 16) & 0xFF] << 16) |
              (inv[(s1 >> 8) & 0xFF] << 8) | inv[s0 & 0xFF])
        pack(out, off,
             f0 ^ dk[0] ^ p0, f1 ^ dk[1] ^ p1, f2 ^ dk[2] ^ p2, f3 ^ dk[3] ^ p3)
        p0, p1, p2, p3 = c0, c1, c2, c3
        off += 16
    return bytes(out)
