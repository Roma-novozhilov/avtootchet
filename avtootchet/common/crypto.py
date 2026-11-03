"""Шифрование файла для передачи между ПК и надёжное удаление временных файлов.

Схема: общий «код доступа» (вводится в обеих программах) -> scrypt -> ключ AES-256-GCM.
GCM одновременно шифрует и проверяет подлинность: файл с чужим кодом не расшифруется.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"AVR1"


class WrongSecret(Exception):
    pass


def new_secret() -> str:
    """Код доступа вида 'K7QX-M2PA-9DTF-H4WN' - легко продиктовать."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _key(secret: str, salt: bytes) -> bytes:
    norm = secret.strip().upper().replace(" ", "").encode()
    return hashlib.scrypt(norm, salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def seal(secret: str, header: dict, payload: bytes) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    h = json.dumps(header, ensure_ascii=False).encode()
    plain = len(h).to_bytes(4, "big") + h + payload
    return MAGIC + salt + nonce + AESGCM(_key(secret, salt)).encrypt(nonce, plain, MAGIC)


def unseal(secret: str, blob: bytes) -> tuple[dict, bytes]:
    if not blob.startswith(MAGIC) or len(blob) < 4 + 16 + 12 + 16:
        raise WrongSecret("Это не файл АвтоОтчёта")
    salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
    try:
        plain = AESGCM(_key(secret, salt)).decrypt(nonce, ct, MAGIC)
    except InvalidTag:
        raise WrongSecret("Неверный код доступа") from None
    n = int.from_bytes(plain[:4], "big")
    return json.loads(plain[4:4 + n]), plain[4 + n:]


def secure_delete(path: Path) -> None:
    """Перезаписать содержимое и удалить. На SSD перезапись не гарантирует
    полного уничтожения, поэтому программа старается вообще не писать данные на диск."""
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def wipe(buf: bytearray | None) -> None:
    if buf is not None:
        buf[:] = b"\0" * len(buf)
