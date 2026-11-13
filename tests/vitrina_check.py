"""Витрина: сбор из «1С» (поддельной), расчёт показателей, сайт с паролями и ролями.

    python tests/vitrina_check.py
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.marketing.export import Directions  # noqa: E402
from avtootchet.vitrina import collect as C  # noqa: E402
from avtootchet.vitrina import metrics as M  # noqa: E402
from avtootchet.vitrina.db import read_db, write_db  # noqa: E402
from avtootchet.vitrina.demo import make_demo  # noqa: E402
from avtootchet.vitrina.server import App, Store, make_lock, serve  # noqa: E402

NOW = datetime(2026, 11, 13, 13, 0)
EMPTY = datetime(1, 1, 1)


# ------------------------------------------------------------------ поддельная 1С
class Sel:
    def __init__(self, rows):
        self.rows, self.i = rows, -1

    def Next(self):  # noqa: N802
        self.i += 1
        if self.i < len(self.rows):
            for k, v in self.rows[self.i].items():
                setattr(self, k, v)
            return True
        return False


class Query:
    def __init__(self, conn):
        self.conn, self.Text = conn, ""

    def Execute(self):  # noqa: N802
        conn = self.conn
        assert "&Начало" not in self.Text, "даты должны быть подставлены литералом"
        for marker, rows in conn.answers.items():
            if marker in self.Text:
                if isinstance(rows, Exception):
                    raise rows
                return type("R", (), {"Choose": lambda _s: Sel(rows)})()
        raise AssertionError("неизвестный запрос")


class Conn:
    def __init__(self, answers):
        self.answers = answers

    def NewObject(self, name):  # noqa: N802
        assert name == "Запрос"
        return Query(self)


def call(uid, t, code, dur, frm="", to="", client="Клиент А", ccode="0001", end=None):
    return {"УИД": uid, "Начало": t, "Окончание": end or t, "Направление": code, "Длительность": dur,
            "Менеджер": "Менеджер 1", "КлиентКод": ccode, "Клиент": client, "Линия": "+7 4932 00-00-21",
            "С": frm, "На": to}


CALLS = [
    call("c1", datetime(2026, 11, 10, 10, 0), 0, 0, frm="+7 (910) 111-22-33"),               # пропущен
    call("c2", datetime(2026, 11, 10, 10, 30), 1, 45, frm="200", to="89101112233"),          # перезвон
    call("c3", datetime(2026, 11, 11, 11, 0), 0, 60, frm="+79105556677", client="Клиент Б", ccode="0002",
         end=datetime(2026, 11, 11, 11, 1, 15)),                                             # ждал 15 с
    call("c4", datetime(2026, 11, 12, 9, 0), 0, 0, frm="+79107770000", client="", ccode=""),  # не перезвонили
]
ORDERS = [
    {"Номер": "ЗН1", "Дата": datetime(2026, 11, 10, 12), "Создан": datetime(2026, 11, 10, 12), "Автор": "Менеджер 1",
     "Тип": "СЕРВИС", "Статус": "Выдан", "Источник": "", "КлиентКод": "0001", "Клиент": "Клиент А",
     "Автомобиль": "Авто", "Мастер": "Мастер", "ВРаботе": EMPTY, "Завершен": EMPTY,
     "Выдан": datetime(2026, 11, 11, 17), "Повтор": False, "Проведен": True, "Сумма": 10000, "Работы": 6000,
     "Запчасти": 4000, "Материалы": 0},
]
PAYMENTS = [
    {"Знак": 1, "Дата": datetime(2026, 11, 11, 17), "Номер": "П1", "Сумма": 7000, "Касса": "Касса Эквайринг",
     "Подразделение": "Сервис", "КлиентКод": "0001", "Клиент": "Клиент А", "ВидОснования": "ЗН",
     "НомерОснования": "ЗН1"},
    {"Знак": 1, "Дата": datetime(2026, 11, 11, 18), "Номер": "П2", "Сумма": 500, "Касса": "Вывод ДС",
     "Подразделение": "", "КлиентКод": "", "Клиент": "", "ВидОснования": "РН", "НомерОснования": "Р1"},
]

conn = Conn({"пз_ИсторияЗвонков": CALLS, "Документ.ЗаказНаряд КАК З": ORDERS, "Документ.ПриходДенег КАК П": PAYMENTS,
             "ЗаказНаряд.Услуги КАК У": RuntimeError("Поле не найдено «Работа»"),
             "Справочник.Напоминания": []})
tables, meta = C.collect(conn, 6, Directions.parse("0", "1"), now=NOW)
assert meta["errors"] == {"Работы": "Поле не найдено «Работа»"}, meta["errors"]   # раздел упал - остальные есть
assert meta["from"] == "2026-05-01", meta["from"]
c = {r["id"]: r for r in tables["calls"]}
assert c["c1"]["missed"] and c["c1"]["callback"] == "да" and c["c1"]["callback_min"] == 30.0
assert c["c1"]["phone"] == "9101112233"
assert c["c3"]["wait"] == 15.0, c["c3"]
assert c["c4"]["callback"] == "нет"
assert [p["number"] for p in tables["payments"]] == ["П1"]            # «Вывод ДС» - не выручка
assert tables["payments"][0]["method"] == "Терминал"
print("сбор: OK")

tmp = Path(tempfile.mkdtemp())
write_db(tmp / "v.db", tables, meta)
t2, m2 = read_db(tmp / "v.db")
assert m2["errors"] == meta["errors"] and len(t2["calls"]) == 4

d = M.Data.from_tables(t2, m2, {"21": "Городская"})
f = M.Filters(date(2026, 11, 10), date(2026, 11, 13))
k = M.call_kpis(d, f)
assert (k["incoming"], k["missed"], k["no_callback"], k["outgoing"]) == (3, 2, 1, 1), k
unpaid = M.unpaid_orders(d, f)
assert len(unpaid) == 1 and unpaid[0]["debt"] == 3000, unpaid
assert M.money_kpis(d, f) == {"revenue": 7000.0, "avg_check": 10000.0}
# Клиент Б поговорил 60 с, ЗН за 14 дней нет и машина не в ремонте -> обращение без ЗН;
# Клиент А - ЗН есть
lost = M.lost_requests(d, d.calls_in(f))
assert [x["client_code"] for x in lost] == ["0002"], lost
assert d.calls[0]["line_name"] == "Городская"
tel = M.telephony(d, f)
assert tel["wait_avg"] == 15 and tel["callback_median"] == 30
assert M.mask_phone("9101112233") == "+7 ··· ···-22-33"
print("показатели: OK")

# ------------------------------------------------------------------ роли: у маркетолога денег нет
dt, dm = make_demo(NOW)
demo = M.Data.from_tables(dt, dm)
f30 = M.Filters(date(2026, 10, 15), date(2026, 11, 13))
home_m = M.strip_money(M.home(demo, f30, M.MARKETER, NOW), M.MARKETER)
text = json.dumps(home_m, ensure_ascii=False)
assert "Выручка" not in text and '"revenue"' not in text and '"amount"' not in text
assert not any(p["key"] == "unpaid" for p in home_m["problems"])
lst = M.strip_money(M.list_rows(demo, "orders", f30, M.MARKETER, NOW), M.MARKETER)
assert lst["rows"] and all("amount" not in r for r in lst["rows"])
try:
    M.list_rows(demo, "unpaid", f30, M.MARKETER, NOW)
    raise AssertionError("маркетологу нельзя список неоплаченных")
except PermissionError:
    pass
for key, *_ in M.PROBLEMS:
    M.list_rows(demo, key, f30, M.OWNER, NOW)
M.finance(demo, f30)
M.managers(demo, f30, M.OWNER)
path = M.client_path(demo, M.MARKETER, code=demo.orders[0]["client_code"])
assert all(e["kind"] != "payment" for e in path["events"])
print("роли: OK")

# ------------------------------------------------------------------ сайт
write_db(tmp / "demo.db", dt, dm)
app = App(Store(tmp / "demo.db"), {"owner": make_lock("owner-pass"), "marketer": make_lock("market-pass")},
          ROOT / "avtootchet" / "vitrina" / "web", now=lambda: NOW)
httpd = serve(app, "127.0.0.1", 0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def req(path, data=None, cookie=""):
    r = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                               headers={"Cookie": cookie, "Content-Type": "application/json"},
                               method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


code, body, hdr = req("/")
assert code == 200 and b"ACMineevo" in body and "default-src 'self'" in hdr["Content-Security-Policy"]
assert req("/api/home")[0] == 401
assert req("/api/login", {"password": "wrong"})[0] == 401


def login(pw):
    code, body, hdr = req("/api/login", {"password": pw})
    assert code == 200, body
    return hdr["Set-Cookie"].split(";")[0]


owner, market = login("owner-pass"), login("market-pass")
code, body, _ = req("/api/home?from=2026-10-15&to=2026-11-13", cookie=owner)
assert code == 200 and "Выручка" in body.decode()
code, body, _ = req("/api/home?from=2026-10-15&to=2026-11-13", cookie=market)
assert code == 200 and "Выручка" not in body.decode() and '"amount"' not in body.decode()
assert req("/api/finance", cookie=market)[0] == 403
assert req("/api/finance", cookie=owner)[0] == 200
assert req("/api/list?key=unpaid", cookie=market)[0] == 403
assert req("/api/list?key=nope", cookie=owner)[0] == 404
assert req("/api/meta", cookie=owner)[0] == 200
req("/api/logout", {}, cookie=owner)
assert req("/api/home", cookie=owner)[0] == 401
# подбор пароля: после 5 неверных - пауза даже для верного
for _ in range(5):
    req("/api/login", {"password": "x"})
assert req("/api/login", {"password": "owner-pass"})[0] == 429
httpd.shutdown()
print("сайт: OK")
print("Витрина: все проверки прошли")
