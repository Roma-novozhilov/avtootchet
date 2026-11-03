"""Журнал действий. Пишутся только события и количества - без сумм, ФИО и номеров."""
from __future__ import annotations

from datetime import datetime

from .paths import app_dir


class Journal:
    def __init__(self, name: str):
        self.path = app_dir() / f"journal_{name}.log"

    def write(self, action: str, details: str = "") -> str:
        line = f"{datetime.now():%d.%m.%Y %H:%M:%S} | {action}" + (f" | {details}" if details else "")
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        return line

    def tail(self, n: int = 300) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()[-n:]
        except OSError:
            return []
