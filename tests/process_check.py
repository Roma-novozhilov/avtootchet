"""«Анализ процессов»: журнал событий из поддельной 1С, метрики и PDF по ГОСТ.

    python tests/process_check.py            # проверка
    python tests/process_check.py out.pdf    # и сохранить PDF, чтобы посмотреть глазами
"""
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.owner import process as P  # noqa: E402
from avtootchet.owner.process_pdf import build_process_pdf, process_filename  # noqa: E402


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
        self.conn, self.Text = conn, ""

    def Execute(self):
        rows = self.conn.answer(self.Text)
        return type("R", (), {"Choose": lambda _s: Sel(rows)})()


EMPTY = datetime(1, 1, 1)


def fake_base(n=400, seed=7):
    rnd = random.Random(seed)
    zn, parts_in, parts_out, pay, recs = [], [], [], [], []
    users = ["Семин В.", "Иванов А.", "Петрова Е.", "Склад"]
    masters = ["Семин В.", "Кузнецов Д."]
    t0 = datetime(2026, 5, 12, 9)
    for i in range(n):
        created = t0 + timedelta(days=rnd.randint(0, 180), hours=rnd.choice([0, 1, 2, 3, 5, 7, 8]),
                                 minutes=rnd.randint(0, 59))
        kind = rnd.choices(["СЕРВИС", "Сервис (БЧ)", "Гарантийный"], [6, 4, 1])[0]
        num = f"26СЧ{i:05d}"
        row = {"Номер": num, "Дата": created, "Создан": created, "ВРаботе": EMPTY, "Завершен": EMPTY,
               "Отгружен": EMPTY, "ПланНачало": created + timedelta(hours=1),
               "ПланКонец": created + timedelta(hours=6), "Статус": "Предварительный", "Тип": kind,
               "Автор": rnd.choice(users), "КтоВРаботу": "", "КтоЗавершил": "", "КтоОтгрузил": "",
               "Мастер": rnd.choice(masters), "Пост": rnd.choice(["Пост 1", "Пост 2", "Шиномонтаж"]),
               "Повтор": rnd.random() < 0.03, "Оценка": rnd.choice([0, 0, 0, 5, 4]),
               "Отказ": "", "ИзРекомендации": rnd.random() < 0.06, "ТекстРек": rnd.random() < 0.3}
        r = rnd.random()
        if r < 0.06:
            row["Отказ"] = rnd.choice(["", "Дорого", "Не приехал"])
        else:
            work = created + timedelta(hours=rnd.expovariate(1 / 6))
            if rnd.random() < 0.3:
                parts_in.append({"Номер": num, "Первый": created + timedelta(hours=rnd.expovariate(1 / 30)), "Кол": 1})
            row.update(ВРаботе=work, КтоВРаботу=rnd.choice(users), Статус="В работе")
            if rnd.random() < 0.8:
                parts_out.append({"Номер": num, "Первый": work + timedelta(minutes=rnd.randint(-60, 120)), "Кол": 1})
            if rnd.random() < 0.97:
                done = work + timedelta(hours=rnd.expovariate(1 / 5))
                row.update(Завершен=done, КтоЗавершил=rnd.choice(users), Статус="Завершен")
                if rnd.random() < 0.97:
                    ship = done + timedelta(hours=rnd.expovariate(1 / 8))
                    row.update(Отгружен=ship, КтоОтгрузил=rnd.choice(users), Статус="Отгружен")
                    pay.append({"Номер": num, "Первый": ship + timedelta(minutes=rnd.randint(-30, 10)), "Кол": 1})
            if rnd.random() < 0.13:
                recs.append({"Номер": num, "Кол": rnd.randint(1, 3)})
        zn.append(row)
    return {"zn": zn, "in": parts_in, "out": parts_out, "pay": pay, "recs": recs}


class Conn:
    def __init__(self, base):
        self.base, self.texts = base, []

    def NewObject(self, name):
        assert name == "Запрос"
        return Query(self)

    def answer(self, text):
        self.texts.append(text)
        assert "&Начало" not in text, "даты подставляются литералом"
        if "Документ.ЗаказНаряд КАК З" in text:
            return self.base["zn"]
        if "Документ.ПриходнаяНакладная" in text:
            return self.base["in"]
        if "Документ.ВнутренняяНакладная" in text:
            return self.base["out"]
        if "Документ.ПриходДенег" in text:
            return self.base["pay"]
        if "Документ.Рекомендация" in text and "Р.Дата МЕЖДУ" in text:
            return [{"Кол": 60}]
        if "Документ.Рекомендация" in text:
            return self.base["recs"]
        raise AssertionError(text)


conn = Conn(fake_base())
data = P.load_process(conn, date(2026, 5, 12), date(2026, 11, 10), on=date(2026, 11, 10), stuck_days=7)
n = len(data.cases)
assert n == 400
assert all(P.CREATED in c.events for c in data.cases)
filled = dict(data.filled())
assert 0 < filled[P.WORK] < n and filled[P.PARTS_OUT] > 0 and filled[P.PAID] > 0
order = data.step_order()
assert order[0] == P.CREATED and order[-1] in (P.SHIPPED, P.PAID)
trans = data.transitions()
assert any(a == P.CREATED and b == P.NOT_STARTED for a, b, *_ in trans), "предварительные без работы"
s, bad = data.stage(P.CREATED, P.WORK)
assert s and s.n == filled[P.WORK] and s.median > 0
assert data.stuck(), "есть незакрытые ЗН старше 7 дней"
assert sum(data.weekday_load()) == n
text_rec, recs, linked, from_rec = data.recommendations()
assert text_rec and recs == 60 and linked and from_rec
assert P.fmt_hours(0.5) == "30 мин" and P.fmt_hours(5.25) == "5,2 ч" and P.fmt_hours(72) == "3,0 дн."

# пустые даты 1С (01.01.0001) - не шаг
c0 = next(c for c in data.cases if c.status == "Предварительный")
assert P.WORK not in c0.events and P.DONE not in c0.events

pdf = build_process_pdf(data)
assert pdf[:5] == b"%PDF-" and len(pdf) > 20000
assert process_filename(data) == "Анализ процессов 12.05.2026-10.11.2026.pdf"
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_bytes(pdf)
    print("PDF:", sys.argv[1])

# редкий шаг (как «Запчасти пришли» у 2 % ЗН в настоящей базе) не выносится на схему
from avtootchet.owner.process_pdf import Builder  # noqa: E402
for c in data.cases[5:]:
    c.events.pop(P.PARTS_IN, None)
b = Builder(data, "Авто Центр", "Иваново")
assert "«Запчасти пришли»" in b._hidden_steps() and "не вынесен" in b._hidden_steps()
assert build_process_pdf(data)[:5] == b"%PDF-"

# пустая база не роняет отчёт
empty = P.ProcessData(date(2026, 5, 12), date(2026, 11, 10), date(2026, 11, 10), 7, [])
assert build_process_pdf(empty)[:5] == b"%PDF-"
print("OK: анализ процессов")
