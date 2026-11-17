"""Клиенты и воронка - пожелания маркетолога (ноябрь 2026):

1. звонки по людям: «пропущено 120 звонков» -> «это 74 человека, 51 дозвонились, 9 так и не дозвонились»;
2. воронка: позвонил -> связались -> запись/ЗН -> приехал -> выдали, и на чём остановился каждый;
3. группы клиентов: новые, повторные, вернувшиеся, постоянные, уснувшие, ещё не обслуживались;
4. новые карточки контрагентов: сколько, кто завёл, возможные дубли;
5. состояние базы: что не заполнено и где хвосты.

Человек = карточка клиента. Звонок без карточки привязывается к карточке по номеру телефона
(если номер есть ровно в одной карточке), иначе человек - это номер телефона.

Визит - завершённый или выданный ЗН, кроме внутренних; несколько ЗН в один день - один визит.
Чего в Далионе нет - «думает», «дорого» и т.п.: это можно узнать только если менеджеры начнут
заполнять «Причину отказа» в ЗН.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .metrics import CALL_AFTER_DAYS, OPEN_ORDER_DAYS, OWNER, Data, Filters, _days, _incoming, _series, pct

CALLBACK_HOURS = 72
YEAR = 365
ACTIVE_WINDOW = 548          # «постоянный» - 2+ визита за 18 месяцев
STALE_BOOKING_DAYS = 30
PRELIM = "предвар"


def _fmt(t: datetime | date | None, time: bool = True) -> str:
    if not t:
        return ""
    if isinstance(t, datetime):
        return t.strftime("%d.%m.%Y %H:%M" if time else "%d.%m.%Y")
    return t.strftime("%d.%m.%Y")


def _connected(c: dict) -> bool:
    """Был разговор: принятый входящий или исходящий, где ответили."""
    return float(c.get("dur") or 0) > 0 and not c["missed"]


def _person_info(d: Data, key: str, cs: list[dict]) -> dict:
    code = key[2:] if key.startswith("c:") else ""
    last = cs[-1]
    name = (d.client_name(code) if code else "") or next((c.get("client") for c in reversed(cs) if c.get("client")), "")
    phone = next((c.get("phone") for c in reversed(cs) if c.get("phone")), "")
    if not phone and code:
        phone = (d.card_phones.get(code) or [""])[0]
    return {"id": last.get("id"), "client_code": code, "client": name or "", "phone": d.phone_text(phone)}


# ---------------------------------------------------------------------------- 1. звонки по людям
def call_people(d: Data, f: Filters) -> list[dict]:
    """Каждый человек, который звонил нам в период: сколько раз, сколько пропущено и чем кончилось."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in _incoming(d.calls_in(f)):
        groups[d.person(c)].append(c)
    rows = []
    for key, inc in groups.items():
        missed = [c for c in inc if c["missed"]]
        everything = d.calls_by_person.get(key, [])
        status, tries = "все звонки приняты", 0
        if missed:
            last_miss = missed[-1]["_t"]
            limit = last_miss + timedelta(hours=CALLBACK_HOURS)
            after = [c for c in everything if last_miss < c["_t"] <= limit]
            tries = sum(1 for c in everything if c.get("dir") == "out" and missed[0]["_t"] < c["_t"] <= limit)
            if any(_connected(c) for c in after):
                status = "пропустили, потом связались"
            elif tries:
                status = "перезванивали, не дозвонились"
            else:
                status = "не перезванивали"
        users = Counter(c.get("user") for c in inc if c.get("user"))
        rows.append({**_person_info(d, key, inc), "calls": len(inc), "missed": len(missed),
                     "answered": len(inc) - len(missed), "tries": tries, "status": status,
                     "first": _fmt(inc[0]["_t"]), "last": _fmt(inc[-1]["_t"]),
                     "user": ", ".join(u for u, _ in users.most_common(2))})
    rows.sort(key=lambda r: (-r["missed"], -r["calls"]))
    return rows


