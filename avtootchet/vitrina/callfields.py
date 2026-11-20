"""Что Далион на самом деле пишет в звонок: заполненность каждого поля «Истории звонков».

Нужно для инструкции менеджерам: прежде чем требовать «записывайте итог разговора» или строить
ИИ-разбор записей, надо знать, какие поля в звонке есть и заполняются ли они (запись разговора,
комментарий, клиент, пользователь...). Поля берутся из метаданных регистра, поэтому заранее
названия знать не нужно. Читаются только КОЛИЧЕСТВА - ни телефонов, ни текстов, ни имён.
"""
from __future__ import annotations

from datetime import datetime

from ..common.comdate import inline_dates
from ..owner.stock_probe import _call, _items, _prop

REGISTER = "пз_ИсторияЗвонков"
COLS = ("name", "title", "kind", "filled", "total")


def fill_condition(field: str, type_text: str, kinds: list[str]) -> str:
    """Условие «поле заполнено» для языка запросов 1С по типу поля."""
    t = (type_text or "").lower()
    f = f"Т.{field}"
    if kinds:                                   # ссылка (может быть составной)
        parts = [f"{f} <> НЕОПРЕДЕЛЕНО"]
        parts += [f"{f} <> ЗНАЧЕНИЕ({k}.ПустаяСсылка)" for k in kinds]
        return " И ".join(parts)
    if "строка" in t or "string" in t:          # ПОДСТРОКА - чтобы работало и для неограниченных строк
        return f"ПОДСТРОКА({f}, 1, 100) <> \"\""
    if "число" in t or "number" in t:
        return f"{f} <> 0"
    if "дата" in t or "date" in t:
        return f"{f} > ДАТАВРЕМЯ(1, 1, 2)"
    if "булево" in t or "boolean" in t:
        return f"{f} = ИСТИНА"
    return f"{f} <> НЕОПРЕДЕЛЕНО И НЕ {f} ЕСТЬ NULL"


def kind_of(type_text: str, kinds: list[str]) -> str:
    if kinds:
        return "ссылка: " + ", ".join(k.split(".", 1)[-1] for k in kinds[:3])
    return (type_text or "?").split("(")[0].strip()


def _text(conn, v) -> str:
    try:
        return str(conn.String(v))
    except Exception:  # noqa: BLE001
        return str(v)


def _ref_kinds(conn, attr) -> list[str]:
    out = []
    try:
        types = _items(_call(_prop(attr, "Type", "Тип"), "Types", "Типы"))
    except AttributeError:
        return out
    md = _prop(conn, "Metadata", "Метаданные")
    for t in types:
        try:
            m = _call(md, "FindByType", "НайтиПоТипу", args=(t,))
            if m is not None:
                out.append(str(_call(m, "FullName", "ПолноеИмя")))
        except Exception:  # noqa: BLE001
            continue
    return out


def _fields(reg) -> list:
    out = []
    for en, ru in (("Dimensions", "Измерения"), ("Resources", "Ресурсы"), ("Attributes", "Реквизиты")):
        try:
            out += _items(_prop(reg, en, ru))
        except AttributeError:
            continue
    return out


def collect_call_fields(conn, start: datetime, end: datetime) -> list[dict]:
    """Строка на каждое поле регистра: имя, синоним, тип, сколько звонков за период его заполнили."""
    md = _prop(conn, "Metadata", "Метаданные")
    reg = _call(_prop(md, "InformationRegisters", "РегистрыСведений"), "Find", "Найти", args=(REGISTER,))
    if reg is None:
        raise RuntimeError(f"в Далионе нет регистра {REGISTER}")
    period = "Т.ДатаВремяНачала МЕЖДУ &Начало И &Конец"

    def count(cond: str = "") -> int:
        q = conn.NewObject("Запрос")
        where = f"{period} И ({cond})" if cond else period
        q.Text, _ = inline_dates(f"ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Кол ИЗ РегистрСведений.{REGISTER} КАК Т ГДЕ {where}",
                                 {"Начало": start, "Конец": end})
        sel = q.Execute().Choose()
        return int(getattr(sel, "Кол") or 0) if sel.Next() else 0

    total = count()
    out = []
    for attr in _fields(reg):
        name = str(_prop(attr, "Name", "Имя"))
        try:
            title = str(_prop(attr, "Synonym", "Синоним"))
        except AttributeError:
            title = ""
        try:
            type_text = _text(conn, _prop(attr, "Type", "Тип"))
        except AttributeError:
            type_text = ""
        kinds = _ref_kinds(conn, attr)
        row = {"name": name, "title": title if title and title != name else "",
               "kind": kind_of(type_text, kinds), "total": total}
        try:
            row["filled"] = count(fill_condition(name, type_text, kinds))
        except Exception:  # noqa: BLE001 - поле, которое 1С не даёт сравнить, просто без числа
            row["filled"] = None
        out.append(row)
    return out
