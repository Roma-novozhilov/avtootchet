"""Показатели витрины: всё считается здесь из файла данных, сайт только рисует.

Определения (их же видно на сайте в подсказках):
* пропущенный - входящий длительностью 0 с; перезвонили - в течение 72 ч был исходящий на этот
  номер или принятый входящий с него (как в выгрузке маркетолога);
* обращение без заказ-наряда - принятый входящий от 30 с, после которого у этого клиента
  за 14 дней не появился ЗН и в момент звонка не было открытого ЗН (машина не в ремонте);
* зависший ЗН - создан больше 14 дней назад и не выдан;
* выручка - оплаты в кассы по ЗН и расходным накладным минус возвраты (как у владельца);
* средний чек - сумма выданных за период ЗН / их количество.

Роль «marketer» (Анастасия) денег не видит: суммы удаляются из всех ответов, раздел
«Финансы» и список неоплаченных ЗН закрыты.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

OWNER, MARKETER = "owner", "marketer"
CALL_AFTER_DAYS = 14          # обращение -> ЗН
OPEN_ORDER_DAYS = 45          # ЗН моложе этого и не выдан - машина ещё в работе
STUCK_DAYS = 14
TALK_MIN_SEC = 30
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONEY_KEYS = {"amount", "works", "parts", "materials", "paid", "debt", "revenue", "avg_check", "sum"}


def _t(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d")
        except ValueError:
            return None


def mask_phone(p: str) -> str:
    p = re.sub(r"\D", "", p or "")
    if len(p) < 6:
        return "номер скрыт" if p else ""
    return f"+7 ··· ···-{p[-4:-2]}-{p[-2:]}"


def pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


@dataclass
class Filters:
    d1: date
    d2: date
    user: str = ""
    line: str = ""

    @property
    def start(self) -> datetime:
        return datetime(self.d1.year, self.d1.month, self.d1.day)

    @property
    def end(self) -> datetime:
        return datetime(self.d2.year, self.d2.month, self.d2.day, 23, 59, 59)

    def previous(self) -> "Filters":
        n = (self.d2 - self.d1).days + 1
        return Filters(self.d1 - timedelta(days=n), self.d1 - timedelta(days=1), self.user, self.line)

    def has(self, t: datetime | None) -> bool:
        return t is not None and self.start <= t <= self.end


@dataclass
class Data:
    calls: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    works: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    lines: dict = field(default_factory=dict)       # кусок номера линии -> название («21» -> «Авито»)
    full_phones: bool = False

    @classmethod
    def from_tables(cls, tables: dict, meta: dict, lines: dict | None = None, full_phones: bool = False) -> "Data":
        d = cls(meta=meta, lines=lines or {}, full_phones=full_phones)
        for c in tables.get("calls", []):
            c = dict(c)
            c["_t"] = _t(c["start"])
            if c["_t"] is None:
                continue
            c["missed"] = c["missed"] in (1, True, "1", "да")
            c["line_name"] = d.line_name(c.get("line") or "")
            d.calls.append(c)
        d.calls.sort(key=lambda c: c["_t"])
        for o in tables.get("orders", []):
            o = dict(o)
            o["_t"] = _t(o["created"]) or _t(o["date"])
            o["_issued"] = _t(o["issued"])
            if o["_t"]:
                d.orders.append(o)
        d.orders.sort(key=lambda o: o["_t"])
        for p in tables.get("payments", []):
            p = dict(p)
            p["_t"] = _t(p["date"])
            if p["_t"]:
                d.payments.append(p)
        for w in tables.get("works", []):
            w = dict(w)
            w["_t"] = _t(w["day"])
            if w["_t"]:
                d.works.append(w)
        for t in tables.get("tasks", []):
            t = dict(t)
            t["_t"] = _t(t["created"])
            t["is_task"] = t["is_task"] in (1, True, "1")
            t["done"] = t["done"] in (1, True, "1")
            if t["_t"]:
                d.tasks.append(t)
        d._index()
        return d

    def _index(self):
        self.orders_by_client: dict[str, list[dict]] = defaultdict(list)
        for o in self.orders:
            if o.get("client_code"):
                self.orders_by_client[o["client_code"]].append(o)
        self.paid_by_order: dict[str, float] = defaultdict(float)
        for p in self.payments:
            if p.get("basis_kind") == "ЗН" and p.get("basis_number"):
                self.paid_by_order[p["basis_number"]] += float(p.get("amount") or 0)
        self.calls_by_id = {c["id"]: c for c in self.calls if c.get("id")}

    def line_name(self, raw: str) -> str:
        for key, name in self.lines.items():
            if key and key in raw:
                return name
        if not raw:
            return "без линии"
        digits = re.sub(r"\D", "", raw)
        return f"линия …{digits[-2:]}" if len(digits) >= 2 else raw

    def phone_text(self, p: str) -> str:
        return p if self.full_phones and p else mask_phone(p)

    # ------------------------------------------------------------------ выборки
    def calls_in(self, f: Filters) -> list[dict]:
        return [c for c in self.calls if f.has(c["_t"]) and (not f.user or c.get("user") == f.user)
                and (not f.line or c["line_name"] == f.line)]

    def orders_in(self, f: Filters) -> list[dict]:
        return [o for o in self.orders if f.has(o["_t"]) and (not f.user or o.get("author") == f.user)]

    def issued_in(self, f: Filters) -> list[dict]:
        return [o for o in self.orders if f.has(o["_issued"]) and (not f.user or o.get("author") == f.user)]

    def payments_in(self, f: Filters) -> list[dict]:
        return [p for p in self.payments if f.has(p["_t"])]


# ---------------------------------------------------------------------------- расчёты
def _incoming(calls):
    return [c for c in calls if c.get("dir") == "in"]


def lost_requests(d: Data, calls: list[dict]) -> list[dict]:
    """Принятые входящие от клиентов, после которых не появился ЗН (и машина не была в ремонте)."""
    out = []
    for c in _incoming(calls):
        if c["missed"] or float(c.get("dur") or 0) < TALK_MIN_SEC or not c.get("client_code"):
            continue
        t = c["_t"]
        orders = d.orders_by_client.get(c["client_code"], [])
        busy = any(o["_t"] <= t and (o["_issued"] is None or o["_issued"] >= t)
                   and o["_t"] >= t - timedelta(days=OPEN_ORDER_DAYS) for o in orders)
        after = any(t <= o["_t"] <= t + timedelta(days=CALL_AFTER_DAYS) for o in orders)
        if not busy and not after:
            out.append(c)
    # один клиент - одна строка (последний звонок)
    last: dict[str, dict] = {}
    for c in out:
        last[c["client_code"]] = c
    return sorted(last.values(), key=lambda c: c["_t"], reverse=True)


def converted(d: Data, c: dict) -> bool:
    if not c.get("client_code"):
        return False
    return any(c["_t"] <= o["_t"] <= c["_t"] + timedelta(days=CALL_AFTER_DAYS)
               for o in d.orders_by_client.get(c["client_code"], []))


def stuck_orders(d: Data, now: datetime) -> list[dict]:
    return sorted([o for o in d.orders if o["_issued"] is None and o["_t"] < now - timedelta(days=STUCK_DAYS)
                   and not is_cancelled(o)], key=lambda o: o["_t"])


def is_cancelled(o: dict) -> bool:
    return bool(re.search(r"отмен|отказ|аннул", (o.get("status") or "").lower()))


def unpaid_orders(d: Data, f: Filters) -> list[dict]:
    out = []
    for o in d.issued_in(f):
        amount = float(o.get("amount") or 0)
        paid = round(d.paid_by_order.get(o["number"], 0.0), 2)
        if amount > 0 and amount - paid > 1:
            out.append({**o, "paid": paid, "debt": round(amount - paid, 2)})
    return sorted(out, key=lambda o: -o["debt"])


def repeat_callers(calls: list[dict], days: int = 7) -> list[dict]:
    """Номера, звонившие 2+ раз в течение недели (повторное обращение)."""
    by_phone: dict[str, list[dict]] = defaultdict(list)
    for c in _incoming(calls):
        if c.get("phone"):
            by_phone[c["phone"]].append(c)
    out = []
    for phone, cs in by_phone.items():
        cs.sort(key=lambda c: c["_t"])
        if any((b["_t"] - a["_t"]) <= timedelta(days=days) for a, b in zip(cs, cs[1:])):
            out.append({"phone": phone, "calls": len(cs), "missed": sum(1 for c in cs if c["missed"]),
                        "first": cs[0], "last": cs[-1]})
    return sorted(out, key=lambda r: -r["calls"])


def call_kpis(d: Data, f: Filters) -> dict:
    calls = d.calls_in(f)
    inc = _incoming(calls)
    missed = [c for c in inc if c["missed"]]
    nocb = [c for c in missed if c.get("callback") == "нет"]
    return {"incoming": len(inc), "missed": len(missed), "missed_pct": pct(len(missed), len(inc)),
            "no_callback": len(nocb), "outgoing": sum(1 for c in calls if c.get("dir") == "out"),
            "callers": len({c.get("phone") for c in inc if c.get("phone")})}


def money_kpis(d: Data, f: Filters) -> dict:
    issued = [o for o in d.issued_in(f) if float(o.get("amount") or 0) > 0]
    total = sum(float(o["amount"]) for o in issued)
    return {"revenue": round(sum(float(p["amount"]) for p in d.payments_in(f)), 2),
            "avg_check": round(total / len(issued), 2) if issued else None}


def _days(f: Filters) -> list[date]:
    n = (f.d2 - f.d1).days + 1
    return [f.d1 + timedelta(days=i) for i in range(n)]


def _series(f: Filters, items, key=lambda x: 1, when=lambda x: x["_t"]) -> list[float]:
    by = defaultdict(float)
    for x in items:
        t = when(x)
        if t:
            by[t.date()] += key(x)
    return [round(by.get(day, 0), 2) for day in _days(f)]


# ---------------------------------------------------------------------------- разделы
def home(d: Data, f: Filters, role: str, now: datetime) -> dict:
    p = f.previous()
    cur, prev = call_kpis(d, f), call_kpis(d, p)
    orders, prev_orders = d.orders_in(f), d.orders_in(p)
    kpis = [
        {"key": "incoming", "title": "Входящих звонков", "value": cur["incoming"], "prev": prev["incoming"]},
        {"key": "missed", "title": "Пропущено", "value": cur["missed"], "prev": prev["missed"],
         "note": f"{str(cur['missed_pct']).replace('.', ',')} % входящих" if cur["missed_pct"] is not None else "", "bad_up": True,
         "list": "missed"},
        {"key": "no_callback", "title": "Не перезвонили", "value": cur["no_callback"], "prev": prev["no_callback"],
         "bad_up": True, "list": "missed_no_callback"},
        {"key": "orders", "title": "Новых заказ-нарядов", "value": len(orders), "prev": len(prev_orders),
         "list": "orders", "good_up": True},
        {"key": "issued", "title": "Выдано машин", "value": len(d.issued_in(f)), "prev": len(d.issued_in(p)),
         "good_up": True},
    ]
    series = {"Входящие": _series(f, _incoming(d.calls_in(f))),
              "Пропущенные": _series(f, [c for c in _incoming(d.calls_in(f)) if c["missed"]]),
              "Новые ЗН": _series(f, orders)}
    out = {"kpis": kpis, "days": [x.isoformat() for x in _days(f)], "series": series}
    if role == OWNER:
        m, mp = money_kpis(d, f), money_kpis(d, p)
        kpis += [{"key": "revenue", "title": "Выручка", "value": m["revenue"], "prev": mp["revenue"], "money": True,
                  "good_up": True},
                 {"key": "avg_check", "title": "Средний чек", "value": m["avg_check"], "prev": mp["avg_check"],
                  "money": True}]
        out["revenue"] = _series(f, d.payments_in(f), key=lambda x: float(x["amount"]))
    out["problems"] = problem_counts(d, f, role, now)
    return out


PROBLEMS = [
    ("missed_no_callback", "Пропущенные без перезвона", "Входящий без ответа, и за 72 часа никто не перезвонил"),
    ("slow_callback", "Перезвонили позже часа", "Пропущенный, перезвон больше чем через 60 минут"),
    ("lost_requests", "Обращения без заказ-наряда",
     "Поговорили (от 30 с), но за 14 дней у клиента не появился ЗН, и машина не была в ремонте"),
    ("unknown_numbers", "Новые номера без карточки",
     "Принятый звонок с номера, которого нет у клиентов Далиона: новый клиент не заведён"),
    ("cancelled", "Отменённые заказ-наряды", "Статус ЗН содержит «отмен» или «отказ»"),
    ("unpaid", "Выданы, но не оплачены полностью", "Выданный ЗН, по которому оплат меньше суммы"),
    ("stuck", "Зависшие заказ-наряды", "Создан больше 14 дней назад и не выдан (на сегодня, без учёта периода)"),
    ("no_source", "ЗН без источника рекламы", "Не заполнено «Источник рекламы» - не узнать, откуда клиент"),
    ("open_tasks", "Невыполненные задачи", "Задача не выполнена дольше 2 дней"),
]


def problem_counts(d: Data, f: Filters, role: str, now: datetime) -> list[dict]:
    out = []
    for key, title, hint in PROBLEMS:
        if key == "unpaid" and role != OWNER:
            continue
        rows = problem_rows(d, key, f, now)
        item = {"key": key, "title": title, "hint": hint, "count": len(rows)}
        if role == OWNER and key == "unpaid":
            item["sum"] = round(sum(r["debt"] for r in rows), 2)
        out.append(item)
    return out


def problem_rows(d: Data, key: str, f: Filters, now: datetime) -> list[dict]:
    calls = d.calls_in(f)
    if key == "missed":
        return [c for c in _incoming(calls) if c["missed"]]
    if key == "missed_no_callback":
        return [c for c in _incoming(calls) if c["missed"] and c.get("callback") == "нет"]
    if key == "slow_callback":
        return [c for c in _incoming(calls) if c["missed"] and c.get("callback") == "да"
                and float(c.get("callback_min") or 0) > 60]
    if key == "lost_requests":
        return lost_requests(d, calls)
    if key == "unknown_numbers":
        seen, out = set(), []
        for c in sorted(_incoming(calls), key=lambda c: c["_t"], reverse=True):
            if not c["missed"] and not c.get("client_code") and c.get("phone") and c["phone"] not in seen:
                seen.add(c["phone"])
                out.append(c)
        return out
    if key == "cancelled":
        return [o for o in d.orders_in(f) if is_cancelled(o)]
    if key == "unpaid":
        return unpaid_orders(d, f)
    if key == "stuck":
        return [o for o in stuck_orders(d, now) if not f.user or o.get("author") == f.user]
    if key == "no_source":
        return [o for o in d.orders_in(f) if not (o.get("source") or "").strip()]
    if key == "open_tasks":
        return [t for t in d.tasks if t["is_task"] and not t["done"] and t["_t"] < now - timedelta(days=2)
                and (not f.user or t.get("executor") == f.user)]
    if key == "orders":
        return d.orders_in(f)
    if key == "repeat":
        return repeat_callers(calls)
    raise KeyError(key)


def telephony(d: Data, f: Filters) -> dict:
    calls = d.calls_in(f)
    inc = _incoming(calls)
    heat = [[0] * 24 for _ in range(7)]
    heat_missed = [[0] * 24 for _ in range(7)]
    for c in inc:
        heat[c["_t"].weekday()][c["_t"].hour] += 1
        if c["missed"]:
            heat_missed[c["_t"].weekday()][c["_t"].hour] += 1
    lines = defaultdict(lambda: {"incoming": 0, "missed": 0, "no_callback": 0, "outgoing": 0})
    for c in calls:
        r = lines[c["line_name"]]
        if c.get("dir") == "in":
            r["incoming"] += 1
            r["missed"] += c["missed"]
            r["no_callback"] += c["missed"] and c.get("callback") == "нет"
        elif c.get("dir") == "out":
            r["outgoing"] += 1
    line_rows = [{"line": k, **v, "missed_pct": pct(v["missed"], v["incoming"])}
                 for k, v in sorted(lines.items(), key=lambda kv: -kv[1]["incoming"])]
    buckets = [("до 15 мин", 0, 15), ("15–60 мин", 15, 60), ("1–4 ч", 60, 240), ("4–24 ч", 240, 1440),
               ("1–3 дня", 1440, 4321)]
    missed = [c for c in inc if c["missed"]]
    cb = [float(c["callback_min"]) for c in missed if c.get("callback") == "да" and c.get("callback_min") not in ("", None)]
    dist = [{"label": n, "count": sum(1 for m in cb if a <= m < b)} for n, a, b in buckets]
    dist.append({"label": "не перезвонили", "count": sum(1 for c in missed if c.get("callback") == "нет"), "bad": True})
    waits = [float(c["wait"]) for c in inc if c.get("wait") not in ("", None) and not c["missed"]]
    talks_in = [float(c["dur"]) for c in inc if not c["missed"] and float(c.get("dur") or 0) > 0]
    talks_out = [float(c["dur"]) for c in calls if c.get("dir") == "out" and float(c.get("dur") or 0) > 0]
    rep = repeat_callers(calls)
    known = sum(1 for c in inc if c.get("client_code"))
    hours = [{"hour": h, "incoming": sum(heat[w][h] for w in range(7)),
              "missed": sum(heat_missed[w][h] for w in range(7))} for h in range(24)]
    return {
        "weekdays": WEEKDAYS, "heat": heat, "heat_missed": heat_missed, "hours": hours, "lines": line_rows,
        "callback": dist, "callback_median": round(median(cb)) if cb else None,
        "wait_avg": round(sum(waits) / len(waits)) if waits else None, "wait_count": len(waits),
        "talk_in_avg": round(sum(talks_in) / len(talks_in)) if talks_in else None,
        "talk_out_avg": round(sum(talks_out) / len(talks_out)) if talks_out else None,
        "repeat": len(rep), "repeat_missed_first": sum(1 for r in rep if r["first"]["missed"]),
        "known_pct": pct(known, len(inc)), "incoming": len(inc),
    }


def managers(d: Data, f: Filters, role: str) -> dict:
    calls = d.calls_in(Filters(f.d1, f.d2, "", f.line))
    rows: dict[str, dict] = {}

    def row(name):
        return rows.setdefault(name or "(не указан)", {
            "name": name or "(не указан)", "answered": 0, "missed": 0, "no_callback": 0, "outgoing": 0,
            "talk_sum": 0.0, "talks": 0, "client_talks": 0, "converted": 0, "orders": 0, "no_source": 0,
            "tasks_done": 0, "tasks_open": 0})

    for c in calls:
        r = row(c.get("user"))
        if c.get("dir") == "in":
            if c["missed"]:
                r["missed"] += 1
                r["no_callback"] += c.get("callback") == "нет"
            else:
                r["answered"] += 1
                if float(c.get("dur") or 0) >= TALK_MIN_SEC and c.get("client_code"):
                    r["client_talks"] += 1
                    r["converted"] += converted(d, c)
        elif c.get("dir") == "out":
            r["outgoing"] += 1
        if float(c.get("dur") or 0) > 0:
            r["talk_sum"] += float(c["dur"])
            r["talks"] += 1
    for o in d.orders_in(Filters(f.d1, f.d2)):
        if o.get("author"):
            r = row(o["author"])
            r["orders"] += 1
            r["no_source"] += not (o.get("source") or "").strip()
    for t in d.tasks:
        if t["is_task"] and f.has(t["_t"]) and t.get("executor"):
            r = row(t["executor"])
            r["tasks_done" if t["done"] else "tasks_open"] += 1
    out = []
    for r in rows.values():
        inc = r["answered"] + r["missed"]
        out.append({**{k: v for k, v in r.items() if k not in ("talk_sum", "talks")},
                    "missed_pct": pct(r["missed"], inc),
                    "talk_avg": round(r["talk_sum"] / r["talks"]) if r["talks"] else None,
                    "conversion": pct(r["converted"], r["client_talks"])})
    out.sort(key=lambda r: -(r["answered"] + r["missed"] + r["outgoing"] + r["orders"]))
    masters = defaultdict(lambda: {"orders": 0, "issued": 0, "sum": 0.0})
    for o in d.orders_in(Filters(f.d1, f.d2)):
        masters[o.get("master") or "(не указан)"]["orders"] += 1
    for o in d.issued_in(Filters(f.d1, f.d2)):
        m = masters[o.get("master") or "(не указан)"]
        m["issued"] += 1
        m["sum"] += float(o.get("amount") or 0)
    mrows = []
    for name, m in sorted(masters.items(), key=lambda kv: -kv[1]["sum"] - kv[1]["orders"]):
        item = {"name": name, "orders": m["orders"], "issued": m["issued"]}
        if role == OWNER:
            item["sum"] = round(m["sum"], 2)
            item["avg_check"] = round(m["sum"] / m["issued"], 2) if m["issued"] else None
        mrows.append(item)
    users_with_calls = sorted({c.get("user") for c in d.calls if c.get("user")})
    return {"managers": out, "masters": mrows, "phone_users": users_with_calls}


def finance(d: Data, f: Filters) -> dict:
    pays = d.payments_in(f)
    by_method, by_dir = Counter(), Counter()
    for p in pays:
        by_method[p.get("method") or "Прочее"] += float(p["amount"])
        by_dir[p.get("direction") or ""] += float(p["amount"])
    issued = d.issued_in(Filters(f.d1, f.d2))
    parts = {"Работы": sum(float(o.get("works") or 0) for o in issued),
             "Запчасти": sum(float(o.get("parts") or 0) for o in issued),
             "Материалы": sum(float(o.get("materials") or 0) for o in issued)}
    works = defaultdict(lambda: {"orders": 0, "amount": 0.0})
    for w in d.works:
        if f.has(w["_t"]):
            works[w["work"]]["orders"] += int(w.get("orders") or 0)
            works[w["work"]]["amount"] += float(w.get("amount") or 0)
    top = sorted(({"work": k, "orders": v["orders"], "amount": round(v["amount"], 2)} for k, v in works.items()),
                 key=lambda r: -r["amount"])[:20]
    sources = defaultdict(lambda: {"orders": 0, "amount": 0.0, "issued": 0})
    for o in d.orders_in(Filters(f.d1, f.d2)):
        sources[(o.get("source") or "").strip() or "не указан"]["orders"] += 1
    for o in issued:
        s = sources[(o.get("source") or "").strip() or "не указан"]
        s["issued"] += 1
        s["amount"] += float(o.get("amount") or 0)
    src = sorted(({"source": k, **v, "amount": round(v["amount"], 2)} for k, v in sources.items()),
                 key=lambda r: -r["amount"])
    months: dict[str, dict] = {}
    for p in d.payments:
        k = p["_t"].strftime("%Y-%m")
        months.setdefault(k, {"month": k, "revenue": 0.0, "issued": 0, "issued_sum": 0.0})["revenue"] += float(p["amount"])
    for o in d.orders:
        if o["_issued"] and float(o.get("amount") or 0) > 0:
            m = months.setdefault(o["_issued"].strftime("%Y-%m"),
                                  {"month": o["_issued"].strftime("%Y-%m"), "revenue": 0.0, "issued": 0, "issued_sum": 0.0})
            m["issued"] += 1
            m["issued_sum"] += float(o["amount"])
    mrows = []
    for k in sorted(months):
        m = months[k]
        mrows.append({"month": k, "revenue": round(m["revenue"], 2), "issued": m["issued"],
                      "avg_check": round(m["issued_sum"] / m["issued"], 2) if m["issued"] else None})
    unpaid = unpaid_orders(d, f)
    return {
        "revenue": round(sum(by_method.values()), 2),
        "by_method": [{"name": k, "amount": round(v, 2)} for k, v in by_method.most_common()],
        "by_direction": [{"name": k or "не указано", "amount": round(v, 2)} for k, v in by_dir.most_common()],
        "structure": [{"name": k, "amount": round(v, 2)} for k, v in parts.items()],
        "top_works": top, "sources": src, "months": mrows,
        "days": [x.isoformat() for x in _days(f)],
        "daily": _series(f, pays, key=lambda x: float(x["amount"])),
        "unpaid_count": len(unpaid), "unpaid_sum": round(sum(r["debt"] for r in unpaid), 2),
        "source_filled_pct": pct(sum(1 for o in d.orders_in(f) if (o.get("source") or "").strip()), len(d.orders_in(f))),
    }


# ---------------------------------------------------------------------------- строки для списков
def call_row(d: Data, c: dict) -> dict:
    return {"id": c.get("id"), "time": c["_t"].strftime("%d.%m.%Y %H:%M"),
            "dir": {"in": "входящий", "out": "исходящий"}.get(c.get("dir"), ""),
            "dur": int(float(c.get("dur") or 0)), "missed": c["missed"], "callback": c.get("callback") or "",
            "callback_min": c.get("callback_min"), "user": c.get("user") or "", "client": c.get("client") or "",
            "client_code": c.get("client_code") or "", "phone": d.phone_text(c.get("phone") or ""),
            "line": c["line_name"]}


def order_row(o: dict, role: str) -> dict:
    r = {"number": o.get("number"), "created": o["_t"].strftime("%d.%m.%Y"), "status": o.get("status") or "",
         "type": o.get("type") or "", "author": o.get("author") or "", "master": o.get("master") or "",
         "client": o.get("client") or "", "client_code": o.get("client_code") or "", "car": o.get("car") or "",
         "source": o.get("source") or "",
         "issued": o["_issued"].strftime("%d.%m.%Y") if o.get("_issued") else ""}
    if role == OWNER:
        r["amount"] = o.get("amount")
        if "debt" in o:
            r["paid"], r["debt"] = o["paid"], o["debt"]
    return r


def list_rows(d: Data, key: str, f: Filters, role: str, now: datetime) -> dict:
    if key == "unpaid" and role != OWNER:
        raise PermissionError(key)
    rows = problem_rows(d, key, f, now)
    title = next((t for k, t, _ in PROBLEMS if k == key),
                 {"missed": "Пропущенные звонки", "orders": "Новые заказ-наряды", "repeat": "Повторные обращения"}.get(key, key))
    if key in ("missed", "missed_no_callback", "slow_callback", "lost_requests", "unknown_numbers"):
        return {"title": title, "kind": "calls", "rows": [call_row(d, c) for c in rows]}
    if key == "repeat":
        return {"title": title, "kind": "repeat", "rows": [
            {"id": r["last"]["id"], "phone": d.phone_text(r["phone"]), "calls": r["calls"], "missed": r["missed"],
             "client": r["last"].get("client") or "", "client_code": r["last"].get("client_code") or "",
             "first": r["first"]["_t"].strftime("%d.%m %H:%M"), "last": r["last"]["_t"].strftime("%d.%m %H:%M")}
            for r in rows]}
    if key == "open_tasks":
        return {"title": title, "kind": "tasks", "rows": [
            {"created": t["_t"].strftime("%d.%m.%Y %H:%M"), "type": t.get("type") or "",
             "executor": t.get("executor") or "", "days": (now - t["_t"]).days} for t in rows]}
    return {"title": title, "kind": "orders", "rows": [order_row(o, role) for o in rows]}


def client_path(d: Data, role: str, code: str = "", call_id: str = "") -> dict:
    """Путь клиента: звонки -> заказ-наряды -> оплаты на одной ленте."""
    phone = ""
    if call_id and call_id in d.calls_by_id:
        c = d.calls_by_id[call_id]
        code = code or c.get("client_code") or ""
        phone = c.get("phone") or ""
    calls = [c for c in d.calls if (code and c.get("client_code") == code) or (phone and c.get("phone") == phone)]
    orders = d.orders_by_client.get(code, []) if code else []
    numbers = {o["number"] for o in orders}
    events = [{"t": c["_t"], "kind": "call", **call_row(d, c)} for c in calls]
    events += [{"t": o["_t"], "kind": "order", **order_row(o, role)} for o in orders]
    if role == OWNER:
        for p in d.payments:
            if p.get("basis_number") in numbers or (code and p.get("client_code") == code):
                events.append({"t": p["_t"], "kind": "payment", "time": p["_t"].strftime("%d.%m.%Y %H:%M"),
                               "amount": p["amount"], "method": p.get("method"), "basis": p.get("basis_number")})
    events.sort(key=lambda e: e["t"], reverse=True)
    for e in events:
        e.pop("t")
    name = next((c.get("client") for c in calls if c.get("client")), "") or \
        next((o.get("client") for o in orders if o.get("client")), "")
    return {"client": name or "Номер без карточки клиента", "phone": d.phone_text(phone) if phone else "",
            "calls": len(calls), "orders": len(orders), "events": events[:300]}


def strip_money(obj, role: str):
    """Подстраховка: у маркетолога не должно уйти ни одного денежного поля."""
    if role == OWNER:
        return obj
    if isinstance(obj, dict):
        return {k: strip_money(v, role) for k, v in obj.items() if k not in MONEY_KEYS and not (
            isinstance(v, dict) and v.get("money"))}
    if isinstance(obj, list):
        return [strip_money(x, role) for x in obj if not (isinstance(x, dict) and x.get("money"))]
    return obj


def filter_options(d: Data) -> dict:
    return {"users": sorted({c.get("user") for c in d.calls if c.get("user")} |
                            {o.get("author") for o in d.orders if o.get("author")}),
            "lines": sorted({c["line_name"] for c in d.calls})}
