"""Отчёт по выручке на поддельной 1С: раскладка касс, разбивка Работы/Запчасти,
масло, возвраты, прочие поступления, Excel.

    python tests/revenue_check.py [файл.xlsx]
"""
import sys
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook  # noqa: E402

from avtootchet.owner import revenue as R  # noqa: E402
from avtootchet.owner.revenue_report import build_revenue_report, revenue_filename  # noqa: E402

# --- кассы
assert R.classify_cashbox("Эквайринг СЕРВИС") == (R.SERVICE, R.TERMINAL)
assert R.classify_cashbox("QR (М)") == (R.METAL, R.QR)
assert R.classify_cashbox("Наличные БЧ СЕРВИС") == (R.SERVICE, R.CASH_NC)
assert R.classify_cashbox("Нал БЧ (М)") == (R.METAL, R.CASH_NC)
assert R.classify_cashbox("Наличные СЕРВИС") == (R.SERVICE, R.CASH)
assert R.classify_cashbox("Вывод ДС") == ("", "")
assert R._split(100.0, [1, 2]) == [33.33, 66.67]
assert R._split(50.0, [0, 0]) == [50.0, 0.0]


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


class Arr(list):
    def Add(self, v):
        self.append(v)


class Query:
    def __init__(self, conn):
        self.conn, self.Text, self.params = conn, "", {}

    def SetParameter(self, k, v):
        self.params[k] = v

    def Execute(self):
        if "ПОМЕСТИТЬ ВТ_П" in self.Text:
            assert self.params["ГруппыМасла"] == ["ref-масло"], self.params
            rows = self.conn.payments
        elif "КонвертацияДС" in self.Text:
            rows = self.conn.transfers
        elif "Р.НазначениеПлатежа" in self.Text:
            rows = self.conn.expenses
        elif "НАЧАЛОПЕРИОДА(Р.Дата, ДЕНЬ)" in self.Text:
            rows = self.conn.rko
        elif "ОстаткиИОбороты" in self.Text:
            rows = self.conn.balances
        elif "Г.Наименование В (&Имена)" in self.Text:
            assert list(self.params["Имена"]) == ["Масло"]
            rows = [{"Ссылка": "ref-масло"}]
        else:
            raise AssertionError(self.Text[:60])
        return type("Res", (), {"Choose": lambda _s: Sel(rows)})()


def pay(sign, doc, dt, num, box, div, op, amount, basis="ЗН", u=0, t=0, m=0, oil=0, other=0, client="Иванов"):
    return {"Знак": sign, "Документ": doc, "Дата": dt, "Номер": num, "Касса": box,
            "Организация": "ИП", "Подразделение": div, "ВидОперации": op, "Сумма": amount,
            "Клиент": client, "ВидОснования": basis, "НомерОснования": "ЗН-1" if basis == "ЗН" else "РН-1",
            "Автомобиль": "Lada", "ГосНомер": "А001АА", "Мастер": "Петров", "ТипЗН": "СЕРВИС",
            "Услуги": u, "Товары": t, "Материалы": m, "Масло": oil, "ПрочееРН": other, "УслугиРН": 0}


D1, D2 = datetime(2026, 8, 4, 10, 0), datetime(2026, 8, 5, 11, 30)


