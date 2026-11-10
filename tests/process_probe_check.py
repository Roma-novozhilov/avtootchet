"""«Проверить данные для анализа процессов» на поддельной 1С и поддельном журнале регистрации.

    python tests/process_probe_check.py
"""
import runpy
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.owner import process_probe as PP  # noqa: E402
from avtootchet.owner.sources import ComSource  # noqa: E402

fake = runpy.run_path(str(ROOT / "tests" / "probe_check.py"), run_name="fake")  # Sel/Query/Conn


class Coll:
    def __init__(self, items):
        self.items = items

    def Count(self):
        return len(self.items)

    def Get(self, i):
        return self.items[i]

    def __call__(self, *a):
        raise AssertionError("коллекцию нельзя вызывать")


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


class Doc:
    def __init__(self, name, attrs, ts=None):
        self.Name, self.Synonym = name, ""
        self.Attributes = Coll(attrs)
        self.TabularSections = Coll([type("TS", (), {"Name": n, "Attributes": Coll(cols)})()
                                     for n, cols in (ts or {}).items()])


class Row:
    def __init__(self, ev, md, user):
        self.Событие, self.Метаданные, self.ИмяПользователя = ev, md, user


class Table:
    rows = [Row("_$Data$_.Update", "Документ.ЗаказНаряд", "Иванов"),
            Row("_$Data$_.Update", "Документ.ЗаказНаряд", "Петров"),
            Row("_$Data$_.Post", "Документ.ПриходнаяНакладная", "Иванов")]

    def Count(self):
        return len(self.rows)

    def Get(self, i):
        return self.rows[i]


class Struct(dict):
    def Insert(self, k, v):
        self[k] = v


class Conn(fake["Conn"]):
    Metadata = type("MDRoot", (), {
        "Documents": Coll([
            Doc("ЗаказНаряд", [Attr("Автор"), Attr("ДатаЗавершен", "Дата"),
                               Attr("Состояние", "Состояния ЗН", ["Перечисление.СостоянияЗН"]),
                               Attr("ДокументОснование", "Заявка", ["Документ.Заявка"])],
                {"Товары": [Attr("Номенклатура")]}),
            Doc("ПриходнаяНакладная", [Attr("Автор")],
                {"Товары": [Attr("ЗаказПокупателя", "Заказ-наряд", ["Документ.ЗаказНаряд"])]}),
            Doc("Пустой", [Attr("Автор")]),
        ]),
        "InformationRegisters": Coll([
            type("R", (), {"Name": "ИсторияСостоянийЗН", "Synonym": "", "Dimensions": Coll([Attr("ЗН")]),
                           "Resources": Coll([Attr("Состояние")]), "Attributes": Coll([])})(),
            type("R", (), {"Name": "КурсыВалют", "Synonym": "", "Dimensions": Coll([]),
                           "Resources": Coll([]), "Attributes": Coll([])})(),
        ]),
        "FindByType": lambda _s, t: MD(t),
    })()

    def NewObject(self, name):
        if name == "ТаблицаЗначений":
            return Table()
        if name == "Структура":
            return Struct()
        return super().NewObject(name)

    def ПолучитьИспользованиеЖурналаРегистрации(self):
        return Coll(["Ошибки", "Предупреждения", "Информация", "Примечания"])

    def ВыгрузитьЖурналРегистрации(self, table, flt):
        raise AssertionError("выгрузка журнала через 1С съедает память сервера - не вызывать")


DATA = {
    "И Д.ДатаЗавершен > ДАТАВРЕМЯ(1, 1, 2)": [{"Кол": 90}],
    "Д.Состояние КАК Знач": [{"Знач": "Закрыт", "Кол": 80}, {"Знач": "В работе", "Кол": 20}],
    "РАЗЛИЧНЫЕ Д.Автор) КАК Разных\nИЗ\n\tДокумент.ЗаказНаряд": [{"Разных": 4}],
    "РАЗЛИЧНЫЕ Д.Автор) КАК Разных\nИЗ\n\tДокумент.ПриходнаяНакладная": [{"Разных": 2}],
    "КАК Авторов\nИЗ\n\tДокумент.ЗаказНаряд": [{"Всего": 100, "Проведено": 95, "Авторов": None}],
    "КАК Авторов\nИЗ\n\tДокумент.ПриходнаяНакладная": [{"Всего": 30, "Проведено": 30, "Авторов": None}],
    "КАК Авторов\nИЗ\n\tДокумент.Пустой": [{"Всего": 0, "Проведено": 0, "Авторов": None}],
    "ТИПЗНАЧЕНИЯ(Д.ДокументОснование)": [{"Тип": "Заявка", "Док": 60}, {"Тип": None, "Док": 40}],
    "ТИПЗНАЧЕНИЯ(Т.ЗаказПокупателя)": [{"Тип": "Заказ-наряд", "Док": 12}],
    "РегистрСведений.ИсторияСостоянийЗН": [{"Кол": 500}],
}

with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / "1Cv8Log"
    log.mkdir()
    con = sqlite3.connect(log / "1Cv8.lgd")
    con.execute("CREATE TABLE EventLog (date INTEGER)")
    ticks = [int((d - datetime(1, 1, 1)).total_seconds() * 10000)
             for d in (datetime(2025, 1, 10), datetime(2026, 11, 9))]
    con.executemany("INSERT INTO EventLog VALUES (?)", [(t,) for t in ticks])
    con.commit()
    con.close()

    class Src(ComSource):
        is_file_base = True          # временная папка в Linux не похожа на путь Windows

    src = Src(tmp, "", "BILL", "x")
    src._connect = lambda: Conn(DATA)
    text = PP.ProcessProbe(src, date(2026, 5, 11), date(2026, 11, 10)).run()

print(text)
assert "файлов: 1; размер: 0.0 МБ" in text
assert "формат: новый (1Cv8.lgd, SQLite)" in text
assert "период: 10.01.2025 – 09.11.2026" in text and "записей: 2" in text
assert "уровни, которые пишутся: <Ошибки>, <Предупреждения>, <Информация>, <Примечания>" in text
assert "[ЗаказНаряд] всего: 100, проведено: 95, разных «Автор»: 4" in text
assert "Пустой" not in text, "документы без записей не показываем"
assert "ДатаЗавершен: <Дата> - заполнено: 90" in text
assert "Состояние: <Состояния ЗН> - значения: <Закрыт>: 80; <В работе>: 20" in text
assert "ТЧ Товары: Номенклатура" in text
assert "ЗаказНаряд.ДокументОснование → <Заявка>: 60" in text, "пустые ссылки (None) не показываем"
assert "ПриходнаяНакладная.Товары.ЗаказПокупателя → <Заказ-наряд>: 12" in text
assert "[ИсторияСостоянийЗН] измерения: ЗН; ресурсы: Состояние; реквизиты: ; записей: 500" in text
assert "КурсыВалют" not in text
assert "== 6." in text
print("OK: проверка данных для анализа процессов")
