"""«Проверить структуру склада» на поддельной 1С: метаданные, количества, независимость проверок.

    python tests/stock_probe_check.py
"""
import runpy
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.owner import stock_probe as S  # noqa: E402
from avtootchet.owner.sources import ComSource  # noqa: E402

fake = runpy.run_path(str(ROOT / "tests" / "probe_check.py"), run_name="fake")  # Sel/Query/Conn


class Coll:
    """Коллекция метаданных: Count() и Get(i), как в COM 1С."""
    def __init__(self, items):
        self.items = items

    def Count(self):
        return len(self.items)

    def Get(self, i):
        return self.items[i]

    def __call__(self, *a):          # COM-объекты вызываемы: нельзя путать свойство с методом
        raise AssertionError("коллекцию нельзя вызывать")


class Attr:
    def __init__(self, name, typ="СправочникСсылка.Пользователи"):
        self.Name, self.Synonym, self.Type = name, "", typ


class Doc:
    def __init__(self, name, syn, attrs, ts=None):
        self.Name, self.Synonym = name, syn
        self.Attributes = Coll([Attr(a) for a in attrs])
        self.TabularSections = Coll([type("TS", (), {"Name": n, "Synonym": "",
                                                     "Attributes": Coll([Attr(a, "Число") for a in cols])})()
                                     for n, cols in (ts or {}).items()])


class Reg:
    def __init__(self, name, dims, res, kind="Остатки"):
        self.Name, self.Synonym, self.RegisterType = name, "", kind
        self.Dimensions = Coll([Attr(d) for d in dims])
        self.Resources = Coll([Attr(r, "Число") for r in res])
        self.Attributes = Coll([])


class Conn(fake["Conn"]):
    Metadata = type("MD", (), {
        "Documents": Coll([
            Doc("ПриходнаяНакладная", "Приходная накладная", ["Ответственный", "Склад", "Контрагент", "Комментарий"],
                {"Товары": ["Номенклатура", "Количество", "Цена"]}),
            Doc("РасходнаяНакладная", "Расходная накладная", ["Ответственный", "Склад"]),
            Doc("ПриходДенег", "Приход денег", ["Касса"]),         # денежный - пропускается
            Doc("Сотрудник", "Карточка", ["Имя"]),                  # не про товар - пропускается
        ]),
        "AccumulationRegisters": Coll([
            Reg("ОстаткиТоваров", ["Склад", "Номенклатура"], ["Количество"]),
            Reg("ДенежныеСредства", ["СчетДенежныхСредств"], ["Сумма"]),
        ]),
    })()


DATA = {
    "РегистрНакопления.ОстаткиТоваров": [
        {"Тип": "Приходная накладная", "Движение": "приход", "Кол": 40},
        {"Тип": "Заказ-наряд", "Движение": "расход", "Кол": 25}],
    "КОЛИЧЕСТВО(*) КАК Кол\nИЗ\n\tДокумент.ПриходнаяНакладная": [{"Кол": 12}],
    "КОЛИЧЕСТВО(*) КАК Кол\nИЗ\n\tДокумент.РасходнаяНакладная": RuntimeError("нет доступа"),
    "РАЗЛИЧНЫЕ Д.Ответственный": [{"Разных": 3}],
    "РАЗЛИЧНЫЕ Д.Склад": [{"Разных": 1}],
    "РАЗЛИЧНЫЕ Д.Контрагент": [{"Разных": 5}],
}

src = ComSource(r"C:\1C\test", "", "BILL", "x")
src._connect = lambda: Conn(DATA)
text = S.StockProbe(src, date(2026, 10, 1), date(2026, 10, 31)).run()
print(text)
assert "[ПриходнаяНакладная (Приходная накладная)]" in text
assert "шапка: Ответственный: <СправочникСсылка.Пользователи>" in text
assert "ТЧ Товары: Номенклатура: <Число>, Количество" in text
assert "ПриходДенег" not in text and "Сотрудник" not in text, "денежные и прочие документы не нужны"
assert "[ОстаткиТоваров] вид: <Остатки>" in text and "[ДенежныеСредства]" in text
assert "<Приходная накладная> | приход: 40" in text and "<Заказ-наряд> | расход: 25" in text
assert "[ПриходнаяНакладная] проведено за период: 12" in text
assert "Ответственный: разных значений 3" in text and "Склад: разных значений 1" in text
assert "Комментарий: разных" not in text, "лишние реквизиты не считаем"
assert "[РасходнаяНакладная] ОШИБКА: нет доступа" in text, "ошибка одного документа не роняет остальные"
assert "== 4." in text
print("OK: проверка структуры склада")
