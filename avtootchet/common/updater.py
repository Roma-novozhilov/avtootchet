"""Обновление программы из GitHub Releases.

Как это устроено:
1. Программа спрашивает у GitHub последний релиз репозитория REPO (update_config.py).
2. Если его версия новее, скачивает свой .exe и файл подписи .exe.sig.
3. Подпись проверяется открытым ключом, зашитым в программу. Подписать файл может только
   тот, у кого есть закрытый ключ (секрет в GitHub Actions). Файл с неверной подписью,
   от другой программы или другой версии не ставится.
4. Работающий .exe переименовывается в .old, новый встаёт на его место и запускается.
   Новый процесс ждёт, пока старый закроется, и удаляет .old.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import __version__
from .. import update_config

API = "https://api.github.com/repos/{repo}/releases/latest"
MAX_SIZE = 300 * 1024 * 1024
USER_AGENT = "AvtoOtchet-updater"
WAIT_ARG = "--after-update"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    notes: str
    exe_url: str
    sig_url: str
    asset: str
    size: int


# ------------------------------------------------------------------ версии
def parse_version(text: str) -> tuple[int, ...]:
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", text.strip())
    if not m:
        raise UpdateError(f"Непонятная версия «{text}»")
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(remote: str, local: str = __version__) -> bool:
    a, b = parse_version(remote), parse_version(local)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ------------------------------------------------------------------ подпись
def signed_message(asset: str, version: str, sha256_hex: str) -> bytes:
    """То, что подписывается: имя файла, версия и хеш. Подпись от одной программы или
    версии не подойдёт к другой."""
    return f"avtootchet-update\n{asset}\n{version}\n{sha256_hex}".encode()


def verify(asset: str, version: str, data: bytes, sig_hex: str, public_key_hex: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(bytes.fromhex(sig_hex.strip()),
                   signed_message(asset, version, hashlib.sha256(data).hexdigest()))
    except (InvalidSignature, ValueError):
        raise UpdateError("Подпись обновления не совпала. Файл не установлен: он мог быть "
                          "повреждён или подменён.") from None


# ------------------------------------------------------------------ где мы
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def asset_name() -> str:
    return current_exe().name if is_frozen() else ""


def available() -> str | None:
    """None, если обновляться можно, иначе причина, почему нельзя."""
    if not update_config.PUBLIC_KEY:
        return "Обновления ещё не настроены: в программе нет ключа подписи."
    if not is_frozen():
        return "Обновление работает только в собранной программе (.exe)."
    return None


# ------------------------------------------------------------------ сеть
def _get(url: str, accept: str, timeout: float = 30) -> urllib.request.addinfourl:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("На GitHub нет опубликованных версий (или репозиторий закрыт).") from None
        if e.code == 403:
            raise UpdateError("GitHub временно ограничил запросы. Попробуйте через час.") from None
        raise UpdateError(f"GitHub ответил ошибкой {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("Нет связи с GitHub. Проверьте интернет на этом компьютере.") from None


def check(repo: str | None = None, name: str | None = None) -> Release | None:
    """Последний релиз, если он новее текущей версии, иначе None."""
    repo = repo or update_config.REPO
    name = name or asset_name()
    with _get(API.format(repo=repo), "application/vnd.github+json") as r:
        info = json.loads(r.read())
    version = str(info.get("tag_name", "")).lstrip("v")
    if not version or info.get("draft") or info.get("prerelease"):
        return None
    if not is_newer(version):
        return None
    assets = {a["name"]: a for a in info.get("assets", [])}
    exe, sig = assets.get(name), assets.get(name + ".sig")
    if not exe or not sig:
        raise UpdateError(f"В версии {version} нет файла {name} или его подписи.")
    return Release(version=version, notes=(info.get("body") or "").strip(),
                   exe_url=exe["browser_download_url"], sig_url=sig["browser_download_url"],
                   asset=name, size=int(exe.get("size", 0)))


def download(rel: Release, public_key: str | None = None,
             target_dir: Path | None = None,
             progress: Callable[[int, int], None] | None = None) -> Path:
    """Скачать и проверить новый .exe. Возвращает путь к файлу рядом с текущей программой."""
    public_key = public_key or update_config.PUBLIC_KEY
    if rel.size > MAX_SIZE:
        raise UpdateError("Файл обновления слишком большой.")
    with _get(rel.sig_url, "application/octet-stream") as r:
        sig = r.read(4096).decode("ascii", "replace")
    buf = bytearray()
    with _get(rel.exe_url, "application/octet-stream", timeout=60) as r:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_SIZE:
                raise UpdateError("Файл обновления слишком большой.")
            if progress:
                progress(len(buf), rel.size)
    data = bytes(buf)
    verify(rel.asset, rel.version, data, sig, public_key)
    target_dir = target_dir or current_exe().parent
    new = target_dir / (rel.asset + ".new")
    try:
        new.write_bytes(data)
    except OSError:
        raise UpdateError(f"Нет прав на запись в папку {target_dir}. Переложите программу в папку, "
                          "где у этого пользователя есть права (например, C:\\AvtoOtchet).") from None
    return new


# ------------------------------------------------------------------ замена
def _old_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".old")


def install(new: Path, exe: Path | None = None, launch: bool = True) -> None:
    """Поставить новый файл на место текущего и запустить его. После вызова программу
    нужно закрыть - новая версия дождётся этого сама."""
    exe = exe or current_exe()
    old = _old_path(exe)
    try:
        if old.exists():
            old.unlink()
        exe.rename(old)          # работающий .exe Windows переименовать разрешает
    except OSError:
        new.unlink(missing_ok=True)
        raise UpdateError("Не удалось заменить файл программы. Закройте другие её копии "
                          "и попробуйте ещё раз.") from None
    try:
        new.rename(exe)
    except OSError:
        old.rename(exe)          # вернуть как было
        raise UpdateError("Не удалось поставить новую версию, оставлена текущая.") from None
    if launch:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("_PYI") and k != "_MEIPASS2"}
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # новый .exe распакуется заново, а не в нашу папку
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # DETACHED | NEW_GROUP
        subprocess.Popen([str(exe), WAIT_ARG, str(os.getpid())], env=env, close_fds=True,
                         creationflags=flags, cwd=str(exe.parent))


def wait_for_pid(pid: int, timeout_ms: int = 20000) -> None:
    if sys.platform != "win32":
        return
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if h:
        k32.WaitForSingleObject(h, timeout_ms)
        k32.CloseHandle(h)


def after_start(argv: list[str] | None = None) -> bool:
    """Вызвать в начале main(). Если программу запустило обновление - дождаться закрытия
    старой версии (иначе порты приёма ещё заняты) и убрать .old. True = только что обновились."""
    argv = sys.argv if argv is None else argv
    updated = False
    if WAIT_ARG in argv:
        i = argv.index(WAIT_ARG)
        try:
            wait_for_pid(int(argv[i + 1]))
        except (IndexError, ValueError):
            pass
        del argv[i:i + 2]
        updated = True
    if is_frozen():
        exe = current_exe()
        for p in (_old_path(exe), exe.with_name(exe.name + ".new")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    return updated
