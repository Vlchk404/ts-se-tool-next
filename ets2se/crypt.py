"""SII container handling: ScsC decryption and 3nK decoding.

A file found in an ETS2/ATS profile is one of:
  "SiiN"  plain text SII                       -> use as is
  "BSII"  binary SII                           -> ets2se.bsii
  "ScsC"  AES-256-CBC + zlib around one of the above
  "3nK"   byte-rotation obfuscated text SII
"""

from __future__ import annotations

import struct
import zlib

MAGIC_TEXT = b"SiiN"
MAGIC_BINARY = b"BSII"
MAGIC_ENCRYPTED = b"ScsC"
MAGIC_3NK = b"3nK"

# The key SCS ships with the game; public since 2013.
_KEY = bytes(
    (
        0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6,
        0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E, 0xD0, 0xB2,
        0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C,
        0x9E, 0xDF, 0x6B, 0x11, 0x82, 0x5A, 0x5D, 0x0A,
    )
)

HEADER_SIZE = 4 + 32 + 16 + 4  # magic + hmac + iv + uncompressed size


class SiiFormatError(Exception):
    pass


def _aes_cbc_decrypt(iv: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        from .aes_fallback import aes_cbc_decrypt

        return aes_cbc_decrypt(_KEY, iv, data)
    dec = Cipher(algorithms.AES(_KEY), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def decrypt(raw: bytes) -> bytes:
    """ScsC -> inner payload (text or binary SII)."""
    if raw[:4] != MAGIC_ENCRYPTED:
        raise SiiFormatError("not an ScsC file")
    if len(raw) < HEADER_SIZE:
        raise SiiFormatError("truncated ScsC header")
    iv = raw[36:52]
    declared = struct.unpack_from("<I", raw, 52)[0]
    body = raw[HEADER_SIZE:]
    if len(body) % 16:
        body = body[: len(body) - len(body) % 16]
    plain = _aes_cbc_decrypt(iv, body)
    try:
        out = zlib.decompress(plain)
    except zlib.error as exc:
        # Tail padding can upset the inflater when the stream is truncated.
        obj = zlib.decompressobj()
        out = obj.decompress(plain)
        if not out:
            raise SiiFormatError(f"cannot inflate save payload: {exc}") from exc
    if declared and len(out) != declared:
        raise SiiFormatError(
            f"size mismatch: header says {declared}, inflated {len(out)}"
        )
    return out


def decode_3nk(raw: bytes) -> bytes:
    """Undo the 3nK text obfuscation (seeded byte rotation)."""
    if raw[:3] != MAGIC_3NK:
        raise SiiFormatError("not a 3nK file")
    seed = raw[3]
    out = bytearray(len(raw) - 5)
    src = raw[5:]
    for i, b in enumerate(src):
        rotated = ((b << 4) | (b >> 4)) & 0xFF
        out[i] = (rotated - (i + seed)) & 0xFF
    return bytes(out)


def unwrap(raw: bytes) -> tuple[bytes, str]:
    """Return (payload, container) where container is one of
    'plain', 'encrypted', '3nk'. The payload still may be BSII."""
    if raw[:4] == MAGIC_ENCRYPTED:
        return decrypt(raw), "encrypted"
    if raw[:3] == MAGIC_3NK:
        return decode_3nk(raw), "3nk"
    return raw, "plain"


def payload_kind(payload: bytes) -> str:
    """'text' | 'binary'."""
    if payload[:4] == MAGIC_BINARY:
        return "binary"
    if payload[:4] == MAGIC_TEXT:
        return "text"
    raise SiiFormatError(f"unknown SII payload {payload[:8]!r}")