def people_summary(d: Data, f: Filters) -> dict:
    rows = call_people(d, f)
    inc = _incoming(d.calls_in(f))
    with_missed = [r for r in rows if r["missed"]]
    st = Counter(r["status"] for r in with_missed)
    dist = Counter(min(r["calls"], 5) for r in rows)
    return {
        "people": len(rows), "calls": len(inc), "missed_calls": sum(1 for c in inc if c["missed"]),
        "people_missed": len(with_missed),
        "reached": st["пропустили, потом связались"], "tried": st["перезванивали, не дозвонились"],
        "not_tried": st["не перезванивали"],
        "known": sum(1 for r in rows if r["client_code"]),
        "calls_per_person": round(len(inc) / len(rows), 1) if rows else None,
        "distribution": [{"label": f"{n} звонок" if n == 1 else f"{n} звонка" if n < 5 else "5 и больше",
                          "count": dist.get(n, 0)} for n in range(1, 6)],
    }


# ---------------------------------------------------------------------------- 2. записи и воронка
def is_booking(o: dict) -> bool:
    """Запись - ЗН на будущее: предварительный сейчас, или машина пришла не в день создания,
    или плановая дата начала работ позже дня создания."""
    created = o["_t"].date()
    return (PRELIM in (o.get("status") or "").lower()
            or (o["_in_work"] is not None and o["_in_work"].date() > created)
            or (o["_plan"] is not None and o["_plan"].date() > created))


def booking_outcome(o: dict, now: datetime) -> tuple[str, str]:
    """(итог, что сделать)."""
    status = (o.get("status") or "").lower()
    if (o.get("refusal") or "").strip():
        return f"отказ: {o['refusal']}", ""
    if o.get("deleted") in ("да", 1, True, "1"):
        return "отменена (ЗН удалён)", ""
    if o["_issued"] is not None or "отгруж" in status or "выдан" in status:
        return "приехал, выдан", ""
    if o["_in_work"] is not None or PRELIM not in status:
        return "приехал", ""
    when = o["_plan"] or o["_t"]
    if when.date() < now.date():
        return "не приехал", "позвонить, перенести запись или указать причину отказа"
    if when.date() == now.date():
        return "ждём сегодня", ""
    return "ждём", "напомнить клиенту накануне" if when.date() == now.date() + timedelta(days=1) else ""


def _last_action(d: Data, code: str, after: datetime | None = None) -> str:
    cs = [c for c in d.calls_by_person.get("c:" + code, []) if after is None or c["_t"] >= after]
    if not cs:
        return ""
    c = cs[-1]
    what = "пропущенный" if c["missed"] else {"in": "входящий", "out": "исходящий"}.get(c.get("dir"), "звонок")
    if c.get("dir") == "out" and not float(c.get("dur") or 0):
        what = "исходящий, не ответили"
    return f"{_fmt(c['_t'])} {what}" + (f" ({c['user']})" if c.get("user") else "")


def bookings(d: Data, f: Filters, now: datetime) -> list[dict]:
    rows = []
    for o in d.orders + d.deleted:
        if not f.has(o["_t"]) or not is_booking(o):
            continue
        who = o.get("manager") or o.get("author") or ""
        if f.user and f.user not in (o.get("manager"), o.get("author")):
            continue
        outcome, todo = booking_outcome(o, now)
        rows.append({"_sort": o["_t"], "number": o.get("number"), "created": _fmt(o["_t"]), "plan": _fmt(o["_plan"] or None),
                     "client": o.get("client") or "", "client_code": o.get("client_code") or "",
                     "car": o.get("car") or "", "who": who, "status": o.get("status") or "",
                     "outcome": outcome, "todo": todo, "source": o.get("source") or "",
                     "last": _last_action(d, o.get("client_code") or "", o["_t"]) if o.get("client_code") else ""})
    rows.sort(key=lambda r: r["_sort"], reverse=True)
    for r in rows:
        r.pop("_sort")
    return rows


