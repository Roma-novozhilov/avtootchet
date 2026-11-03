"""Создать ключ подписи обновлений. Запускается ОДИН раз, на своём компьютере.

    python tools\\make_update_key.py

* открытый ключ записывается в avtootchet/update_config.py (его можно публиковать);
* закрытый ключ сохраняется в файл avtootchet_update_key.txt в папке пользователя,
  ВНЕ проекта. Его нужно добавить в GitHub: Settings -> Secrets and variables -> Actions ->
  New repository secret, имя UPDATE_SIGNING_KEY. После этого файл с ключом лучше
  перенести на флешку или в менеджер паролей и удалить с диска.
"""
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "avtootchet" / "update_config.py"
KEY_FILE = Path.home() / "avtootchet_update_key.txt"

text = CONFIG.read_text(encoding="utf-8")
if re.search(r'^PUBLIC_KEY = "[0-9a-f]{64}"', text, re.M) and "--force" not in sys.argv:
    sys.exit("Ключ уже создан. Новый ключ сломает обновление у уже установленных программ.\n"
             "Если это точно нужно: python tools\\make_update_key.py --force")
if KEY_FILE.exists() and "--force" not in sys.argv:
    sys.exit(f"Файл {KEY_FILE} уже есть - не перезаписываю.")

key = Ed25519PrivateKey.generate()
private_hex = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption()).hex()
public_hex = key.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw).hex()
CONFIG.write_text(re.sub(r'^PUBLIC_KEY = ".*"$', f'PUBLIC_KEY = "{public_hex}"', text, flags=re.M),
                  encoding="utf-8")
KEY_FILE.write_text(private_hex + "\n", encoding="utf-8")
print("Открытый ключ записан в", CONFIG)
print("Закрытый ключ сохранён в", KEY_FILE)
print("Добавьте содержимое этого файла в GitHub как секрет UPDATE_SIGNING_KEY.")
print("Никому его не показывайте и не коммитьте.")
