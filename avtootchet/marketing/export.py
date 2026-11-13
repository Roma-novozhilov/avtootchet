"""Выгрузка для витрины маркетолога: звонки, задачи, заявки (ЗН без сумм), клиенты, менеджеры.

Только чтение Далиона. Форматы: CSV (UTF-8 с BOM, разделитель «;» - открывается в Excel) и JSON.
Финансов нет: в заказ-нарядах выгружаются только даты, статусы, тип, источник рекламы и люди.
Номера телефонов выгружаются только если это включено в настройках (по умолчанию нет); для
определения «перезвонили или нет» номера используются внутри программы и в файл не попадают.

Пропущенный звонок: длительность 0 и направление не исходящее. Перезвонили: после пропущенного
в течение CALLBACK_HOURS был исходящий звонок на этот номер или принятый входящий с него.
Коды направлений «Простых звонков» (входящий/исходящий) задаются в настройках программы.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..common.comdate import com_datetime, inline_dates

CALLBACK_HOURS = 72

Q_CALLS = """ВЫБРАТЬ
	Т.УИД КАК УИД,
	Т.ДатаВремяНачала КАК Начало,
	Т.ДатаВремяОкончания КАК Окончание,
	Т.НаправлениеЗвонка КАК Направление,
	Т.Продолжительность КАК Длительность,
	ПРЕДСТАВЛЕНИЕ(Т.Пользователь) КАК Менеджер,
	Т.Контрагент.Код КАК КлиентКод,
	ПРЕДСТАВЛЕНИЕ(Т.Контрагент) КАК Клиент,
	Т.пз_ПредставлениеКанала КАК Линия,
	Т.НомерТелефонаС КАК С,
	Т.НомерТелефонаНа КАК На
ИЗ
	РегистрСведений.пз_ИсторияЗвонков КАК Т
ГДЕ
	Т.ДатаВремяНачала МЕЖДУ &Начало И &КонецСЗапасом
УПОРЯДОЧИТЬ ПО
	Т.ДатаВремяНачала"""
CALL_COLS = ("УИД", "Начало", "Окончание", "Направление", "Длительность", "Менеджер", "КлиентКод", "Клиент",
             "Линия", "С", "На")

Q_TASKS = """ВЫБРАТЬ
	Т.Дата КАК Создана,
	ПРЕДСТАВЛЕНИЕ(Т.ТипЗадачи) КАК Тип,
	ПРЕДСТАВЛЕНИЕ(Т.Тема) КАК Тема,
	ПРЕДСТАВЛЕНИЕ(Т.Исполнитель) КАК Исполнитель,
	ПРЕДСТАВЛЕНИЕ(Т.Контролер) КАК Контролер,
	Т.НапомнитьПосле КАК Срок,
	Т.Выполнено КАК Выполнено,
	Т.ДатаВыполнения КАК ДатаВыполнения
ИЗ
	Справочник.Напоминания КАК Т
ГДЕ
	НЕ Т.ПометкаУдаления
	И Т.Дата МЕЖДУ &Начало И &Конец
УПОРЯДОЧИТЬ ПО
	Т.Дата"""
TASK_COLS = ("Создана", "Тип", "Тема", "Исполнитель", "Контролер", "Срок", "Выполнено",
             "ДатаВыполнения")

Q_REQUESTS = """ВЫБРАТЬ
	З.Номер КАК Номер,
	З.ДатаСоздания КАК Создан,
	ПРЕДСТАВЛЕНИЕ(З.Автор) КАК Автор,
	ПРЕДСТАВЛЕНИЕ(З.ТипЗН) КАК Тип,
	ПРЕДСТАВЛЕНИЕ(З.СтатусЗН) КАК Статус,
	ПРЕДСТАВЛЕНИЕ(З.ИсточникРекламы) КАК Источник,
	З.Контрагент.Код КАК КлиентКод,
	ПРЕДСТАВЛЕНИЕ(З.Контрагент) КАК Клиент,
	ПРЕДСТАВЛЕНИЕ(З.МастерПриемщик) КАК Мастер,
	З.ДатаПереведенВРаботу КАК ВРаботе,
	ПРЕДСТАВЛЕНИЕ(З.КтоПеревелВРаботу) КАК КтоВРаботу,
	З.ДатаЗавершен КАК Завершен,
	З.ДатаОтгружен КАК Выдан,
	З.Повтор КАК Повтор
ИЗ
	Документ.ЗаказНаряд КАК З
ГДЕ
	НЕ З.ПометкаУдаления
	И З.Дата МЕЖДУ &Начало И &Конец
