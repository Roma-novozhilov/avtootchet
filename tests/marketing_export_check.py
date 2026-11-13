"""Выгрузка для витрины маркетолога: пропущенные, перезвоны, просрочки, CSV/JSON, без телефонов и сумм.

    python tests/marketing_export_check.py
"""
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.marketing import export as E  # noqa: E402

EMPTY = datetime(1, 1, 1)


def call(uid, start, code, dur, frm="", to="", user="Менеджер 1", client="Клиент А", ccode="0001"):
    return {"УИД": uid, "Начало": start, "Окончание": start, "Направление": code, "Длительность": dur,
            "Менеджер": user, "КлиентКод": ccode, "Клиент": client, "Линия": "Сервис", "С": frm, "На": to}


CALLS = [
    call("c1", datetime(2026, 11, 10, 10, 0), 1, 0, frm="+7 (910) 111-22-33"),                 # пропущен
    call("c2", datetime(2026, 11, 10, 10, 30), 2, 45, frm="200", to="89101112233"),            # перезвон
    call("c3", datetime(2026, 11, 11, 18, 0), 1, 0, frm="+79105556677", client="Клиент Б", ccode="0002"),
    call("c4", datetime(2026, 11, 11, 12, 0), 1, 120, frm="+79109998877", user=""),            # принят
    call("c5", datetime(2026, 11, 13, 9, 0), 1, 30, frm="+79105556677"),   # после периода: только для перезвона
]
TASKS = [
    {"Создана": datetime(2026, 11, 10, 9), "Тип": "Задание", "Тема": "",
     "Исполнитель": "Менеджер 1", "Контролер": "Саша", "Срок": datetime(2026, 11, 10, 18), "Выполнено": True,
     "ДатаВыполнения": datetime(2026, 11, 10, 20)},
    {"Создана": datetime(2026, 11, 11, 9), "Тип": "Задание", "Тема": "",
     "Исполнитель": "Менеджер 1", "Контролер": "Саша", "Срок": datetime(2026, 11, 11, 12), "Выполнено": False,
     "ДатаВыполнения": EMPTY},
]
REQS = [{"Номер": "26СЧ00001", "Создан": datetime(2026, 11, 10, 8), "Автор": "Менеджер 1", "Тип": "СЕРВИС",
         "Статус": "Отгружен", "Источник": "Яндекс", "КлиентКод": "0001", "Клиент": "Клиент А",
         "Мастер": "Семин В.", "ВРаботе": datetime(2026, 11, 10, 10), "КтоВРаботу": "Семин В.",
         "Завершен": datetime(2026, 11, 10, 15), "Выдан": datetime(2026, 11, 10, 16), "Повтор": False}]


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


class Conn:
    texts = []

    def NewObject(self, name):
        conn = self
        q = type("Q", (), {})()
        q.Text = ""

        def execute():
            conn.texts.append(q.Text)
            assert "&" not in q.Text, "даты подставлены литералом"
            assert "Наименование" not in q.Text, "у «Напоминаний» нет наименования"
            assert "Сумм" not in q.Text and "Цена" not in q.Text, "финансов в выгрузке нет"
            rows = CALLS if "пз_ИсторияЗвонков" in q.Text else TASKS if "Напоминания" in q.Text else REQS
            return type("R", (), {"Choose": lambda _s: Sel(rows)})()
        q.Execute = execute
        return q


dirs = E.Directions.parse("1", "2")
ex = E.load_export(Conn(), date(2026, 11, 10), date(2026, 11, 11), dirs, phones=False,
                   now=datetime(2026, 11, 12, 9))
by = {c["id"]: c for c in ex.calls}
assert set(by) == {"c1", "c2", "c3", "c4"}, "звонок после периода не выгружается"
assert by["c1"]["пропущен"] == "да" and by["c1"]["перезвонили"] == "да" and by["c1"]["перезвон_через_мин"] == 30.0
assert by["c3"]["пропущен"] == "да" and by["c3"]["перезвонили"] == "да", "перезвон найден в запасе после периода"
assert by["c2"]["направление"] == "исходящий" and by["c2"]["пропущен"] == "нет"
assert by["c4"]["направление"] == "входящий" and by["c4"]["перезвонили"] == ""
assert "телефон" not in by["c1"]
t1, t2 = ex.tasks
assert t1["просрочено"] == "да" and t1["опоздание_ч"] == 2.0, "выполнено позже срока"
assert t2["просрочено"] == "да" and t2["выполнено"] == "нет"
r = ex.requests[0]
assert r["часов_до_работы"] == 2.0 and r["источник_рекламы"] == "Яндекс"
mgr = {m["менеджер"]: m for m in ex.managers}
assert mgr["Менеджер 1"]["пропущено"] == 2 and mgr["Менеджер 1"]["задач_просрочено"] == 2
assert {c["клиент_код"] for c in ex.clients} == {"0001", "0002"}

with tempfile.TemporaryDirectory() as tmp:
    files = E.save_export(ex, Path(tmp))
    names = sorted(p.name for p in files)
    assert names == ["calls.csv", "clients.csv", "crm.json", "managers.csv", "requests.csv", "tasks.csv"], names
    raw = (Path(tmp) / "Выгрузка CRM 10.11.2026-11.11.2026" / "calls.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM - Excel откроет кириллицу"
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("id;начало;окончание")
    assert "1112233" not in text and "5556677" not in text, "телефонов нет"
    data = json.loads((files[-1].parent / "crm.json").read_text(encoding="utf-8"))
    assert data["период"] == {"с": "2026-11-10", "по": "2026-11-11"} and len(data["calls"]) == 4

# с телефонами - только последние 10 цифр
ex2 = E.load_export(Conn(), date(2026, 11, 10), date(2026, 11, 11), dirs, phones=True)
assert {c["телефон"] for c in ex2.calls} >= {"9101112233"}
# пустые таблицы - заголовки всё равно есть
assert E.to_csv([], "tasks").decode("utf-8-sig").startswith("создана;дата;тип")
# коды не заданы - направление «код N», пропущенный определяется по длительности 0
ex3 = E.load_export(Conn(), date(2026, 11, 10), date(2026, 11, 11), E.Directions())
assert {c["направление"] for c in ex3.calls} == {"код 1", "код 2"}
print("OK: выгрузка для витрины")