OUTCOME_ORDER = ["не дозвонились", "поговорили, без карточки", "поговорили, записи нет", "звонил по машине в ремонте",
                 "запись отменена или отказ", "записан, не приехал", "записан, ждём", "приехал, в работе", "выдан"]
OUTCOME_HINT = {
    "не дозвонились": "Все звонки пропущены, и после них разговора не было",
    "поговорили, без карточки": "Поговорили, но номера нет ни в одной карточке - дальше не проследить. Завести карточку?",
    "поговорили, записи нет": "Разговор был, но за 14 дней не появилось ни ЗН, ни записи",
    "звонил по машине в ремонте": "В момент звонка у клиента был открытый ЗН - звонил по своей машине",
    "запись отменена или отказ": "Запись удалена или в ЗН указана причина отказа",
    "записан, не приехал": "Дата записи прошла, а машина в работу не переведена",
    "записан, ждём": "Запись на сегодня или позже",
    "приехал, в работе": "ЗН в работе или завершён, ещё не выдан",
    "выдан": "Машину выдали",
}
TODO = {
    "не дозвонились": "перезвонить",
    "поговорили, без карточки": "завести карточку, уточнить итог разговора",
    "поговорили, записи нет": "уточнить, почему не записался; при отказе - причину в ЗН",
    "записан, не приехал": "позвонить, перенести запись или указать причину отказа",
}


def funnel(d: Data, f: Filters, now: datetime) -> dict:
    """Каждый, кто позвонил в период, проходит этапы; итог - где остановился."""
    people: dict[str, list[dict]] = defaultdict(list)
    for c in _incoming(d.calls_in(f)):
        people[d.person(c)].append(c)
    rows = []
    for key, inc in people.items():
        first = inc[0]["_t"]
        everything = [c for c in d.calls_by_person.get(key, []) if first <= c["_t"] <= f.end + timedelta(hours=CALLBACK_HOURS)]
        talked = any(_connected(c) for c in everything)
        code = key[2:] if key.startswith("c:") else ""
        info = _person_info(d, key, inc)
        busy = after = None
        if code:
            orders = d.bookings_by_client.get(code, [])
            busy = next((o for o in orders if o.get("deleted") != "да" and o["_t"] <= first
                         and (o["_issued"] is None or o["_issued"] >= first)
                         and o["_t"] >= first - timedelta(days=OPEN_ORDER_DAYS)), None)
            after = next((o for o in orders if first - timedelta(hours=1) <= o["_t"] <= first + timedelta(days=CALL_AFTER_DAYS)), None)
        order = after
        if after is not None:
            booked, _ = booking_outcome(after, now)
            if booked.startswith(("отмен", "отказ")):
                outcome = "запись отменена или отказ"
            elif booked == "не приехал":
                outcome = "записан, не приехал"
            elif booked.startswith("ждём"):
                outcome = "записан, ждём"
            elif booked == "приехал, выдан":
                outcome = "выдан"
            else:
                outcome = "приехал, в работе"
        elif busy is not None:
            outcome, order = "звонил по машине в ремонте", busy
        elif not talked:
            outcome = "не дозвонились"
        elif not code:
            outcome = "поговорили, без карточки"
        else:
            outcome = "поговорили, записи нет"
        users = Counter(c.get("user") for c in everything if c.get("user"))
        rows.append({**info, "first": _fmt(first), "calls": len(inc), "outcome": outcome,
                     "who": (order or {}).get("manager") or (order or {}).get("author") or
                            (users.most_common(1)[0][0] if users else ""),
                     "order": (order or {}).get("number") or "", "status": (order or {}).get("status") or "",
                     "last": _last_action(d, code) if code else _fmt(everything[-1]["_t"]) if everything else "",
                     "todo": TODO.get(outcome, "")})
    counts = Counter(r["outcome"] for r in rows)
    n = len(rows)
    service = n - counts["звонил по машине в ремонте"]
    talked = service - counts["не дозвонились"]
    booked = talked - counts["поговорили, без карточки"] - counts["поговорили, записи нет"]
    came = counts["приехал, в работе"] + counts["выдан"]
    stages = [
        {"key": "f_all", "title": "Позвонили", "count": service,
         "hint": "Разные люди, звонившие в период (без тех, кто звонил по машине, уже стоящей в ремонте)"},
        {"key": "f_talked", "title": "Связались", "count": talked, "hint": "Был разговор: сразу или после перезвона"},
        {"key": "f_booked", "title": "Записались / есть ЗН", "count": booked,
         "hint": "В течение 14 дней после первого звонка появился ЗН или запись"},
        {"key": "f_came", "title": "Приехали", "count": came, "hint": "ЗН переведён в работу"},
        {"key": "f_issued", "title": "Выдали", "count": counts["выдан"], "hint": "Машину выдали"},
    ]
    for s in stages:
        s["pct"] = pct(s["count"], service)
    outcomes = [{"key": "o:" + k, "title": k, "count": counts.get(k, 0), "hint": OUTCOME_HINT[k],
                 "todo": TODO.get(k, "")} for k in OUTCOME_ORDER]
    return {"stages": stages, "outcomes": outcomes, "rows": rows}


