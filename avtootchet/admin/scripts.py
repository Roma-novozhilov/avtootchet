"""Библиотека скриптов администратора.

- «Мои скрипты» - папка на сервере (по умолчанию C:\\avtootchet\\admin\\scripts): сюда копируются
  добавленные файлы и сохраняется вставленный текст. Удаляются только эти копии.
- «Встроенные» - скрипты из папки tools, которые идут в комплекте с программой (только чтение).
- Каждый запуск пишется в отдельный журнал (C:\\avtootchet\\admin\\logs).
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..common.paths import resource

KINDS = {".ps1": "PowerShell", ".cmd": "командный файл", ".bat": "командный файл"}
BUILTIN_HINTS = {
    "check_server.ps1": "Проверка сервера: диски, память, службы (только чтение)",
    "fix_1c_client_rights.ps1": "Права на настройки клиента 1С для всех пользователей",
    "setup_perf_log.ps1": "Сборщик счётчиков производительности",
    "setup_power_schedule.ps1": "Питание: день — высокая производительность, ночь — экономия, без сна",
    "setup_stock_task.ps1": "Задача утреннего отчёта по складу",
    "setup_techlog.ps1": "Технологический журнал 1С (-Remove — выключить)",
    "setup_weekly_reboot.ps1": "Плановая перезагрузка раз в неделю (-Test — проверка)",
    "setup_vitrina.ps1": "Витрина маркетолога: служба и задачи",
    "pg_readiness.ps1": "Готовность сервера к PostgreSQL (только чтение)",
}


@dataclass
class Script:
    path: Path
    builtin: bool

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def kind(self) -> str:
        return KINDS.get(self.path.suffix.lower(), "")

    @property
    def hint(self) -> str:
        if self.builtin:
            return BUILTIN_HINTS.get(self.name, "")
        return first_comment(self.path)


def first_comment(path: Path) -> str:
    """Первая строка-комментарий скрипта - как описание."""
    try:
        for ln in read_text(path).splitlines()[:15]:
            s = ln.strip()
            if s.startswith(("#", "::", "REM ", "rem ")) and len(s) > 2 and not s.startswith("#Requires"):
                return s.lstrip("#:").replace("REM ", "").replace("rem ", "").strip()
    except OSError:
        pass
    return ""


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "cp866"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def builtin_dir() -> Path:
    return resource("tools")


class Library:
    def __init__(self, folder: str | Path, logs: str | Path):
        self.folder = Path(folder)
        self.logs = Path(logs)

    def ensure(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

    def scripts(self) -> list[Script]:
        mine = sorted((p for p in self.folder.glob("*") if p.suffix.lower() in KINDS),
                      key=lambda p: p.name.lower()) if self.folder.exists() else []
        bdir = builtin_dir()
        built = sorted((p for p in bdir.glob("*") if p.suffix.lower() in KINDS),
                       key=lambda p: p.name.lower()) if bdir.exists() else []
        return [Script(p, False) for p in mine] + [Script(p, True) for p in built]

    def add_file(self, src: str | Path, overwrite: bool = False) -> Path:
        src = Path(src)
        if src.suffix.lower() not in KINDS:
            raise ValueError("Поддерживаются только .ps1, .cmd и .bat")
        self.ensure()
        dst = self.folder / src.name
        if dst.exists() and not overwrite:
            raise FileExistsError(dst.name)
        shutil.copy2(src, dst)
        return dst

    def save_text(self, name: str, text: str, kind: str = ".ps1", overwrite: bool = False) -> Path:
        name = safe_name(name)
        if not name:
            raise ValueError("Укажите название скрипта")
        if not Path(name).suffix.lower() in KINDS:
            name += kind
        if not text.strip():
            raise ValueError("Текст скрипта пустой")
        self.ensure()
        dst = self.folder / name
        if dst.exists() and not overwrite:
            raise FileExistsError(dst.name)
        enc = "utf-8-sig" if dst.suffix.lower() == ".ps1" else "cp866"
        # PowerShell 5.1 читает файл без BOM как ANSI - русский текст сломается, поэтому BOM;
        # командные файлы - в кодировке консоли
        dst.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode(enc, "replace"))
        return dst

    def remove(self, script: Script) -> None:
        if script.builtin:
            raise PermissionError("Встроенные скрипты не удаляются")
        if script.path.resolve().parent != self.folder.resolve():
            raise PermissionError("Удалять можно только скрипты из своей папки")
        script.path.unlink()

    def log_path(self, script: Script, when: datetime | None = None) -> Path:
        when = when or datetime.now()
        self.ensure()
        return self.logs / f"{when:%Y-%m-%d_%H%M%S} {script.path.stem}.log"


_BAD = re.compile(r'[\\/:*?"<>|]+')


def safe_name(name: str) -> str:
    return _BAD.sub("_", name.strip()).strip(". ")


def command(script: Script, args: str = "") -> tuple[str, list[str]]:
    """Программа и аргументы для запуска. Вывод PowerShell - в UTF-8."""
    p = str(script.path)
    if script.path.suffix.lower() == ".ps1":
        quoted = p.replace("'", "''")
        body = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; & '{quoted}' {args}".rstrip()
        return "powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", body]
    return "cmd.exe", ["/c", f'"{p}" {args}'.rstrip()]


def output_encoding(script: Script) -> str:
    return "utf-8" if script.path.suffix.lower() == ".ps1" else "cp866"
