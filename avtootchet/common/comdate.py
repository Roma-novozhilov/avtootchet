"""Даты между Python и 1С через COM без сдвига часового пояса.

pywin32 считает дату COM (VT_DATE) местным временем и отдаёт её в UTC с пометкой
tzinfo: полночь 02.11 по Москве приходит как 01.11 21:00 UTC, и день «съезжает» назад.
* com_datetime() возвращает местное время без пояса - как оно записано в 1С;
* inline_dates() подставляет даты в текст запроса литералом ДАТАВРЕМЯ(...), чтобы
  при передаче параметра через COM дата тоже не могла сдвинуться.
"""
from __future__ import annotations

from datetime import date, datetime


def com_datetime(v) -> datetime:
    if getattr(v, "tzinfo", None) is not None:
        v = v.astimezone()          # в местный пояс этого компьютера (как в 1С)
    return datetime(v.year, v.month, v.day, getattr(v, "hour", 0), getattr(v, "minute", 0),
                    getattr(v, "second", 0))


def literal(d: date | datetime) -> str:
    if isinstance(d, datetime):
        return f"ДАТАВРЕМЯ({d.year}, {d.month}, {d.day}, {d.hour}, {d.minute}, {d.second})"
    return f"ДАТАВРЕМЯ({d.year}, {d.month}, {d.day})"


def inline_dates(text: str, params: dict) -> tuple[str, dict]:
    """Параметры-даты -> литералы в тексте запроса. Возвращает (текст, остальные параметры)."""
    rest = {}
    for k, v in sorted(params.items(), key=lambda kv: -len(kv[0])):   # «Начало» раньше «Нач…»
        if isinstance(v, (date, datetime)):
            text = text.replace(f"&{k}", literal(v))
        else:
            rest[k] = v
    return text, rest