def bookings_summary(d: Data, f: Filters, now: datetime) -> dict:
    rows = bookings(d, f, now)
    by = defaultdict(Counter)
    for r in rows:
        o = r["outcome"]
        group = ("отменена" if o.startswith(("отмен", "отказ")) else "не приехал" if o == "не приехал"
                 else "ждём" if o.startswith("ждём") else "приехал")
        by[r["who"] or "(не указан)"][group] += 1
        by[r["who"] or "(не указан)"]["всего"] += 1
    who = [{"name": k, "total": v["всего"], "came": v["приехал"], "no_show": v["не приехал"],
            "cancelled": v["отменена"], "waiting": v["ждём"], "came_pct": pct(v["приехал"], v["всего"] - v["ждём"])}
           for k, v in sorted(by.items(), key=lambda kv: -kv[1]["всего"])]
    oc = Counter(r["outcome"] if not r["outcome"].startswith("отказ") else "отказ (указана причина)" for r in rows)
    refusals = Counter(r["outcome"][7:] for r in rows if r["outcome"].startswith("отказ"))
    return {"total": len(rows), "outcomes": [{"title": k, "count": v} for k, v in oc.most_common()],
            "who": who, "refusals": [{"title": k, "count": v} for k, v in refusals.most_common()],
            "has_extra": not d.meta.get("errors", {}).get("Записи и отказы")}


# ---------------------------------------------------------------------------- 3. группы клиентов
def _prev(days: list[date], before: date) -> date | None:
    p = None
    for x in days:
        if x >= before:
            break
        p = x
    return p