УПОРЯДОЧИТЬ ПО
	З.Дата"""
REQ_COLS = ("Номер", "Создан", "Автор", "Тип", "Статус", "Источник", "КлиентКод", "Клиент", "Мастер", "ВРаботе",
            "КтоВРаботу", "Завершен", "Выдан", "Повтор")


@dataclass
class Directions:
    """Коды «НаправлениеЗвонка» из «Простых звонков». Пусто - неизвестно."""
    incoming: set[int] = field(default_factory=set)
    outgoing: set[int] = field(default_factory=set)

    @classmethod
    def parse(cls, incoming: str, outgoing: str) -> "Directions":
        def nums(s: str) -> set[int]:
            return {int(x) for x in re.findall(r"-?\d+", s or "")}
        return cls(nums(incoming), nums(outgoing))

    def name(self, code) -> str:
        c = _int(code)
        if c in self.incoming:
            return "входящий"
        if c in self.outgoing:
            return "исходящий"
        return f"код {c}"


@dataclass
class Export:
    d1: date
    d2: date
    calls: list[dict]
    tasks: list[dict]
    requests: list[dict]
    clients: list[dict]
    managers: list[dict]

    def tables(self) -> dict[str, list[dict]]:
        return {"calls": self.calls, "tasks": self.tasks, "requests": self.requests,
                "clients": self.clients, "managers": self.managers}


# ---------------------------------------------------------------------------- помощники
def _dt(v) -> datetime | None:
    if v is None or not hasattr(v, "year") or v.year < 1990:
        return None
    return com_datetime(v)


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _phone(v) -> str:
    """Последние 10 цифр номера - чтобы +7 и 8 в начале совпадали."""
    d = re.sub(r"\D", "", _s(v))
    return d[-10:] if len(d) >= 10 else d


def _iso(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else ""


def _minutes(a: datetime | None, b: datetime | None) -> float | str:
    return round((b - a).total_seconds() / 60, 1) if a and b else ""


def _hours(a: datetime | None, b: datetime | None) -> float | str:
    return round((b - a).total_seconds() / 3600, 2) if a and b else ""


# ---------------------------------------------------------------------------- расчёт
def build_calls(rows: list[dict], dirs: Directions, d2_end: datetime, phones: bool) -> list[dict]:
    """rows - звонки за период + запас CALLBACK_HOURS после него (для поиска перезвона)."""
    calls = []
    for r in rows:
        start = _dt(r["Начало"])
        code = _int(r["Направление"])
        dur = _int(r["Длительность"])
        outgoing = code in dirs.outgoing
        # номер клиента: для исходящего - куда звонили, иначе - откуда
        client_phone = _phone(r["На"] if outgoing else r["С"])
        calls.append({"_start": start, "_phone": client_phone, "_out": outgoing, "_dur": dur,
                      "_row": r, "_code": code})
    by_phone: dict[str, list[dict]] = {}
    for c in calls:
        by_phone.setdefault(c["_phone"], []).append(c)

    out = []
    for c in calls:
        if c["_start"] is None or c["_start"] > d2_end:
            continue                       # звонки из «запаса» нужны только для перезвона
        r = c["_row"]
        missed = c["_dur"] == 0 and not c["_out"]
        back = ""
        back_min: float | str = ""
        if missed and c["_phone"]:
            limit = c["_start"] + timedelta(hours=CALLBACK_HOURS)
            nxt = next((x for x in by_phone.get(c["_phone"], [])
                        if x["_start"] and c["_start"] < x["_start"] <= limit
                        and (x["_out"] or x["_dur"] > 0)), None)
            back = "да" if nxt else "нет"
            back_min = _minutes(c["_start"], nxt["_start"]) if nxt else ""
        row = {
            "id": _s(r["УИД"]),
            "начало": _iso(c["_start"]),
            "окончание": _iso(_dt(r["Окончание"])),
            "дата": c["_start"].strftime("%Y-%m-%d"),
            "час": c["_start"].hour,
            "направление_код": c["_code"],
            "направление": dirs.name(c["_code"]),
            "длительность_с": c["_dur"],
            "пропущен": "да" if missed else "нет",
            "перезвонили": back,
            "перезвон_через_мин": back_min,
            "менеджер": _s(r["Менеджер"]),
            "клиент_код": _s(r["КлиентКод"]),
            "клиент": _s(r["Клиент"]),
            "линия": _s(r["Линия"]),
        }
        if phones:
            row["телефон"] = c["_phone"]
        out.append(row)
    return out


def build_tasks(rows: list[dict], now: datetime) -> list[dict]:
    out = []
    for r in rows:
        created, due, done_at = _dt(r["Создана"]), _dt(r["Срок"]), _dt(r["ДатаВыполнения"])
        done = bool(r["Выполнено"])
        overdue = (not done and due is not None and due < now) or (done and due and done_at and done_at > due)
        out.append({
            "создана": _iso(created), "дата": created.strftime("%Y-%m-%d") if created else "",
            "тип": _s(r["Тип"]), "тема": _s(r["Тема"]),
            "исполнитель": _s(r["Исполнитель"]), "контролер": _s(r["Контролер"]),
            "срок": _iso(due), "выполнено": "да" if done else "нет", "выполнена": _iso(done_at),
            "просрочено": "да" if overdue else "нет",
            "часов_до_выполнения": _hours(created, done_at),
            "опоздание_ч": _hours(due, done_at) if done and due and done_at and done_at > due else "",
        })
    return out


def build_requests(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        created, work = _dt(r["Создан"]), _dt(r["ВРаботе"])
        out.append({
            "номер": _s(r["Номер"]), "создан": _iso(created), "дата": created.strftime("%Y-%m-%d") if created else "",
            "автор": _s(r["Автор"]), "тип": _s(r["Тип"]), "статус": _s(r["Статус"]),
            "источник_рекламы": _s(r["Источник"]),
            "клиент_код": _s(r["КлиентКод"]), "клиент": _s(r["Клиент"]), "мастер": _s(r["Мастер"]),
            "в_работе": _iso(work), "кто_перевел": _s(r["КтоВРаботу"]),
            "часов_до_работы": _hours(created, work),
            "завершен": _iso(_dt(r["Завершен"])), "выдан": _iso(_dt(r["Выдан"])),
            "повтор": "да" if r["Повтор"] else "нет",
        })
    return out


def build_dictionaries(calls, requests, tasks) -> tuple[list[dict], list[dict]]:
    clients: dict[str, dict] = {}
    for row in calls + requests:
        code = row.get("клиент_код")
        if code and code not in clients:
            clients[code] = {"клиент_код": code, "клиент": row.get("клиент", ""),
                             "звонков": 0, "заявок": 0}
        if code:
            clients[code]["звонков" if "направление" in row else "заявок"] += 1
    people: dict[str, dict] = {}

    def add(name, what):
        if name:
            p = people.setdefault(name, {"менеджер": name, "звонков": 0, "пропущено": 0, "заявок": 0,
                                         "задач": 0, "задач_просрочено": 0})
            p[what] += 1

    for c in calls:
        add(c["менеджер"], "звонков")
        if c["пропущен"] == "да":
            add(c["менеджер"], "пропущено")
    for r in requests:
        add(r["автор"], "заявок")
    for t in tasks:
        add(t["исполнитель"], "задач")
        if t["просрочено"] == "да":
            add(t["исполнитель"], "задач_просрочено")
    return sorted(clients.values(), key=lambda x: x["клиент"]), sorted(people.values(), key=lambda x: x["менеджер"])


# ---------------------------------------------------------------------------- 1С
def load_export(conn, d1: date, d2: date, dirs: Directions, phones: bool = False,
                now: datetime | None = None) -> Export:
    start = datetime(d1.year, d1.month, d1.day)
    end = datetime(d2.year, d2.month, d2.day, 23, 59, 59)

    def run(text, cols):
        q = conn.NewObject("Запрос")
        q.Text, _ = inline_dates(text, {"Начало": start, "Конец": end,
                                        "КонецСЗапасом": end + timedelta(hours=CALLBACK_HOURS)})
        sel = q.Execute().Choose()
        rows = []
        while sel.Next():
            rows.append({c: getattr(sel, c) for c in cols})
        return rows

    calls = build_calls(run(Q_CALLS, CALL_COLS), dirs, end, phones)
    tasks = build_tasks(run(Q_TASKS, TASK_COLS), now or datetime.now())
    requests = build_requests(run(Q_REQUESTS, REQ_COLS))
    clients, managers = build_dictionaries(calls, requests, tasks)
    return Export(d1, d2, calls, tasks, requests, clients, managers)


# ---------------------------------------------------------------------------- файлы
FIELDS = {   # заголовки - даже если за период строк нет
    "calls": ["id", "начало", "окончание", "дата", "час", "направление_код", "направление", "длительность_с",
              "пропущен", "перезвонили", "перезвон_через_мин", "менеджер", "клиент_код", "клиент", "линия"],
    "tasks": ["создана", "дата", "тип", "тема", "исполнитель", "контролер", "срок", "выполнено",
              "выполнена", "просрочено", "часов_до_выполнения", "опоздание_ч"],
    "requests": ["номер", "создан", "дата", "автор", "тип", "статус", "источник_рекламы", "клиент_код", "клиент",
                 "мастер", "в_работе", "кто_перевел", "часов_до_работы", "завершен", "выдан", "повтор"],
    "clients": ["клиент_код", "клиент", "звонков", "заявок"],
    "managers": ["менеджер", "звонков", "пропущено", "заявок", "задач", "задач_просрочено"],
}


def to_csv(rows: list[dict], name: str = "") -> bytes:
    buf = io.StringIO()
    fields = list(rows[0].keys()) if rows else FIELDS.get(name, [])
    w = csv.DictWriter(buf, fieldnames=fields, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_json(ex: Export) -> bytes:
    data = {"период": {"с": ex.d1.isoformat(), "по": ex.d2.isoformat()},
            "выгружено": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **ex.tables()}
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")


def save_export(ex: Export, folder: Path, fmt_csv: bool = True, fmt_json: bool = True) -> list[Path]:
    """Папка «Выгрузка ДД.ММ.ГГГГ-ДД.ММ.ГГГГ» внутри folder; повторная выгрузка за тот же период её заменяет."""
    target = folder / f"Выгрузка CRM {ex.d1:%d.%m.%Y}-{ex.d2:%d.%m.%Y}"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    if fmt_csv:
        for name, rows in ex.tables().items():
            p = target / f"{name}.csv"
            p.write_bytes(to_csv(rows, name))
            written.append(p)
    if fmt_json:
        p = target / "crm.json"
        p.write_bytes(to_json(ex))
        written.append(p)
    return written
