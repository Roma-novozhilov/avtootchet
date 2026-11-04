"""Настройки программ. Код доступа и пароль 1С хранятся зашифрованными
средствами Windows (DPAPI): расшифровать их может только этот пользователь Windows."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from copy import deepcopy
from pathlib import Path

from .paths import app_dir

SECRET_FIELDS = {"secret", "password", "report_password"}


# ------------------------------------------------------------------ DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI error")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect(value: str) -> str:
    if not value:
        return ""
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(value.encode(), True)).decode()
    return "plain:" + base64.b64encode(value.encode()).decode()


def unprotect(value: str) -> str:
    if not value:
        return ""
    kind, _, data = value.partition(":")
    raw = base64.b64decode(data)
    if kind == "dpapi":
        try:
            return _dpapi(raw, False).decode()
        except OSError:
            return ""  # настройки скопированы с другого ПК/пользователя
    return raw.decode()


# ------------------------------------------------------------------ файл
def _walk(d: dict, fn) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _walk(v, fn)
        elif k in SECRET_FIELDS and isinstance(v, str):
            out[k] = fn(v)
        else:
            out[k] = v
    return out


def _merge(defaults: dict, loaded: dict) -> dict:
    out = deepcopy(defaults)
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, name: str, defaults: dict):
        self.path: Path = app_dir() / f"{name}_settings.json"
        self.defaults = defaults
        self.data = deepcopy(defaults)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = _merge(defaults, _walk(loaded, unprotect))
            except (ValueError, OSError):
                pass

    def save(self) -> None:
        self.path.write_text(json.dumps(_walk(self.data, protect), ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
