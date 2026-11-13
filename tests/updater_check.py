"""Проверка обновления без интернета: поддельный GitHub на 127.0.0.1.

    python tests/updater_check.py
"""
import hashlib
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from avtootchet import __version__  # noqa: E402
from avtootchet.common import updater  # noqa: E402

# --- версии
assert updater.is_newer("0.2.0", "0.1.9")
assert updater.is_newer("v1.0", "0.9.9")
assert updater.is_newer("0.1.10", "0.1.9")
assert not updater.is_newer("0.1.0", "0.1.0")
assert not updater.is_newer("0.1", "0.1.0")
assert not updater.is_newer("0.0.9", "0.1.0")

# --- подпись
key = Ed25519PrivateKey.generate()
pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
NAME, NEW = "AvtoOtchet-Vladelec.exe", "99.0.0"
exe = b"MZ new version " * 1000


def sign(name, version, data):
    return key.sign(updater.signed_message(name, version, hashlib.sha256(data).hexdigest())).hex()


good = sign(NAME, NEW, exe)
updater.verify(NAME, NEW, exe, good, pub)
for bad in ((NAME, NEW, exe + b"x", good),                               # файл изменён
            ("AvtoOtchet-Buhgalter.exe", NEW, exe, good),                # файл другой программы
            (NAME, "98.0.0", exe, good),                                 # другая версия
            (NAME, NEW, exe, Ed25519PrivateKey.generate().sign(b"x").hex())):  # чужой ключ
    try:
        updater.verify(*bad, pub)
        raise AssertionError("подпись должна быть отклонена")
    except updater.UpdateError:
        pass
print("подпись: OK")

# --- переименование AvtoOtchet-* -> ACMineevo-*: старый .exe ищет в релизе новое имя
assert updater.release_name("AvtoOtchet-Vladelec.exe") == "ACMineevo-Vladelec.exe"
assert updater.release_name("avtootchet-marketing.exe") == "ACMineevo-Marketing.exe"
assert updater.release_name("ACMineevo-Vitrina.exe") == "ACMineevo-Vitrina.exe"
assert updater.release_name("Другое.exe") == "Другое.exe"
print("старые имена: OK")

# --- поддельный GitHub
files = {"/exe": exe, "/sig": good.encode()}
release = {"tag_name": "v" + NEW, "body": "Тест", "draft": False, "prerelease": False,
           "assets": [{"name": NAME, "size": len(exe), "browser_download_url": None},
                      {"name": NAME + ".sig", "size": 128, "browser_download_url": None}]}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(release).encode() if self.path.endswith("/latest") else files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
release["assets"][0]["browser_download_url"] = base + "/exe"
release["assets"][1]["browser_download_url"] = base + "/sig"
updater.API = base + "/repos/{repo}/releases/latest"

rel = updater.check("x/y", NAME)
assert rel and rel.version == NEW and rel.notes == "Тест", rel

work = Path(tempfile.mkdtemp())
cur = work / NAME
cur.write_bytes(b"MZ old version")
new = updater.download(rel, pub, target_dir=work)
assert new.read_bytes() == exe

# подменённый файл не скачивается
files["/exe"] = exe + b"!"
release["assets"][0]["size"] = len(exe) + 1
try:
    updater.download(updater.check("x/y", NAME), pub, target_dir=work / "nope")
    raise AssertionError("подменённый файл должен быть отклонён")
except updater.UpdateError as e:
    print("подмена ->", e)
files["/exe"] = exe

# нет новой версии
release["tag_name"] = "v" + __version__
assert updater.check("x/y", NAME) is None
release["tag_name"] = "v" + NEW

# нет файла для этой программы
try:
    updater.check("x/y", "AvtoOtchet-Buhgalter.exe")
    raise AssertionError("должна быть ошибка про отсутствующий файл")
except updater.UpdateError:
    pass

# --- замена файла и уборка после перезапуска
updater.install(new, exe=cur, launch=False)
assert cur.read_bytes() == exe
assert (work / (NAME + ".old")).read_bytes() == b"MZ old version"
assert not new.exists()

updater.sys.frozen = True
updater.sys.executable = str(cur)
argv = ["prog", updater.WAIT_ARG, "999999"]
assert updater.after_start(argv) is True and argv == ["prog"]
assert not (work / (NAME + ".old")).exists(), ".old должен удалиться"
assert updater.after_start(["prog"]) is False
srv.shutdown()
print("OK: обновление проверено")