def client_groups(d: Data, f: Filters) -> dict:
    if not d.visits:
        return {"available": False}
    d1, d2 = f.d1, f.d2
    period = {"new": [], "repeat": [], "returned": []}
    base = {"regular": [], "single": [], "sleeping": [], "lost": []}
    for code, days in d.visits.items():
        inside = [x for x in days if d1 <= x <= d2]
        if inside:
            prev = _prev(days, d1)
            if prev is None:
                period["new"].append(code)
            elif (d1 - prev).days <= YEAR:
                period["repeat"].append(code)
            else:
                period["returned"].append(code)
        upto = [x for x in days if x <= d2]
        if not upto:
            continue
        age = (d2 - upto[-1]).days
        if age <= YEAR:
            recent = sum(1 for x in upto if (d2 - x).days <= ACTIVE_WINDOW)
            base["regular" if recent >= 2 else "single"].append(code)
        elif age <= 2 * YEAR:
            base["sleeping"].append(code)
        else:
            base["lost"].append(code)
    never = [c for c, card in d.clients.items() if not card["deleted"] and c not in d.visits and f.has(card["_t"])]
    months = []
    m = date(d2.year, d2.month, 1)
    for _ in range(12):
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        cnt = Counter()
        for days in d.visits.values():
            inside = [x for x in days if m <= x < nxt]
            if not inside:
                continue
            prev = _prev(days, m)
            cnt["new" if prev is None else "repeat" if (m - prev).days <= YEAR else "returned"] += 1
        months.append({"month": m.strftime("%Y-%m"), **{k: cnt.get(k, 0) for k in ("new", "repeat", "returned")}})
        m = date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 1)
    months.reverse()
    groups = [
        {"key": "g_new", "title": "Новые", "count": len(period["new"]), "hint": "Первый визит за всё время - в этом периоде"},
        {"key": "g_repeat", "title": "Повторные", "count": len(period["repeat"]),
         "hint": "Приезжали в период, и прошлый визит был не больше года назад"},
        {"key": "g_returned", "title": "Вернувшиеся", "count": len(period["returned"]),
         "hint": "Приезжали в период после перерыва больше года"},
    ]
    state = [
        {"key": "g_regular", "title": "Постоянные", "count": len(base["regular"]),
         "hint": "2+ визита за 18 месяцев, последний - не больше года назад"},
        {"key": "g_single", "title": "Были один раз за год", "count": len(base["single"]),
         "hint": "Последний визит не больше года назад, но визит один за 18 месяцев - кандидаты в постоянные"},
        {"key": "g_sleeping", "title": "Уснувшие (1–2 года)", "count": len(base["sleeping"]),
         "hint": "Последний визит 1–2 года назад: их ещё можно вернуть звонком или рассылкой", "bad": True},
        {"key": "g_lost", "title": "Давно не были (2+ года)", "count": len(base["lost"]),
         "hint": "Последний визит больше двух лет назад"},
        {"key": "g_never", "title": "Карточка есть, визитов нет", "count": len(never),
         "hint": "Карточки, заведённые в период, у которых нет ни одного завершённого ЗН"},
    ]
    return {"available": True, "groups": groups, "state": state, "months": months,
            "_lists": {**{"g_" + k: v for k, v in period.items()}, **{"g_" + k: v for k, v in base.items()},
                       "g_never": never}, "date": d2.isoformat()}


def client_row(d: Data, code: str, ref: date | None = None) -> dict:
    days = d.visits.get(code, [])
    card = d.clients.get(code) or {}
    phone = (d.card_phones.get(code) or [""])[0]
    calls = d.calls_by_person.get("c:" + code, [])
    return {"client_code": code, "client": d.client_name(code), "phone": d.phone_text(phone),
            "visits": len(days), "first_visit": _fmt(days[0]) if days else "",
            "last_visit": _fmt(days[-1]) if days else "",
            "days_since": (ref - days[-1]).days if days and ref else None,
            "card": _fmt(card.get("_t"), False), "author": card.get("author") or "",
            "id": calls[-1].get("id") if calls else ""}


# ---------------------------------------------------------------------------- 4. новые карточки
def _norm_name(s: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]", "", (s or "").lower().replace("ё", "е"))


def duplicates(d: Data) -> dict:
    by_phone = [sorted(codes) for p, codes in d.phone_owners.items() if len(codes) > 1]
    names = defaultdict(list)
    for code, card in d.clients.items():
        n = _norm_name(card.get("name"))
        if not card["deleted"] and len(n) >= 6:
            names[n].append(code)
    by_name = [sorted(v) for v in names.values() if len(v) > 1]
    return {"phone": by_phone, "name": by_name}


