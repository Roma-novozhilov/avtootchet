"""«Проверить структуру CRM и звонков» на поддельной 1С.

    python tests/crm_probe_check.py
"""
import runpy
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.owner import crm_probe as C  # noqa: E402
from avtootchet.owner.sources import ComSource  # noqa: E402

fake = runpy.run_path(str(ROOT / "tests" / "probe_check.py"), run_name="fake")


class Coll:
    def __init__(self, items):
        self.items = items

    def Count(self):
        return len(self.items)

    def Get(self, i):
        return self.items[i]


class Typ:
    def __init__(self, text, full=()):
        self.text, self.full = text, list(full)

    def Types(self):
        return Coll(self.full)

    def __str__(self):
        return self.text


class Attr:
    def __init__(self, name, text="Строка", full=()):
        self.Name, self.Synonym, self.Type = name, "", Typ(text, full)


class MD:
    def __init__(self, full):
        self.full = full

    def FullName(self):
        return self.full


def obj(name, syn="", attrs=(), dims=(), res=(), ts=None):
    o = type("O", (), {})()
    o.Name, o.Synonym = name, syn
    o.Attributes = Coll(list(attrs))
    o.Dimensions, o.Resources = Coll(list(dims)), Coll(list(res))
    o.TabularSections = Coll([type("TS", (), {"Name": n, "Attributes": Coll(c)})() for n, c in (ts or {}).items()])
    return o


class Conn(fake["Conn"]):
    Metadata = type("MDRoot", (), {
        "Documents": Coll([
            obj("ЗаявкаНаРемонт", "Заявка", [Attr("Статус", "Статусы заявок", ["Перечисление.СтатусыЗаявок"]),
                                             Attr("Ответственный", "Пользователи", ["Справочник.Пользователи"]),
                                             Attr("ДатаОбработки", "Дата")]),
            obj("ПриходнаяНакладная", "Приходная накладная", [Attr("Склад")]),   # не CRM - пропуск
        ]),
        "Tasks": Coll([obj("ЗадачаМенеджера", "Задача", [Attr("Выполнена", "Булево"), Attr("Срок", "Дата")])]),
        "BusinessProcesses": Coll([]),
        "InformationRegisters": Coll([
            obj("пз_ИсторияЗвонков", "История звонков", dims=[Attr("Контрагент", "Контрагенты", ["Справочник.Контрагенты"])],
                res=[Attr("ДатаВремяНачала", "Дата"),
                     Attr("НаправлениеЗвонка", "Направления", ["Перечисление.пз_НаправленияЗвонков"]),
                     Attr("НомерТелефонаС")]),
        ]),
        "Catalogs": Coll([obj("КонтактыЛиды", "Контакт (Лид)", [Attr("Телефон")])]),
        "FindByType": lambda _s, t: MD(t),
    })()


DATA = {
    # сначала распределения (ключи ищутся по порядку, побеждает первый совпавший)
    "Т.Статус КАК Знач": [{"Знач": "Новая", "Кол": 10}, {"Знач": "Обработана", "Кол": 30}],
    "РАЗЛИЧНЫЕ Т.Ответственный": [{"Разных": 3}],
    "Т.ДатаОбработки > ДАТАВРЕМЯ": [{"Кол": 28}],
    "Т.Выполнена КАК Знач": [{"Знач": True, "Кол": 9}, {"Знач": False, "Кол": 3}],
    "Т.НаправлениеЗвонка КАК Знач": [{"Знач": "Входящий", "Кол": 500}, {"Знач": "Пропущенный", "Кол": 80}],
    "РАЗЛИЧНЫЕ Т.Контрагент": [{"Разных": 410}],
    "Т.ДатаВремяНачала > ДАТАВРЕМЯ": [{"Кол": 600}],
    "Т.Срок > ДАТАВРЕМЯ": [{"Кол": 12}],
    "ИЗ Документ.ЗаявкаНаРемонт КАК Т ГДЕ Т.Дата МЕЖДУ": [{"Кол": 40}],
    "ИЗ Документ.ЗаявкаНаРемонт КАК Т": [{"Кол": 300}],
    "ИЗ Задача.ЗадачаМенеджера КАК Т": [{"Кол": 12}],
    "ИЗ РегистрСведений.пз_ИсторияЗвонков КАК Т ГДЕ": [{"Кол": 600}],
    "ИЗ РегистрСведений.пз_ИсторияЗвонков КАК Т": [{"Кол": 23931}],
    "ИЗ Справочник.КонтактыЛиды КАК Т": [{"Кол": 5000}],
}

src = ComSource(r"C:\1C\test", "", "BILL", "x")
src._connect = lambda: Conn(DATA)
text = C.CrmProbe(src, date(2026, 10, 13), date(2026, 11, 11)).run()
print(text)
assert "[Документ.ЗаявкаНаРемонт] ЗаявкаНаРемонт (Заявка) — всего: 300, за период (по «Дата»): 40" in text
assert "[Справочник.КонтактыЛиды] КонтактыЛиды (Контакт (Лид)) — всего: 5000\n" in text, "у справочника нет периода"
assert "ПриходнаяНакладная" not in text, "не CRM"
assert "всего: 23931, за период (по «ДатаВремяНачала»): 600" in text
assert "НаправлениеЗвонка: <Входящий>: 500; <Пропущенный>: 80" in text
assert "Статус: <Новая>: 10; <Обработана>: 30" in text
assert "Выполнена: <True>: 9; <False>: 3" in text
assert "Ответственный → Справочник.Пользователи: разных значений 3" in text, "людей - только число"
assert "Контрагент → Справочник.Контрагенты: разных значений 410" in text
assert "ДатаОбработки: заполнено 28" in text
assert "\n  НомерТелефонаС" not in text, "строки (телефоны) не выводим"
print("OK: проверка структуры CRM")
