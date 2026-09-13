"""AES-256-CBC through the Windows CNG API (bcrypt.dll).

Windows has shipped a hardware-accelerated AES since Vista, and `bcrypt.dll` is
part of the OS, so this needs no install and no third-party package — which is
the whole point: a clean Windows 10 box has Python and nothing else, and the
pure-Python fallback is thousands of times slower.

Unavailable (ImportError-like `None` from `decryptor`) on anything but Windows.
"""

from __future__ import annotations

import sys

_STATUS_SUCCESS = 0
_CACHED = None  # (algorithm handle, ctypes module) once opened, or False


class CngError(Exception):
    pass


def _load():
    """Open (once) the CNG AES provider in CBC mode. Returns None if unusable."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED or None
    if not sys.platform.startswith("win"):
        _CACHED = False
        return None
    try:
        import ctypes
        from ctypes import wintypes

        bcrypt = ctypes.WinDLL("bcrypt")
        alg = ctypes.c_void_p()
        status = bcrypt.BCryptOpenAlgorithmProvider(
            ctypes.byref(alg), "AES", None, 0)
        if status != _STATUS_SUCCESS:
            _CACHED = False
            return None
        mode = "ChainingModeCBC"
        buf = ctypes.create_unicode_buffer(mode)
        status = bcrypt.BCryptSetProperty(
            alg, "ChainingMode", buf, (len(mode) + 1) * 2, 0)
        if status != _STATUS_SUCCESS:
            bcrypt.BCryptCloseAlgorithmProvider(alg, 0)
            _CACHED = False
            return None
        _CACHED = (bcrypt, alg, ctypes, wintypes)
    except (ImportError, OSError, AttributeError):
        _CACHED = False
        return None
    return _CACHED


def available() -> bool:
    return _load() is not None


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Decrypt with no padding.

    Bytes past the last whole block are ignored, which is what the other two
    backends do — CNG itself would refuse the call, and the three must stay
    interchangeable.
    """
    loaded = _load()
    if loaded is None:
        raise CngError("CNG unavailable")
    bcrypt, alg, ctypes, wintypes = loaded

    if len(data) % 16:
        data = data[:len(data) - len(data) % 16]
    if not data:
        return b""

    hkey = ctypes.c_void_p()
    status = bcrypt.BCryptGenerateSymmetricKey(
        alg, ctypes.byref(hkey), None, 0, key, len(key), 0)
    if status != _STATUS_SUCCESS:
        raise CngError("BCryptGenerateSymmetricKey -> 0x%08X" % (status & 0xFFFFFFFF))
    try:
        # CNG overwrites the IV buffer it is given, so hand it a copy.
        iv_buf = ctypes.create_string_buffer(iv, len(iv))
        out = ctypes.create_string_buffer(len(data))
        done = wintypes.ULONG(0)
        status = bcrypt.BCryptDecrypt(
            hkey, data, len(data), None, iv_buf, len(iv),
            out, len(data), ctypes.byref(done), 0)
        if status != _STATUS_SUCCESS:
            raise CngError("BCryptDecrypt -> 0x%08X" % (status & 0xFFFFFFFF))
        return out.raw[:done.value]
    finally:
        bcrypt.BCryptDestroyKey(hkey)