def new_cards(d: Data, f: Filters) -> dict:
    cards = [c for c in d.clients.values() if f.has(c["_t"]) and (not f.user or c.get("author") == f.user)]
    dups = duplicates(d)
    dup_codes = {c for g in dups["phone"] + dups["name"] for c in g}
    authors = defaultdict(Counter)
    for c in cards:
        a = authors[c.get("author") or "(не указан)"]
        a["total"] += 1
        a["no_phone"] += not d.card_phones.get(c["code"])
        a["dup"] += c["code"] in dup_codes
        a["visited"] += c["code"] in d.visits
        a["deleted"] += c["deleted"]
    kinds = Counter(c.get("kind") or "не указан" for c in cards)
    return {
        "available": bool(d.clients), "total": len(cards),
        "days": [x.isoformat() for x in _days(f)], "series": _series(f, cards),
        "authors": [{"name": k, **{x: v.get(x, 0) for x in ("total", "no_phone", "dup", "visited", "deleted")}}
                    for k, v in sorted(authors.items(), key=lambda kv: -kv[1]["total"])],
        "kinds": [{"label": k, "value": v} for k, v in kinds.most_common()],
        "dup_phone": len(dups["phone"]), "dup_name": len(dups["name"]),
        "new_dups": sum(1 for c in cards if c["code"] in dup_codes),
    }


# ---------------------------------------------------------------------------- 5. состояние базы
def quality(d: Data, f: Filters, now: datetime) -> dict:
    live = [c for c in d.clients.values() if not c["deleted"]]
    orders = d.orders_in(f)
    inc = [c for c in _incoming(d.calls_in(f)) if not c["missed"]]
    stale = stale_bookings(d, now)
    dups = duplicates(d)
    items = [
        {"key": "q_no_phone", "title": "Карточки без телефона", "count": sum(1 for c in live if not d.card_phones.get(c["code"])),
         "of": len(live), "hint": "Во всей базе. Без номера звонок не свяжется с клиентом"},
        {"key": "q_dup_phone", "title": "Один номер в нескольких карточках", "count": len(dups["phone"]),
         "hint": "Групп карточек с общим номером - возможные дубли (или семья/организация)"},
        {"key": "q_dup_name", "title": "Одинаковое название у разных карточек", "count": len(dups["name"]),
         "hint": "Групп карточек с одинаковым названием (без учёта пробелов и регистра)"},
        {"key": "unknown_numbers", "title": "Звонки с номеров без карточки", "count": sum(1 for c in inc if d.person(c).startswith("p:")),
         "of": len(inc), "hint": "Принятые входящие за период, номер которых нет ни в одной карточке"},
        {"key": "no_source", "title": "ЗН без источника рекламы", "count": sum(1 for o in orders if not (o.get("source") or "").strip()),
         "of": len(orders), "hint": "За период"},
        {"key": "q_no_manager", "title": "ЗН без менеджера", "count": sum(1 for o in orders if not (o.get("manager") or "").strip()),
         "of": len(orders), "hint": "За период. Без менеджера не понять, кто вёл клиента"},
        {"key": "q_stale", "title": "Предварительные ЗН висят больше 30 дней", "count": len(stale),
         "hint": "Статус «Предварительный», дата записи прошла больше 30 дней назад: закрыть или удалить вручную"},
        {"key": "q_no_refusal", "title": "Отменённые записи без причины", "count": sum(
            1 for o in d.deleted if f.has(o["_t"]) and not (o.get("refusal") or "").strip()),
         "hint": "Удалённые за период ЗН без «Причины отказа» - не узнать, почему клиент не приехал"},
    ]
    for i in items:
        i["pct"] = pct(i["count"], i["of"]) if i.get("of") else None
    return {"items": items, "cards": len(live), "visits_clients": len(d.visits),
            "first_visit": min((v[0] for v in d.visits.values()), default=None),
            "collected_at": d.meta.get("collected_at"), "updated_at": d.meta.get("updated_at"),
            "errors": d.meta.get("errors", {})}


def stale_bookings(d: Data, now: datetime) -> list[dict]:
    return [o for o in d.orders if PRELIM in (o.get("status") or "").lower()
            and ((o["_plan"] or o["_t"]) < now - timedelta(days=STALE_BOOKING_DAYS))]


