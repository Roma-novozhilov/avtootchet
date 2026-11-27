"""Витрина: сбор из «1С» (поддельной), расчёт показателей, сайт с паролями и ролями.

    python tests/vitrina_check.py
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
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
from avtootchet.vitrina import runner as RN  # noqa: E402

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


class Coll:
    def __init__(self, items):
        self.items = items

    def Count(self):  # noqa: N802
        return len(self.items)

    def Get(self, i):  # noqa: N802
        return self.items[i]


class FType:
    def __init__(self, text, refs=()):
        self.text, self.refs = text, list(refs)

    def Types(self):  # noqa: N802
        return Coll(self.refs or [self.text])


class FAttr:
    def __init__(self, name, syn, ftype):
        self.Name, self.Synonym, self.Type = name, syn, ftype


class FRef:
    def __init__(self, full):
        self.full = full

    def FullName(self):  # noqa: N802
        return self.full


class FRegs:
    def Find(self, name):  # noqa: N802
        if name != "пз_ИсторияЗвонков":
            return None
        return type("Reg", (), {"Dimensions": Coll([]), "Resources": Coll([]), "Attributes": Coll([
            FAttr("ПутьКФайлу", "Путь к файлу записи", FType("Строка")),
            FAttr("Комментарий", "", FType("Строка")),
            FAttr("Контрагент", "Контрагент", FType("Справочник ссылка.Контрагенты", ["Справочник.Контрагенты"])),
            FAttr("Странное", "", FType("ХранилищеЗначения")),
        ])})()


class FDocs:
    def Find(self, name):  # noqa: N802
        if name != "ЗаказНаряд":
            return None
        return type("Doc", (), {"Attributes": Coll([FAttr("Менеджер", "Менеджер", FType("Справочник ссылка.Пользователи")),
                                                     FAttr("ВидЗН", "Вид", FType("Справочник ссылка.ВидыЗН"))])})()


class FMeta:
    InformationRegisters = FRegs()
    Documents = FDocs()

    def FindByType(self, t):  # noqa: N802
        return FRef(t) if "." in t else None


class Conn:
    Metadata = FMeta()

    def __init__(self, answers):
        self.answers = answers

    def NewObject(self, name):  # noqa: N802
        assert name == "Запрос"
        return Query(self)

    def String(self, v):  # noqa: N802
        return getattr(v, "text", str(v))


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

EXTRA = [
    {"Номер": "ЗН1", "Дата": datetime(2026, 11, 10, 12), "Удален": False, "План": EMPTY, "Отказ": "", "Менеджер": "Менеджер 1",
     "Создан": datetime(2026, 11, 10, 12), "Автор": "Менеджер 1", "Тип": "СЕРВИС", "Статус": "Отгружен", "Источник": "",
     "КлиентКод": "0001", "Клиент": "Клиент А", "Автомобиль": "Авто", "Мастер": "Мастер"},
    {"Номер": "ЗН2", "Дата": datetime(2026, 11, 11, 12), "Удален": True, "План": datetime(2026, 11, 12, 10),
     "Отказ": "Дорого", "Менеджер": "", "Создан": datetime(2026, 11, 11, 11, 5), "Автор": "Менеджер 1", "Тип": "СЕРВИС",
     "Статус": "Предварительный", "Источник": "", "КлиентКод": "0002", "Клиент": "Клиент Б", "Автомобиль": "Авто 2",
     "Мастер": ""},
]
VISITS = [{"КлиентКод": "0001", "День": datetime(2019, 5, 1), "Тип": "СЕРВИС"},
          {"КлиентКод": "0001", "День": datetime(2026, 11, 11), "Тип": "СЕРВИС"},
          {"КлиентКод": "0003", "День": datetime(2026, 1, 15), "Тип": "Внутренний"},
          {"КлиентКод": "", "День": datetime(2026, 1, 15), "Тип": "СЕРВИС"}]
CLIENTS = [{"Код": "0001", "Наименование": "Клиент А", "Создан": EMPTY, "Автор": "", "Вид": "Покупатель", "Источник": "",
            "Удален": False},
           {"Код": "0002", "Наименование": "Клиент Б", "Создан": datetime(2026, 11, 11, 11), "Автор": "Менеджер 1",
            "Вид": "Покупатель", "Источник": "Авито", "Удален": False},
           {"Код": "0005", "Наименование": "Клиент  б", "Создан": datetime(2026, 11, 12, 9), "Автор": "Менеджер 1",
            "Вид": "Покупатель", "Источник": "", "Удален": False}]
PHONES = [{"Код": "0001", "Телефон": "+7 (910) 111-22-33"}, {"Код": "0005", "Телефон": "8-910-777-00-00"},
          {"Код": "0001", "Телефон": "89101112233"}]

KINDS = [{"Номер": "ЗН1", "Вид": "Гарантия"}]
PAID = [{"Знак": 1, "Номер": "ЗН1", "Сумма": 7000}, {"Знак": 1, "Номер": "ЗН1", "Сумма": 1500},   # касса + р/с
        {"Знак": -1, "Номер": "ЗН1", "Сумма": 500}, {"Знак": 1, "Номер": None, "Сумма": 100}]
conn = Conn({"ДенежныеСредства.Остатки": [
                 {"Счет": "Касса сервиса", "Вид": "Касса", "Организация": "ИП Н", "Карты": False, "Валюта": "руб.", "Остаток": 1500.5},
                 {"Счет": "Эквайринг", "Вид": "Касса", "Организация": "ИП Н", "Карты": True, "Валюта": "руб.", "Остаток": 300},
                 {"Счет": "Р/с Сбер", "Вид": "Расчётный счёт", "Организация": "", "Карты": False, "Валюта": "руб.", "Остаток": 0.001},
                 {"Счет": "Р/с Альфа", "Вид": "Расчётный счёт", "Организация": "", "Карты": False, "Валюта": "руб.", "Остаток": 2000}],
             "ПРЕДСТАВЛЕНИЕ(З.ВидЗН) КАК Вид": KINDS,
             "ПОДСТРОКА(Т.ПутьКФайлу": [{"Кол": 3}], "ПОДСТРОКА(Т.Комментарий": [{"Кол": 0}],
             "Т.Контрагент <> ЗНАЧЕНИЕ(Справочник.Контрагенты.ПустаяСсылка)": [{"Кол": 3}],
             "Т.Странное <> НЕОПРЕДЕЛЕНО": RuntimeError("нельзя сравнивать"),
             "КАК Кол ИЗ РегистрСведений.пз_ИсторияЗвонков": [{"Кол": 4}],
             "ВЫРАЗИТЬ(П.ДокументОснование": PAID, "З.ПометкаУдаления КАК Удален": EXTRA, "НАЧАЛОПЕРИОДА(З.Дата, ДЕНЬ) КАК День": VISITS,
             "ТелефоныДляБыстрогоПоиска": PHONES, "Справочник.Контрагенты КАК К": CLIENTS,
             "пз_ИсторияЗвонков": CALLS, "З.ДатаСоздания КАК Создан": ORDERS, "ПОМЕСТИТЬ ВТ_П": PAYMENTS,
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
o = {r["number"]: r for r in tables["orders"]}
assert o["ЗН1"]["manager"] == "Менеджер 1" and not o["ЗН1"].get("deleted")
assert o["ЗН2"]["deleted"] == "да" and o["ЗН2"]["refusal"] == "Дорого" and o["ЗН2"]["plan"] == "2026-11-12 10:00:00"
assert len(tables["visits"]) == 3                                     # без пустого клиента
assert tables["phones"] == [{"code": "0001", "phone": "9101112233"}, {"code": "0005", "phone": "9107770000"}]
assert len(tables["clients"]) == 3
cf = {r["name"]: r for r in tables["call_fields"]}
assert (cf["ПутьКФайлу"]["filled"], cf["ПутьКФайлу"]["total"]) == (3, 4), cf
assert cf["Комментарий"]["filled"] == 0 and cf["Контрагент"]["kind"] == "ссылка: Контрагенты"
assert cf["Странное"]["filled"] is None                                # поле не сравнить - без числа, сбор цел
assert o["ЗН1"]["kind"] == "Гарантия"                                   # «Вид» найден по синониму
assert [(b["account"], b["kind"]) for b in tables["balances"]] == [
    ("Касса сервиса", "Касса"), ("Эквайринг", "Касса (карты)"), ("Р/с Альфа", "Расчётный счёт")], tables["balances"]
assert meta["balances_at"] == "2026-11-13 13:00", meta
print("сбор: OK")

# дневное обновление: свежие строки подменяют утренние, старые остаются; упавший раздел не трогается
light_conn = Conn({"З.ПометкаУдаления КАК Удален": [], "Справочник.Контрагенты КАК К": [CLIENTS[2] | {"Наименование": "Новый"}],
                   "пз_ИсторияЗвонков": [call("c9", datetime(2026, 11, 13, 12), 0, 30)],
                   "З.ДатаСоздания КАК Создан": [], "ПОМЕСТИТЬ ВТ_П": RuntimeError("занято"), "Справочник.Напоминания": [],
                   "ПРЕДСТАВЛЕНИЕ(З.ВидЗН) КАК Вид": [],
                   "ДенежныеСредства.Остатки": [{"Счет": "Касса сервиса", "Вид": "Касса", "Организация": "ИП Н",
                                                 "Карты": False, "Валюта": "руб.", "Остаток": 999}]})
lt, lm = C.collect(light_conn, 6, Directions.parse("0", "1"), now=NOW, light=True)
assert "visits" not in lt and "works" not in lt and lm["cutoffs"]["calls"] == "2026-11-10 00:00:00", lm
assert "Справочник.Контрагенты КАК К\nГДЕ" not in C.Q_CLIENTS and "&НачалоКарточек" in C.Q_CLIENTS_NEW
mt, mm = C.merge_light(tables, meta, lt, lm)
assert [c["id"] for c in mt["calls"]] == ["c9"], mt["calls"]            # все утренние звонки - после 10.11
assert mt["payments"] == tables["payments"]                           # оплаты не прочитались - утренние
assert {c["code"]: c["name"] for c in mt["clients"]}["0005"] == "Новый" and len(mt["clients"]) == 3
assert mt["visits"] == tables["visits"] and mm["updated_at"] == "2026-11-13 13:00:00"
assert set(mm["errors"]) == {"Работы", "Оплаты"}, mm["errors"]
assert [b["amount"] for b in mt["balances"]] == [999], mt["balances"]      # остатки - всегда свежие
print("дневное обновление: OK")

tmp = Path(tempfile.mkdtemp())
write_db(tmp / "v.db", tables, meta)
t2, m2 = read_db(tmp / "v.db")
assert m2["errors"] == meta["errors"] and len(t2["calls"]) == 4

d = M.Data.from_tables(t2, m2, {"21": "Городская"})
f = M.Filters(date(2026, 11, 10), date(2026, 11, 13))
k = M.call_kpis(d, f)
assert (k["incoming"], k["missed"], k["no_callback"], k["outgoing"]) == (3, 2, 1, 1), k
unpaid = M.unpaid_orders(d, f)
assert len(unpaid) == 1 and unpaid[0]["debt"] == 2000, unpaid        # 7000 касса + 1500 р/с - 500 возврат
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

# ------------------------------------------------------------------ клиенты, воронка, база
from avtootchet.vitrina import people as P  # noqa: E402
assert [x["number"] for x in d.deleted] == ["ЗН2"] and d.visits["0001"] == [date(2019, 5, 1), date(2026, 11, 11)]
assert "0003" not in d.visits                                          # внутренний ЗН - не визит
assert d.person(d.calls_by_id["c4"]) == "c:0005"                       # звонок без карточки -> карточка по номеру
pp = {r["client_code"] or r["phone"]: r for r in P.call_people(d, f)}
assert pp["0001"]["status"] == "пропустили, потом связались" and pp["0005"]["status"] == "не перезванивали"
s_ = P.people_summary(d, f)
assert (s_["people"], s_["missed_calls"], s_["people_missed"], s_["reached"], s_["not_tried"]) == (3, 2, 2, 1, 1), s_
fn = {r["client_code"]: r["outcome"] for r in P.funnel(d, f, NOW)["rows"]}
assert fn == {"0001": "выдан", "0002": "запись отменена или отказ", "0005": "не дозвонились"}, fn
bk = P.bookings(d, f, NOW)
assert [(b["number"], b["outcome"]) for b in bk] == [("ЗН2", "отказ: Дорого")], bk
g = P.client_groups(d, f)
assert g["_lists"]["g_returned"] == ["0001"] and g["_lists"]["g_never"] == ["0002", "0005"], g["_lists"]
dup = P.duplicates(d)
assert dup["name"] == [["0002", "0005"]], dup                          # «Клиент Б» и «Клиент  б»
ca = P.call_accounting(d, f)
assert (ca["stats"]["answered"], ca["stats"]["missed"], ca["stats"]["outgoing"]) == (1, 2, 1), ca["stats"]
assert ca["record_field"]["name"] == "ПутьКФайлу" and ca["record_field"]["pct"] == 75, ca["record_field"]
assert ca["note_field"]["name"] == "Комментарий" and ca["fields_total"] == 4
assert {r["role"] for r in ca["fields"]} >= {"запись разговора", "итог / комментарий", "клиент"}
q = {i["key"]: i["count"] for i in P.quality(d, f, NOW)["items"]}
assert q["q_no_phone"] == 1 and q["q_dup_name"] == 1, q
path = M.client_path(d, M.MARKETER, call_id="c4", now=NOW)
assert path["code"] == "0005" and path["card"]["group"] == "ещё не обслуживался"
path = M.client_path(d, M.MARKETER, code="0002", now=NOW)
assert any(e.get("booking") and e["outcome"] == "отказ: Дорого" for e in path["events"]), path
assert [o["number"] for o in M.problem_rows(d, "cancelled", f, NOW)] == ["ЗН2"]   # удалённый ЗН = отмена
pre = M.Data.from_tables({"orders": [{"number": "П1", "date": "2026-10-01 10:00:00", "created": "2026-10-01 10:00:00",
                                      "issued": "", "status": "Предварительный"},
                                     {"number": "П2", "date": "2026-10-01 10:00:00", "created": "2026-10-01 10:00:00",
                                      "issued": "", "status": "В работе"}]}, {})
assert [o["number"] for o in M.stuck_orders(pre, NOW)] == ["П2"]       # запись - не «зависший»
print("клиенты и воронка: OK")

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
for key in list(P.TITLES) + ["f_all", "f_talked", "f_booked", "f_came", "f_issued"] + ["o:" + k for k in P.OUTCOME_ORDER]:
    lst = M.strip_money(P.list_rows(demo, key, f30, M.MARKETER, NOW), M.MARKETER)
    assert "amount" not in json.dumps(lst, ensure_ascii=False), key
M.finance(demo, f30)
M.managers(demo, f30, M.OWNER)
path = M.client_path(demo, M.MARKETER, code=demo.orders[0]["client_code"])
assert all(e["kind"] != "payment" for e in path["events"])
assert M.strip_money({"balances": {"total": 5}, "x": 1}, M.MARKETER) == {"x": 1}
print("роли: OK")

# ------------------------------------------------------------------ сайт
write_db(tmp / "demo.db", dt, dm)
# пользователи: новый список + старый пароль роли (до 0.12) для маркетолога -> логин «маркетолог»
USERS = RN.site_users({"users": [{"login": "Саша", "role": "owner", "lock": make_lock("owner-pass")},
                                 {"login": "Без роли", "role": "admin", "lock": make_lock("x-pass-123")}],
                       "owner_lock": make_lock("old-owner"), "marketer_lock": make_lock("market-pass")})
assert set(USERS) == {"саша", "маркетолог"}, USERS          # старый пароль владельца не нужен - есть «Саша»
assert RN.special_rules({}) == {"warranty": ["гарант"], "personal": ["личн"]}
assert RN.special_rules({"special": {"warranty": "Гарантия; ГАР", "personal": ""}})["warranty"] == ["гарантия", "гар"]
app = App(Store(tmp / "demo.db"), USERS, ROOT / "avtootchet" / "vitrina" / "web", now=lambda: NOW,
          special=RN.special_rules({}))
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
from avtootchet import __version__  # noqa: E402
from avtootchet.vitrina import restart as R  # noqa: E402
assert hdr["X-Vitrina-Version"] == __version__
assert R.site_version(httpd.server_address[1]) == __version__
# перезапуск: останавливаются все сайты (--serve и кто держит порт), но не окна, сборы и сама программа
procs = [R.Proc(10, "ACMineevo-Vitrina.exe", ["C:\\a\\ACMineevo-Vitrina.exe", "--serve"]),
         R.Proc(11, "ACMineevo-Vitrina.exe", ["C:\\a\\ACMineevo-Vitrina.exe"]),                 # окно
         R.Proc(12, "ACMineevo-Vitrina.exe", ["C:\\a\\ACMineevo-Vitrina.exe", "--collect-today"]),
         R.Proc(13, "python.exe", ["python", "x.py"], listens=True),                           # чужой на порту
         R.Proc(14, "ACMineevo-Vitrina.exe", ["C:\\a\\ACMineevo-Vitrina.exe", "--serve"])]   # это мы
assert [p.pid for p in R.choose_targets(procs, {14})] == [10, 13]
assert req("/api/home")[0] == 401
assert req("/api/login", {"login": "Саша", "password": "wrong"})[0] == 401
assert req("/api/login", {"password": "owner-pass"})[0] == 401                  # без логина нельзя
assert req("/api/login", {"login": "маркетолог", "password": "owner-pass"})[0] == 401   # чужой пароль


def login(name, pw):
    code, body, hdr = req("/api/login", {"login": name, "password": pw})
    assert code == 200, body
    return hdr["Set-Cookie"].split(";")[0]


owner, market = login("  саша ", "owner-pass"), login("Маркетолог", "market-pass")
assert json.loads(req("/api/meta", cookie=owner)[1])["user"] == "Саша"
fin = json.loads(req("/api/finance?from=2026-10-01&to=2026-11-13", cookie=owner)[1])
names = [r["name"] for r in fin["special"]["rows"]]
assert names == ["Гарантийные ремонты", "Ремонт личных автомобилей"], names
assert sum(r["orders"] + r["open"] for r in fin["special"]["rows"]) > 0, fin["special"]["rows"]
assert any(k["special"] == "Гарантийные ремонты" for k in fin["special"]["kinds"])
assert fin["balances"]["total"] == round(48_250 + 12_900 + 31_400 + 612_780.55, 2), fin["balances"]
assert {t["kind"] for t in fin["balances"]["totals"]} == {"Касса", "Касса (карты)", "Расчётный счёт"}
code, body, _ = req("/api/home?from=2026-10-15&to=2026-11-13", cookie=owner)
assert code == 200 and "Выручка" in body.decode()
code, body, _ = req("/api/home?from=2026-10-15&to=2026-11-13", cookie=market)
assert code == 200 and "Выручка" not in body.decode() and '"amount"' not in body.decode()
assert req("/api/finance", cookie=market)[0] == 403
assert req("/api/finance", cookie=owner)[0] == 200
assert req("/api/list?key=unpaid", cookie=market)[0] == 403
assert req("/api/list?key=nope", cookie=owner)[0] == 404
assert req("/api/meta", cookie=owner)[0] == 200
for pth in ("/api/funnel", "/api/clients", "/api/quality", "/api/calls_acc", "/api/list?key=g_sleeping", "/api/list?key=" + urllib.parse.quote("o:выдан")):
    code, body, _ = req(pth + ("&" if "?" in pth else "?") + "from=2026-10-15&to=2026-11-13", cookie=market)
    assert code == 200 and '"amount"' not in body.decode(), (pth, body[:200])
    assert "_lists" not in body.decode()
req("/api/logout", {}, cookie=owner)
assert req("/api/home", cookie=owner)[0] == 401
# подбор пароля: после 5 неверных - пауза даже для верного
for _ in range(5):
    req("/api/login", {"login": "Саша", "password": "x"})
assert req("/api/login", {"login": "Саша", "password": "owner-pass"})[0] == 429
httpd.shutdown()

# сайт без файла данных: вход работает, meta говорит «данных нет», разделы - 503
empty = App(Store(tmp / "nope.db"), RN.site_users({"owner_lock": make_lock("owner-pass")}),
            ROOT / "avtootchet" / "vitrina" / "web")
httpd = serve(empty, "127.0.0.1", 0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"
ck = login("владелец", "owner-pass")
code, body, _ = req("/api/meta", cookie=ck)
assert code == 200 and json.loads(body)["meta"]["nodata"], body
assert req("/api/home", cookie=ck)[0] == 503
httpd.shutdown()
print("сайт: OK")
print("Витрина: все проверки прошли")
