"""Даты между Python и 1С через COM без сдвига часового пояса.

При передаче даты-параметра через COM pywin32 переводит её из местного времени в UTC:
период «01.11 00:00 – 04.11 23:59» уходил в 1С как «31.10 21:00 – 04.11 20:59», и оплаты
последнего дня после 21:00 терялись. Поэтому:
* inline_dates() подставляет даты в текст запроса литералом ДАТАВРЕМЯ(...) - параметр
  через COM не передаётся и сдвигаться нечему;
* com_datetime() берёт дату из 1С «как записано» (год, месяц, день, время), отбрасывая
  пометку часового пояса, но не пересчитывая время.
"""
from __future__ import annotations

from datetime import date, datetime


def com_datetime(v) -> datetime:
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
