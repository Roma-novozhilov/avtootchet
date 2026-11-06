"""Отчёт «Залежавшиеся запчасти» на поддельной 1С: раскладка остатка по приходам,
под заказ-наряды, выданные в ЗН, исключённые группы, Excel и смена утренних файлов.

    python tests/stock_check.py
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp()

from openpyxl import load_workbook  # noqa: E402

from avtootchet.owner import stock as S  # noqa: E402
from avtootchet.owner import stock_job as J  # noqa: E402
from avtootchet.owner.stock_report import build_stock_report, stock_filename  # noqa: E402

TODAY = date(2026, 11, 9)


def ago(n):
    return datetime.combine(TODAY - timedelta(days=n), datetime.min.time()).replace(hour=12)


# --- раскладка: продаются старые партии, лежит остаток последних приходов
item = S.StockItem("k", "Колодки", "A1", "Тормоза", 5)
recs = [S.Receipt("", ago(3), "П-3", "Берг", "Склад(АВ)", "", 3, 3000),
        S.Receipt("", ago(20), "П-20", "Вюрт", "Коровин", "", 4, 4400),
        S.Receipt("", ago(40), "П-40", "Берг", "Склад(АВ)", "", 10, 9000)]
rows = S.allocate(item, recs, TODAY, 14)
assert len(rows) == 1 and rows[0].qty == 2 and rows[0].days == 20 and rows[0].number == "П-20", rows
assert rows[0].price == 1100 and rows[0].amount == 2200
assert S.allocate(S.StockItem("k", "x", "", "", 2), recs[:1], TODAY, 14) == [], "свежий приход не залежался"
lost = S.allocate(S.StockItem("k", "Старьё", "", "", 7), recs[1:2], TODAY, 14)
assert [(r.qty, r.days) for r in lost] == [(4, 20), (3, None)] and lost[1].number == S.NO_RECEIPT
assert lost[1].price == 1100, "без прихода - цена последнего прихода"
assert S.sort_rows(lost)[0].days is None, "без даты - самые старые, сверху"
assert S.group_words("Масло, Фильтр;  ") == ["Масло", "Фильтр"]


# --- поддельная 1С
class Arr(list):
    def Add(self, v):
        self.append(v)


class Sel:
    def __init__(self, rows):
        self.rows, self.i = rows, -1

    def Next(self):
        self.i += 1
        if self.i < len(self.rows):
            for k, v in self.rows[self.i].items():
                setattr(self, k, v)
            return True
        return False


class Query:
    def __init__(self, conn):
        self.conn, self.Text, self.params = conn, "", {}

    def SetParameter(self, k, v):
        self.params[k] = v

    def Execute(self):
        self.conn.queries.append((self.Text, dict(self.params)))
        for key, rows in self.conn.data.items():
            if key in self.Text:
                if callable(rows):
                    rows = rows(self.Text, self.params)
                if isinstance(rows, Exception):
                    raise rows
                return type("R", (), {"Choose": lambda _s, r=rows: Sel(r)})()
        return type("R", (), {"Choose": lambda _s: Sel([])})()


class Coll:
    def __init__(self, items):
        self.items = items

    def Count(self):
        return len(self.items)

    def Get(self, i):
        return self.items[i]

    def Find(self, name):
        return next(x for x in self.items if x.Name == name)


def _md(name, attrs):
    return type("M", (), {"Name": name, "Attributes": Coll([type("A", (), {"Name": a})() for a in attrs])})()


class Conn:
    Metadata = type("MD", (), {"Catalogs": Coll([_md("Номенклатура", ["Родитель", "НомерПоКаталогу", "Вес"])])})()

    def __init__(self, data):
        self.data, self.queries = data, []

    def NewObject(self, name):
        return Arr() if name == "Массив" else Query(self)

    def XMLString(self, ref):
        return f"uuid-{ref}"


def stock_rows(text, params):
    art = "О.Номенклатура.НомерПоКаталогу" in text          # реквизит найден по метаданным
    assert ("В ИЕРАРХИИ (&Исключить)" in text) == bool(params["Исключить"])
    assert params["Исключить"] in ([], ["grp-oil"])
    return [{"Ном": "n1", "Имя": "Колодки", "Артикул": "GDB1550" if art else "", "Группа": "Тормоза", "Кол": 5},
            {"Ном": "n2", "Имя": "Фара", "Артикул": "", "Группа": "Оптика", "Кол": 1},
            {"Ном": "n3", "Имя": "Свеча", "Артикул": "", "Группа": "Зажигание", "Кол": 2},
            {"Ном": "n4", "Имя": "Хомут", "Артикул": "", "Группа": "Крепёж", "Кол": 3}]


DATA = {
    "Г.ЭтоГруппа": lambda t, p: ([{"Ссылка": "grp-oil", "Имя": "Масла моторные"}]
                                 if p.get("С0") == "%Масло%" else []),
    "Остатки(, Склад ССЫЛКА Справочник.Склады": stock_rows,
    "Документ.ПриходнаяНакладная.Товары": [
        {"Ном": "n1", "Дата": ago(3), "Номер": "П-3", "Поставщик": "Берг", "Автор": "Склад(АВ)",
         "Менеджер": "", "Вид": "Покупка", "Кол": 3, "Всего": 3000, "ПодЗаказ": ""},
        {"Ном": "n1", "Дата": ago(20), "Номер": "П-20", "Поставщик": "Вюрт", "Автор": "Коровин",
         "Менеджер": "", "Вид": "Покупка", "Кол": 4, "Всего": 4400, "ПодЗаказ": ""},
        {"Ном": "n2", "Дата": ago(30), "Номер": "П-30", "Поставщик": "Тимасан", "Автор": "Семин",
         "Менеджер": "", "Вид": "Покупка", "Кол": 1, "Всего": 15000, "ПодЗаказ": "Заказ наряд 0042"},
        {"Ном": "n3", "Дата": ago(5), "Номер": "П-5", "Поставщик": "Берг", "Автор": "Семин",
         "Менеджер": "", "Вид": "Покупка", "Кол": 2, "Всего": 800, "ПодЗаказ": ""},
        {"Ном": "n4", "Дата": ago(100), "Номер": "П-100", "Поставщик": "Амтек", "Автор": "Склад(АВ)",
         "Менеджер": "", "Вид": "Покупка", "Кол": 0.5, "Всего": 50, "ПодЗаказ": ""},
    ],
    "РегистрНакопления.ОстаткиТоваров КАК Р": [
        {"Ном": "n4", "Дата": ago(300), "Док": "Инвентаризация 0001", "Кол": 2}],
    "Склад ССЫЛКА Документ.ЗаказНаряд": [
        {"ЗН": "Заказ наряд 0040", "ДатаЗН": ago(25), "Имя": "Ремень", "Артикул": "", "Кол": 1},
        {"ЗН": "Заказ наряд 0050", "ДатаЗН": ago(2), "Имя": "Фильтр салона", "Артикул": "", "Кол": 1}],
}

conn = Conn(DATA)
data = S.load_stock(conn, TODAY, 14, ["Масло", "Фильтр"], months=0)
assert data.excluded_groups == ["Масла моторные"] and data.items_in_stock == 4
assert data.article_field == "НомерПоКаталогу"
forced = S.load_stock(Conn(DATA), TODAY, 14, [], months=0, article="НетТакого")
assert forced.article_field == "НетТакого (не читается)" or forced.article_field == "НетТакого"
got = [(r.name, r.article, r.qty, r.days, r.number, r.price) for r in data.stale]
assert got == [("Хомут", "", 0.5, None, S.NO_RECEIPT, 100), ("Хомут", "", 2, 300, "Инвентаризация 0001", 100),
               ("Хомут", "", 0.5, 100, "П-100", 100), ("Колодки", "GDB1550", 2, 20, "П-20", 1100)], got
assert [(r.name, r.order, r.days) for r in data.ordered] == [("Фара", "Заказ наряд 0042", 30)]
assert [(r.order, r.days) for r in data.issued] == [("Заказ наряд 0040", 25)], "свежие выдачи в ЗН не нужны"
rec_q = [p for t, p in conn.queries if "ПриходнаяНакладная" in t][0]
assert rec_q["Номенклатура"] == ["n1", "n2", "n3", "n4"]
rec_text = [t for t, _ in conn.queries if "ПриходнаяНакладная" in t][0]
assert "&С" not in rec_text and "ДАТАВРЕМЯ(2000, 1, 1" in rec_text, "дата - литералом, без сдвига"
assert data.total == 2200 + 50 + 200 + 50

# --- только приходы за 6 месяцев: инвентаризация 300 дней назад и «без прихода» не показываются
d6 = S.load_stock(Conn(DATA), TODAY, 14, ["Масло"], months=6)
assert [(r.name, r.days) for r in d6.stale] == [("Хомут", 100), ("Колодки", 20)], d6.stale
assert d6.older_count == 2 and d6.older_sum == 250
assert S.months_ago(date(2026, 8, 31), 6) == date(2026, 2, 28)
sub6 = load_workbook(BytesIO(build_stock_report(d6)))["Залежавшиеся"]["A2"].value
assert "Приходы за последние 6 мес." in sub6 and "2 поз. на 250.00 ₽" in sub6, sub6

# --- Excel
wb = load_workbook(BytesIO(build_stock_report(data)))
assert wb.sheetnames == ["Залежавшиеся", "Под заказ-наряды", "Выданы в ЗН", "По менеджерам", "По поставщикам"]
vals = [[c for c in r] for r in wb["Залежавшиеся"].iter_rows(values_only=True)]
assert "Не учитываются группы: Масла моторные" in vals[1][0]
line = [r for r in vals if r and r[0] == "Колодки"][0]
assert line[1] == "GDB1550" and line[3] == 2 and line[5] == 20 and line[6] == "П-20" and line[8] == "Коровин" and line[11] == 2200
assert [r for r in vals if r and r[0] == "Итого"][0][11] == 2500
assert any(r and r[0] == "Хомут" and r[5] == "нет данных" for r in vals)
assert any(r and r[0] == "Фара" and r[-1] == "Заказ наряд 0042"
           for r in wb["Под заказ-наряды"].iter_rows(values_only=True))
assert any(r and r[0] == "Коровин" for r in wb["По менеджерам"].iter_rows(values_only=True))
assert stock_filename(data) == "Залежавшиеся запчасти 09.11.2026.xlsx"
empty = load_workbook(BytesIO(build_stock_report(S.StockData(TODAY, 14))))
assert empty["Залежавшиеся"]["A4"].value == "Таких позиций нет."

# --- папка утреннего отчёта: остаётся только свежий
folder = Path(tempfile.mkdtemp())
(folder / "Залежавшиеся запчасти 08.11.2026.xlsx").write_bytes(b"old")
(folder / J.ERROR_FILE).write_text("x")
(folder / "чужой файл.xlsx").write_bytes(b"keep")
t = J.save_rotating(folder, "Залежавшиеся запчасти 09.11.2026.xlsx", b"new")
assert sorted(p.name for p in folder.iterdir()) == ["Залежавшиеся запчасти 09.11.2026.xlsx", "чужой файл.xlsx"]
assert t.read_bytes() == b"new"

# --- ошибка: старый отчёт не трогаем, рядом файл с причиной
from avtootchet.common.settings import Settings  # noqa: E402
from avtootchet.owner.app import DEFAULTS  # noqa: E402

st = Settings("owner", DEFAULTS)
st["stock"] = {"folder": str(folder), "days": 14, "exclude": ""}
st["report_password"] = "secret123"
assert J.run_morning(st, TODAY) == 1, "демо-источник - отчёт не строится"
assert (folder / J.ERROR_FILE).exists() and "COM" in (folder / J.ERROR_FILE).read_text(encoding="utf-8-sig")
assert t.exists(), "вчерашний (здесь - последний удачный) отчёт остаётся"

# --- удачный запуск: зашифрованный файл, файл ошибки убран
st["source"] = "com"
st["com"] = dict(st["com"], server=r"C:\1C\test", user="BILL", password="x")
J.ComSource._connect = lambda self: Conn(DATA)
J.ComSource._check_settings = lambda self: None
assert J.run_morning(st, TODAY) == 0
names = sorted(p.name for p in folder.iterdir())
assert names == ["Залежавшиеся запчасти 09.11.2026.xlsx", "чужой файл.xlsx"], names
from avtootchet.common.xlsx_crypto import decrypt_xlsx, is_encrypted  # noqa: E402
blob = (folder / "Залежавшиеся запчасти 09.11.2026.xlsx").read_bytes()
assert is_encrypted(blob) and load_workbook(BytesIO(decrypt_xlsx(blob, "secret123"))).sheetnames[0] == "Залежавшиеся"
print("OK: залежавшиеся запчасти")
