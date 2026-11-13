"""Демо-данные витрины: выдуманные клиенты, звонки и заказ-наряды за 6 месяцев.
Нужны, чтобы показать сайт без Далиона и проверять его в тестах. Ни одного настоящего имени
и номера здесь нет."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

from .db import write_db

USERS = ["Менеджер Иванова", "Менеджер Петров"]
MASTERS = ["Мастер Орлов", "Мастер Сидоров", "Мастер Кузнецов"]
LINES = ["+7 (4932) 00-00-21", "+7 (4932) 00-00-11", "8 900 000-00-45"]
SOURCES = ["Яндекс Карты", "2ГИС", "Авито", "Сайт", "Рекомендация", "Повторный клиент"]
WORKS = [("Замена масла ДВС", 1200), ("Диагностика подвески", 900), ("Замена колодок передних", 1800),
         ("Шиномонтаж R15-R17", 2400), ("Компьютерная диагностика", 1500), ("Замена ремня ГРМ", 7500),
         ("Развал-схождение", 2200), ("Замена амортизаторов", 4800), ("Ремонт глушителя (сварка)", 3500),
         ("Заправка кондиционера", 2500), ("Замена свечей", 1100), ("ТО-1", 4500)]
STATUSES = ["Выдан", "Выдан", "Выдан", "Выдан", "В работе", "Завершен", "Отменен"]
BOXES = ["Касса Нал", "Касса Эквайринг", "Касса QR", "Касса БЧ", "Касса Нал (М)"]


def _iso(t):
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else ""


def make_demo(now: datetime | None = None, months: int = 6, seed: int = 7) -> tuple[dict, dict]:
    rnd = random.Random(seed)
    now = now or datetime.now().replace(microsecond=0)
    start = (now - timedelta(days=months * 31)).replace(day=1, hour=0, minute=0, second=0)
    clients = [{"code": f"{i:06d}", "name": f"Клиент {i:03d}", "phone": f"9{rnd.randint(10**8, 10**9 - 1)}"}
               for i in range(1, 700)]
    calls, orders, payments, works, tasks = [], [], [], [], []
    uid = 0
    day = start
    while day.date() <= now.date():
        weekend = day.weekday() >= 5
        n_in = rnd.randint(8, 18) if weekend else rnd.randint(40, 70)
        for _ in range(n_in):
            hour = rnd.choices(range(8, 20), weights=[3, 9, 10, 8, 6, 5, 7, 8, 6, 5, 3, 1])[0]
            t = day.replace(hour=hour, minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
            if t > now:
                continue
            known = rnd.random() < 0.77
            cl = rnd.choice(clients) if known else None
            phone = cl["phone"] if cl else f"9{rnd.randint(10**8, 10**9 - 1)}"
            busy = hour in (9, 10, 15, 16)
            missed = rnd.random() < (0.38 if busy else 0.2)
            dur = 0 if missed else rnd.choice([rnd.randint(3, 9), rnd.randint(20, 240), rnd.randint(30, 400)])
            uid += 1
            c = {"id": f"d{uid}", "start": _iso(t), "end": _iso(t + timedelta(seconds=dur + rnd.randint(4, 30))),
                 "dir": "in", "dur": dur, "wait": "" if missed else rnd.randint(4, 30), "missed": missed,
                 "callback": "", "callback_min": "", "user": rnd.choice(USERS),
                 "client_code": cl["code"] if cl else "", "client": cl["name"] if cl else "",
                 "line": rnd.choices(LINES, weights=[50, 37, 13])[0], "phone": phone}
            if missed:
                r = rnd.random()
                if r < 0.62:
                    m = rnd.choice([rnd.randint(2, 15), rnd.randint(15, 60), rnd.randint(60, 300), rnd.randint(300, 1500)])
                    c["callback"], c["callback_min"] = "да", m
                    uid += 1
                    bt = t + timedelta(minutes=m)
                    if bt <= now:
                        calls.append({**c, "id": f"d{uid}", "start": _iso(bt), "end": _iso(bt + timedelta(seconds=90)),
                                      "dir": "out", "dur": rnd.randint(20, 200), "wait": "", "missed": False,
                                      "callback": "", "callback_min": ""})
                else:
                    c["callback"] = "нет"
            calls.append(c)
        for _ in range(rnd.randint(5, 15) if not weekend else 1):
            uid += 1
            cl = rnd.choice(clients)
            t = day.replace(hour=rnd.randint(9, 18), minute=rnd.randint(0, 59))
            if t <= now:
                dur = rnd.choice([0, rnd.randint(20, 180)])
                calls.append({"id": f"d{uid}", "start": _iso(t), "end": _iso(t + timedelta(seconds=dur)), "dir": "out",
                              "dur": dur, "wait": "", "missed": False, "callback": "", "callback_min": "",
                              "user": rnd.choice(USERS), "client_code": cl["code"], "client": cl["name"],
                              "line": LINES[0], "phone": cl["phone"]})
        # заказ-наряды
        for _ in range(rnd.randint(1, 4) if weekend else rnd.randint(9, 16)):
            t = day.replace(hour=rnd.randint(8, 18), minute=rnd.randint(0, 59))
            if t > now:
                continue
            cl = rnd.choice(clients)
            status = rnd.choice(STATUSES)
            age = (now - t).days
            if age < 3 and status == "Выдан":
                status = "В работе"
            if age > 20 and status == "В работе" and rnd.random() < 0.7:
                status = "Выдан"
            chosen = rnd.sample(WORKS, rnd.randint(1, 3))
            w = sum(p for _, p in chosen) * rnd.uniform(0.9, 1.4)
            parts = rnd.choice([0, rnd.uniform(500, 25000)])
            mat = rnd.uniform(0, 1500)
            amount = round(w + parts + mat, 2) if status != "Отменен" else 0
            issued = t + timedelta(hours=rnd.randint(2, 96)) if status == "Выдан" else None
            if issued and issued > now:
                issued = now - timedelta(hours=1)
            num = f"26СЧ{len(orders) + 1:05d}"
            orders.append({"number": num, "date": _iso(t), "created": _iso(t), "author": rnd.choice(USERS + ["Мастер Орлов"]),
                           "type": rnd.choice(["СЕРВИС", "СЕРВИС", "МЕТАЛЛООБРАБОТКА"]), "status": status,
                           "source": rnd.choice(SOURCES) if rnd.random() < 0.12 else "",
                           "client_code": cl["code"], "client": cl["name"], "car": rnd.choice(["Lada Vesta", "Kia Rio", "Hyundai Solaris", "Renault Logan", "Toyota Camry"]),
                           "master": rnd.choice(MASTERS), "in_work": _iso(t + timedelta(hours=1)),
                           "done": _iso(issued - timedelta(hours=1)) if issued else "", "issued": _iso(issued),
                           "repeat": "", "posted": "да", "amount": amount, "works": round(w, 2) if amount else 0,
                           "parts": round(parts, 2) if amount else 0, "materials": round(mat, 2) if amount else 0})
            for name, price in chosen if amount else []:
                works.append({"day": t.strftime("%Y-%m-%d"), "work": name, "orders": 1,
                              "amount": round(price * rnd.uniform(0.9, 1.4), 2)})
            if issued and amount:
                pay = amount if rnd.random() < 0.94 else round(amount * rnd.uniform(0.3, 0.8), 2)
                box = rnd.choices(BOXES, weights=[25, 45, 15, 5, 10])[0]
                payments.append({"date": _iso(issued), "number": f"П{len(payments) + 1}", "amount": pay,
                                 "cashbox": box, "method": {"Касса Нал": "Наличные с чеком", "Касса Эквайринг": "Терминал",
                                                            "Касса QR": "QR код", "Касса БЧ": "Наличные без чека",
                                                            "Касса Нал (М)": "Наличные с чеком"}[box],
                                 "direction": "Металлообработка" if "(М)" in box else "Автосервис",
                                 "client_code": cl["code"], "client": cl["name"], "basis_kind": "ЗН",
                                 "basis_number": num})
        if not weekend:
            for _ in range(rnd.randint(1, 6)):
                t = day.replace(hour=rnd.randint(9, 18))
                if t > now:
                    continue
                done = rnd.random() < 0.9 or (now - t).days > 30
                tasks.append({"created": _iso(t), "type": "Задание", "is_task": True, "executor": rnd.choice(USERS),
                              "done": done, "done_at": _iso(t + timedelta(hours=5)) if done else "",
                              "hours_to_done": 5 if done else ""})
        day += timedelta(days=1)
    tables = {"calls": calls, "orders": orders, "payments": payments, "works": works, "tasks": tasks}
    meta = {"collected_at": _iso(now), "from": start.strftime("%Y-%m-%d"), "to": now.strftime("%Y-%m-%d"),
            "errors": {}, "demo": True, "counts": {k: len(v) for k, v in tables.items()}}
    return tables, meta


def write_demo(path: Path, now: datetime | None = None) -> dict:
    tables, meta = make_demo(now)
    write_db(path, tables, meta)
    return meta
