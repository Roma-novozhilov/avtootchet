"""«Проверить структуру» на поддельной 1С: форматирование, подсчёт и то, что ошибка
одной проверки не роняет остальные.

    python tests/probe_check.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from avtootchet.owner import probe as P  # noqa: E402
from avtootchet.owner.sources import ComSource  # noqa: E402


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
    def __init__(self, data):
        self.data, self.Text, self.params = data, "", {}

    def SetParameter(self, k, v):
        self.params[k] = v

    def Execute(self):
        for key, rows in self.data.items():
            if key in self.Text:
                if isinstance(rows, Exception):
                    raise rows
                return type("R", (), {"Choose": lambda _s, r=rows: Sel(r)})()
        return type("R", (), {"Choose": lambda _s: Sel([])})()


class Conn:
    def __init__(self, data):
        self.data = data

    def NewObject(self, name):
        assert name == "Запрос"
        return Query(self.data)

    def String(self, v):
        return f"<{v}>"


DATA = {
    "РегистрНакопления.ДенежныеСредства": [
        {"Касса": "Наличные СЕРВИС", "Тип": "Приход денег", "Движение": "приход", "Кол": 12.0}],
    "ТИПЗНАЧЕНИЯ(П.ДокументОснование)": [{"Тип": "Заказ-наряд", "Вид": "Оплата", "Кол": 9}],
    "ТИПЗНАЧЕНИЯ(П.Основание)": RuntimeError("Поле не найдено П.Основание"),
    "ПОМЕСТИТЬ ВТ_ЗН": [
        {"Сумма": 1000.0, "Услуги": 400.0, "Товары": 600.0, "Материалы": 0.0, "Оплачено": 1000.0, "Оплат": 1},
        {"Сумма": 500.0, "Услуги": 500.0, "Товары": 0.0, "Материалы": 0.0, "Оплачено": 200.0, "Оплат": 2},
    ],
    "Справочник.Номенклатура": [{"Родитель": "", "Имя": "Масла"}, {"Родитель": "Масла", "Имя": "Моторные"}],
}

src = ComSource(r"C:\1C\test", "", "BILL", "x")
src._connect = lambda: Conn(DATA)
text = P.Probe(src, date(2026, 10, 1), date(2026, 10, 31)).run()
print(text)
assert "<Наличные СЕРВИС> | <Приход денег> | приход: 12" in text
assert "основание: <Заказ-наряд> | вид операции: <Оплата>: 9" in text
assert "ОШИБКА: Поле не найдено П.Основание" in text, "ошибка одной проверки должна попасть в отчёт"
assert "заказ-нарядов с оплатой в периоде: 2" in text
assert "У+Т = сумма ЗН: 2 из 2" in text
assert "оплачен меньше суммы: 1 из 2" in text and "несколько оплат: 1 из 2" in text
assert "Масла / Моторные" in text
assert "== 10." in text, "все проверки должны выполниться"
for bad in ("1000", "500", "200"):
    assert bad not in text, f"сумма {bad} не должна попасть в отчёт"
print("OK: проверка структуры")