class Conn:
    payments = [
        pay(1, "ПКО", D1, "001", "Эквайринг СЕРВИС", "Сервис", "Приход денег", 26710, u=8680, t=18030),
        pay(1, "ПКО", D1, "002", "Эквайринг СЕРВИС", "Сервис", "Приход денег", 8010, u=4170, t=3000, m=840),
        pay(1, "ПКО", D1, "003", "QR СЕРВИС", "Сервис", "Приход денег", 1000, u=1000, t=1000),  # частично
        pay(1, "ПКО", D1, "004", "Наличные СЕРВИС", "Металлообработка", "Приход денег", 3000, u=3000),
        pay(1, "ПКО", D1, "005", "Наличные БЧ СЕРВИС", "Сервис", "Приход денег", 2500, basis="РН",
            oil=1500, other=1000),
        pay(1, "ПКО", D2, "006", "Нал БЧ (М)", "Сервис", "Приход денег", 700, u=700),
        pay(1, "ПКО", D2, "007", "Наличные БЧ СЕРВИС", "", "Приход в доход", 999, basis=""),
        pay(-1, "РКО", D2, "008", "Эквайринг СЕРВИС", "Сервис", "Расход денег", 400, u=300, t=100),
    ]
    transfers = [{"Дата": D2, "Номер": "К-1", "Откуда": "Эквайринг СЕРВИС", "Куда": "Наличные СЕРВИС",
                  "Списано": 34720.0, "Зачислено": 34720.0, "Комментарий": ""}]
    rko = [{"День": D2, "Касса": "Эквайринг СЕРВИС", "Сумма": 400.0}]

    def exp(num, acc, src, cp, art, mov, div, basis, amount, op="Расход денег"):
        return {"Дата": D2, "Номер": num, "Счет": acc, "Источник": src, "ВидОперации": op,
                "Контрагент": cp, "Статья": art, "ВидДвижения": mov, "Подразделение": div,
                "Организация": "ИП", "Основание": basis, "Сумма": amount, "Комментарий": "",
                "Назначение": ""}
    expenses = [
        exp("Р1", "АО \"АЛЬФА БАНК\"", "Банк", "БЕРГ ХОЛДИНГ ООО", "", "Оплата поставщикам", "Сервис",
            "Приходная накладная", 50000.0),
        exp("Р2", "АО \"АЛЬФА БАНК\"", "Банк", "ООО \"Вюрт-Русь\"", "", "Оплата поставщикам", "Сервис",
            "Приходная накладная", 12000.0),
        exp("Р3", "Наличные БЧ СЕРВИС", "Касса", "Клининг ИП", "Уборка помещений", "", "Сервис", "",
            3000.0, op="Списание на затраты"),
        exp("Р4", "Наличные БЧ СЕРВИС", "Касса", "Клининг ИП", "Уборка помещений", "", "Сервис", "",
            2000.0, op="Списание на затраты"),
        exp("Р5", "Эквайринг СЕРВИС", "Касса", "Иванов", "", "", "Сервис", "Заказ-наряд", 400.0),
        exp("Р6", "Нал БЧ (М)", "Касса", "", "", "", "Металлообработка", "", 150.0, op="Расход денег"),
    ]

    def bal(day, box, inc, out):
        return {"День": day, "Касса": box, "Нач": 0.0, "Приход": inc, "Расход": out, "Кон": inc - out}
    balances = [
        bal(D1, "Эквайринг СЕРВИС", 34720.0, 0.0),
        bal(D1, "QR СЕРВИС", 1000.0, 0.0),
        bal(D1, "Наличные СЕРВИС", 3000.0, 0.0),
        bal(D1, "Наличные БЧ СЕРВИС", 2600.0, 0.0),      # на 100 больше, чем ПКО: должно всплыть
        bal(D2, "Нал БЧ (М)", 700.0, 0.0),
        bal(D2, "Наличные БЧ СЕРВИС", 999.0, 0.0),
        bal(D2, "Эквайринг СЕРВИС", 0.0, 400.0 + 34720.0),
        bal(D2, "Наличные СЕРВИС", 34720.0, 0.0),
    ]

    def NewObject(self, name):
        return Arr() if name == "Массив" else Query(self)


data = R.load_revenue(Conn(), date(2026, 8, 1), date(2026, 8, 31), ["Масло"], ["Берг", "Вюрт", "Амтек"])
g = {e.number: e.group for e in data.expenses}
assert g == {"Р1": "Оплата поставщикам", "Р2": "Оплата поставщикам", "Р3": "Уборка помещений",
             "Р4": "Уборка помещений", "Р5": R.REFUND_GROUP, "Р6": "Без статьи (Расход денег)"}, g
