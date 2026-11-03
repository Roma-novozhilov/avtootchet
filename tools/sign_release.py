"""Подписать собранные .exe (запускается в GitHub Actions).

    python tools/sign_release.py <версия> dist/A.exe dist/B.exe

Закрытый ключ берётся из переменной окружения UPDATE_SIGNING_KEY. Рядом с каждым
файлом появляется <файл>.sig. Подпись сразу проверяется открытым ключом из
update_config.py - если ключи не пара, сборка падает, а не выпускает сломанное обновление.
"""
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from avtootchet import update_config  # noqa: E402
from avtootchet.common.updater import signed_message, verify  # noqa: E402

secret = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
if not secret:
    sys.exit("Нет секрета UPDATE_SIGNING_KEY")
if not update_config.PUBLIC_KEY:
    sys.exit("В update_config.py нет PUBLIC_KEY - запустите tools/make_update_key.py")
key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
version = sys.argv[1].lstrip("v")
for name in sys.argv[2:]:
    f = Path(name)
    data = f.read_bytes()
    sig = key.sign(signed_message(f.name, version, hashlib.sha256(data).hexdigest())).hex()
    verify(f.name, version, data, sig, update_config.PUBLIC_KEY)
    Path(str(f) + ".sig").write_text(sig, encoding="ascii")
    print("подписан", f.name)