# ---------------------------------------------------------------------------- списки
PEOPLE_COLS = [{"key": "client", "title": "Клиент"}, {"key": "phone", "title": "Номер"},
               {"key": "calls", "title": "Звонков", "num": True}, {"key": "missed", "title": "Пропущено", "num": True},
               {"key": "tries", "title": "Перезванивали", "num": True}, {"key": "status", "title": "Итог"},
               {"key": "first", "title": "Первый"}, {"key": "last", "title": "Последний"}, {"key": "user", "title": "Менеджер"}]
FUNNEL_COLS = [{"key": "client", "title": "Клиент"}, {"key": "phone", "title": "Номер"}, {"key": "first", "title": "Позвонил"},
               {"key": "outcome", "title": "Где остановился"}, {"key": "who", "title": "Ответственный"},
               {"key": "order", "title": "ЗН"}, {"key": "last", "title": "Последнее действие"}, {"key": "todo", "title": "Что сделать"}]
BOOKING_COLS = [{"key": "number", "title": "№ ЗН"}, {"key": "created", "title": "Создан"}, {"key": "plan", "title": "Записан на"},
                {"key": "client", "title": "Клиент"}, {"key": "car", "title": "Автомобиль"}, {"key": "who", "title": "Ответственный"},
                {"key": "outcome", "title": "Итог"}, {"key": "last", "title": "Последний звонок"}, {"key": "todo", "title": "Что сделать"}]
CLIENT_COLS = [{"key": "client", "title": "Клиент"}, {"key": "phone", "title": "Номер"}, {"key": "visits", "title": "Визитов", "num": True},
               {"key": "first_visit", "title": "Первый визит"}, {"key": "last_visit", "title": "Последний визит"},
               {"key": "days_since", "title": "Дней назад", "num": True}, {"key": "card", "title": "Карточка заведена"},
               {"key": "author", "title": "Кем"}]
DUP_COLS = [{"key": "group", "title": "Группа", "num": True}] + CLIENT_COLS[:3] + CLIENT_COLS[4:5] + CLIENT_COLS[6:]

TITLES = {"people_all": "Кто звонил: по людям", "people_missed": "Люди с пропущенными звонками",
          "people_not_reached": "Пропустили и так и не связались", "bookings": "Записи (предварительные ЗН)",
          "bookings_noshow": "Записаны, но не приехали", "cards_new": "Новые карточки",
          "q_no_phone": "Карточки без телефона", "q_dup_phone": "Один номер в нескольких карточках",
          "q_dup_name": "Одинаковое название у разных карточек", "q_stale": "Предварительные ЗН висят больше 30 дней",
          "q_no_manager": "ЗН без менеджера", "q_no_refusal": "Отменённые записи без причины",
          "g_new": "Новые клиенты", "g_repeat": "Повторные клиенты", "g_returned": "Вернувшиеся клиенты",
          "g_regular": "Постоянные клиенты", "g_single": "Были один раз за год", "g_sleeping": "Уснувшие клиенты (1–2 года)",
          "g_lost": "Давно не были (2+ года)", "g_never": "Карточка есть, визитов нет"}


def is_people_key(key: str) -> bool:
    return key in TITLES or key.startswith(("f_", "o:"))