assert R.supplier_matches("ООО \"Вюрт-Русь\"", "Вюрт") and R.supplier_matches("БЕРГ ХОЛДИНГ ООО", "берг")
assert not R.supplier_matches("Гринлайт", "Берг")
by = {p.number: p for p in data.payments}
assert (by["001"].works, by["001"].parts) == (8680.0, 18030.0)
assert (by["002"].works, by["002"].parts) == (4170.0, 3840.0), "материалы входят в запчасти"
assert (by["003"].works, by["003"].parts) == (500.0, 500.0), "частичная оплата - пропорционально"
assert (by["004"].direction, by["004"].method) == (R.METAL, R.CASH), "общая наличная касса - по подразделению"
assert (by["005"].parts_sale, by["005"].oil_sale) == (1000.0, 1500.0)
assert by["006"].direction == R.METAL and by["006"].notes, "касса (М) с подразделением Сервис - замечание"
assert not by["007"].is_revenue, "приход без основания - не выручка"
assert by["008"].amount == -400.0 and (by["008"].works, by["008"].parts) == (-300.0, -100.0), "возврат минусом"
assert len(data.revenue) == 7 and len(data.other) == 1

recon = {(r.day, r.cashbox): r for r in data.reconciliation}
bad = [r for r in recon.values() if not r.ok]
assert len(bad) == 1, [(r.day, r.cashbox, r.other_in, r.other_out) for r in bad]
b = bad[0]
assert (b.cashbox, b.other_in) == ("Наличные БЧ СЕРВИС", 100.0), (b.cashbox, b.other_in)
e2 = recon[(D2.date(), "Эквайринг СЕРВИС")]
assert (e2.refunds, e2.rko_other, e2.transfers_out, e2.other_out) == (400.0, 0.0, 34720.0, 0.0)
assert recon[(D2.date(), "Наличные СЕРВИС")].transfers_in == 34720.0
assert recon[(D2.date(), "Наличные БЧ СЕРВИС")].pko_other == 999.0

xlsx = build_revenue_report(data)
wb = load_workbook(BytesIO(xlsx))
assert wb.sheetnames[:9] == ["Выручка по дням", "Итоги", "Сверка", "Оплаты подробно", "Расходы по статьям",
                            "Расходы по поставщикам", "Расходы подробно", "Перемещения ДС",
                            "Остатки касс"], wb.sheetnames
assert "Не сходится: 1 из" in wb["Сверка"]["A3"].value
for name in ("Расходы по статьям", "Расходы по поставщикам", "Расходы подробно"):
    assert name in wb.sheetnames, name
art = {r[0]: r for r in wb["Расходы по статьям"].iter_rows(min_row=5, values_only=True) if r[0]}
assert art["Оплата поставщикам"][4] == 62000 and art["Оплата поставщикам"][8] == 62000
assert art["Уборка помещений"][2] == 2 and art["Уборка помещений"][3] == 5000
assert art["Итого"][8] == 67550, art["Итого"]
sup = [r for r in wb["Расходы по поставщикам"].iter_rows(values_only=True) if r[0] in ("Берг", "Вюрт", "Амтек")]
assert [(r[0], r[5]) for r in sup] == [("Берг", 50000), ("Вюрт", 12000), ("Амтек", 0)], sup
assert sup[2][1] == "не найден за период"
assert "Прочие поступления" in wb.sheetnames and "Замечания" in wb.sheetnames
ws = wb["Выручка по дням"]
cells = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
assert "04.08.2026 Сервис" in cells and "04.08.2026 Металлообработка" in cells
assert "ТЕРМИНАЛ" in cells and "QR КОД" in cells
assert 26710 in cells and 8680 in cells and 18030 in cells
it = wb["Итоги"]
total = [r for r in it.iter_rows(values_only=True) if r[0] == "Итого"][0]
assert round(total[-1], 2) == round(26710 + 8010 + 1000 + 3000 + 2500 + 700 - 400, 2), total
assert revenue_filename(data) == "Выручка 01.08.2026-31.08.2026.xlsx"
for f in wb.worksheets:
    for row in f.iter_rows(values_only=True):
        for v in row:
            assert not (isinstance(v, str) and v.startswith("=")), "в отчёте не должно быть формул"
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_bytes(xlsx)
print("OK: отчёт по выручке")
