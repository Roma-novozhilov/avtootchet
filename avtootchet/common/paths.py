"""Где программа хранит настройки, журнал и временные файлы."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "AvtoOtchet"
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_dir() -> Path:
    p = Path(tempfile.gettempdir()) / "AvtoOtchet"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource(rel: str) -> Path:
    """Файл из комплекта программы (работает и в собранном .exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / rel