def list_rows(d: Data, key: str, f: Filters, role: str, now: datetime) -> dict:
    from .metrics import order_row  # noqa: PLC0415
    if key.startswith("people_"):
        rows = call_people(d, f)
        if key == "people_missed":
            rows = [r for r in rows if r["missed"]]
        elif key == "people_not_reached":
            rows = [r for r in rows if r["status"] in ("перезванивали, не дозвонились", "не перезванивали")]
        return {"title": TITLES[key], "kind": "table", "cols": PEOPLE_COLS, "rows": rows}
    if key.startswith(("f_", "o:")):
        fn = funnel(d, f, now)
        if key.startswith("o:"):
            title, rows = key[2:], [r for r in fn["rows"] if r["outcome"] == key[2:]]
        else:
            st = {"f_all": OUTCOME_ORDER[:3] + OUTCOME_ORDER[4:], "f_talked": OUTCOME_ORDER[1:3] + OUTCOME_ORDER[4:],
                  "f_booked": OUTCOME_ORDER[4:], "f_came": OUTCOME_ORDER[7:], "f_issued": OUTCOME_ORDER[8:]}[key]
            title = next(s["title"] for s in fn["stages"] if s["key"] == key)
            rows = [r for r in fn["rows"] if r["outcome"] in st]
        return {"title": f"Воронка: {title}", "kind": "table", "cols": FUNNEL_COLS, "rows": rows}
    if key.startswith("bookings"):
        rows = bookings(d, f, now)
        if key == "bookings_noshow":
            rows = [r for r in rows if r["outcome"] == "не приехал"]
        return {"title": TITLES[key], "kind": "table", "cols": BOOKING_COLS, "rows": rows}
    if key.startswith("g_"):
        codes = client_groups(d, f).get("_lists", {}).get(key, [])
        rows = sorted((client_row(d, c, f.d2) for c in codes), key=lambda r: r["days_since"] if r["days_since"] is not None else 10**6)
        return {"title": TITLES[key], "kind": "table", "cols": CLIENT_COLS, "rows": rows}
    if key == "cards_new":
        rows = [client_row(d, c["code"], f.d2) for c in sorted(d.clients.values(), key=lambda c: c["_t"] or datetime.min,
                                                                   reverse=True)
                if f.has(c["_t"]) and (not f.user or c.get("author") == f.user)]
        return {"title": TITLES[key], "kind": "table", "cols": CLIENT_COLS, "rows": rows}
    if key == "q_no_phone":
        rows = [client_row(d, c, f.d2) for c, card in d.clients.items() if not card["deleted"] and not d.card_phones.get(c)]
        return {"title": TITLES[key], "kind": "table", "cols": CLIENT_COLS, "rows": rows}
    if key in ("q_dup_phone", "q_dup_name"):
        rows = []
        for i, g in enumerate(duplicates(d)["phone" if key == "q_dup_phone" else "name"], 1):
            rows += [{"group": i, **client_row(d, c, f.d2)} for c in g]
        return {"title": TITLES[key], "kind": "table", "cols": DUP_COLS, "rows": rows}
    if key == "q_stale":
        return {"title": TITLES[key], "kind": "orders", "rows": [order_row(o, role) for o in stale_bookings(d, now)]}
    if key == "q_no_manager":
        return {"title": TITLES[key], "kind": "orders",
                "rows": [order_row(o, role) for o in d.orders_in(f) if not (o.get("manager") or "").strip()]}
    if key == "q_no_refusal":
        return {"title": TITLES[key], "kind": "orders", "rows": [order_row(o, role) for o in d.deleted
                                                                  if f.has(o["_t"]) and not (o.get("refusal") or "").strip()]}
    raise KeyError(key)


# ---------------------------------------------------------------------------- карточка клиента для истории
def client_card(d: Data, code: str, today: date) -> dict:
    if not code:
        return {}
    days = d.visits.get(code, [])
    card = d.clients.get(code) or {}
    group = ""
    if days:
        age = (today - days[-1]).days
        recent = sum(1 for x in days if (today - x).days <= ACTIVE_WINDOW)
        group = ("постоянный" if age <= YEAR and recent >= 2 else "был один раз за год" if age <= YEAR
                 else "уснувший (1–2 года)" if age <= 2 * YEAR else "давно не был (2+ года)")
    elif card:
        group = "ещё не обслуживался"
    return {"visits": len(days), "first_visit": _fmt(days[0]) if days else "", "last_visit": _fmt(days[-1]) if days else "",
            "group": group, "card": _fmt(card.get("_t"), False), "author": card.get("author") or "",
            "kind": card.get("kind") or "", "source": card.get("source") or "",
            "phones": [d.phone_text(p) for p in d.card_phones.get(code, [])]}


__all__ = ["OWNER"]
