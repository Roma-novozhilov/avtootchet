"""Анализ бизнес-процесса «обслуживание автомобиля по заказ-наряду» по данным Далиона.

Только чтение. Каждый заказ-наряд (ЗН) - один «случай» процесса. Шаги и время берутся
из реквизитов самого ЗН (ДатаСоздания, ДатаПереведенВРаботу, ДатаЗавершен, ДатаОтгружен
и кто это сделал) и из связанных документов (приход запчастей под ЗН, выдача запчастей
внутренней накладной, оплата). Из этого строится журнал событий и всё остальное:
схема переходов, варианты процесса, длительности этапов, план/факт, зависшие ЗН, нагрузка.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..common.comdate import com_datetime, inline_dates

# шаги процесса в «правильном» порядке (он же порядок на схеме)
CREATED, PARTS_IN, WORK, PARTS_OUT, DONE, PAID, SHIPPED = (
    "Создан", "Запчасти пришли", "Переведён в работу", "Запчасти выданы", "Завершён", "Оплачен",
    "Выдан клиенту")
NOT_STARTED = "Не начат"
STEPS = [CREATED, PARTS_IN, WORK, PARTS_OUT, DONE, PAID, SHIPPED]
ORDER = {s: i for i, s in enumerate(STEPS + [NOT_STARTED])}

# этапы для таблицы длительностей: (название, от, до)
STAGES = [
    ("Ожидание начала работ", CREATED, WORK),
    ("Ремонт (от начала до завершения)", WORK, DONE),
    ("Ожидание выдачи после завершения", DONE, SHIPPED),
    ("Полный цикл (от создания до выдачи)", CREATED, SHIPPED),
    ("Поставка запчастей под ЗН", CREATED, PARTS_IN),
]


@dataclass
class Case:
    number: str
    doc_date: datetime
    status: str
    kind: str
    master: str = ""
    post: str = ""
    repeat: bool = False
    rating: float = 0.0
    refusal: str = ""
    from_rec: bool = False
    recs: int = 0
    plan_start: datetime | None = None
    plan_end: datetime | None = None
    events: dict = field(default_factory=dict)    # шаг -> (время, кто)

    def at(self, step: str) -> datetime | None:
        e = self.events.get(step)
        return e[0] if e else None

    def status_is_preliminary(self) -> bool:
        return "предвар" in self.status.lower()

    def trace(self) -> list[str]:
        steps = sorted(self.events.items(), key=lambda kv: (kv[1][0], ORDER[kv[0]]))
        return [s for s, _ in steps]


@dataclass
class Stat:
    n: int
    median: float     # часы
    mean: float
    p90: float


@dataclass
class ProcessData:
    d1: date
    d2: date
    on: date
    stuck_days: int
    cases: list[Case]
    notes: list[str] = field(default_factory=list)     # что не удалось прочитать

    # ---------------------------------------------------------------- объёмы
    def by(self, key) -> list[tuple[str, int]]:
        return Counter(key(c) or "(не указан)" for c in self.cases).most_common()

    def filled(self) -> list[tuple[str, int]]:
        return [(s, sum(1 for c in self.cases if s in c.events)) for s in STEPS]

    # ---------------------------------------------------------------- модель процесса
    def variants(self, top: int = 10) -> list[tuple[list[str], int]]:
        cnt = Counter(tuple(c.trace()) for c in self.cases)
        return [(list(v), n) for v, n in cnt.most_common(top)]

    def step_order(self) -> list[str]:
        """Шаги в том порядке, в каком они обычно идут в этой базе (по среднему месту в цепочке)."""
        pos = defaultdict(list)
        for c in self.cases:
            tr = c.trace()
            for i, s in enumerate(tr):
                pos[s].append(i / max(len(tr) - 1, 1))
        present = [s for s in STEPS if pos[s]]
        return sorted(present, key=lambda s: (statistics.fmean(pos[s]), ORDER[s]))

    def variant_count(self) -> int:
        return len({tuple(c.trace()) for c in self.cases})

    def transitions(self) -> list[tuple[str, str, int, float | None]]:
        """Прямые переходы «шаг -> следующий шаг»: сколько ЗН и медиана времени, ч."""
        times = defaultdict(list)
        for c in self.cases:
            tr = c.trace()
            for a, b in zip(tr, tr[1:]):
                times[(a, b)].append(_hours(c.at(a), c.at(b)))
            if c.status_is_preliminary() and WORK not in c.events:
                times[(tr[-1] if tr else CREATED, NOT_STARTED)].append(None)
        out = []
        for (a, b), ts in times.items():
            vals = [t for t in ts if t is not None and t >= 0]
            out.append((a, b, len(ts), statistics.median(vals) if vals else None))
        return sorted(out, key=lambda x: (ORDER[x[0]], ORDER[x[1]]))

    def step_counts(self) -> dict[str, int]:
        c = Counter(s for case in self.cases for s in case.events)
        c[NOT_STARTED] = sum(1 for x in self.cases if x.status_is_preliminary() and WORK not in x.events)
        return dict(c)

    # ---------------------------------------------------------------- длительности
    def stage(self, a: str, b: str, kind: str | None = None) -> tuple[Stat | None, int]:
        """Статистика времени между шагами (часы) и сколько записей отброшено как ошибочные."""
        vals, bad = [], 0
        for c in self.cases:
            if kind and c.kind != kind:
                continue
            h = _hours(c.at(a), c.at(b))
            if h is None:
                continue
            if h < -0.02:
                bad += 1
                continue
            vals.append(max(h, 0.0))
        return _stat(vals), bad

    def main_kinds(self, n: int = 3) -> list[str]:
        return [k for k, _ in self.by(lambda c: c.kind)[:n] if k != "(не указан)"]

    def payment_vs_shipping(self) -> tuple[int, int, int]:
        """(оплачен до выдачи, в день выдачи ±1 ч, после выдачи) - по ЗН, где есть оба события."""
        before = same = after = 0
        for c in self.cases:
            h = _hours(c.at(SHIPPED), c.at(PAID))
            if h is None:
                continue
            if h < -1:
                before += 1
            elif h <= 1:
                same += 1
            else:
                after += 1
        return before, same, after

    # ---------------------------------------------------------------- план / факт
    def plan_fact(self, which: str) -> tuple[Stat | None, int, int]:
        """Опоздание (ч, + опоздали) начала или завершения. Возвращает (стат, вовремя, всего)."""
        vals = []
        for c in self.cases:
            plan, fact = (c.plan_start, c.at(WORK)) if which == "start" else (c.plan_end, c.at(DONE))
            h = _hours(plan, fact)
            if h is not None:
                vals.append(h)
        on_time = sum(1 for v in vals if v <= 1)
        return _stat([max(v, 0) for v in vals]), on_time, len(vals)

    # ---------------------------------------------------------------- зависшие
    def stuck(self) -> list[tuple[Case, str, datetime, int]]:
        """ЗН, которые на дату отчёта висят на шаге дольше stuck_days: (ЗН, где, с какого времени, дней)."""
        now = datetime.combine(self.on, datetime.max.time())
        out = []
        for c in self.cases:
            st = c.status.lower()
            if "отгруж" in st or "затрат" in st:
                continue
            if DONE in c.events and SHIPPED not in c.events:
                where, since = "Завершён, не выдан", c.at(DONE)
            elif WORK in c.events and DONE not in c.events:
                where, since = "В работе", c.at(WORK)
            elif WORK not in c.events:
                where, since = "Предварительный", c.at(CREATED) or c.doc_date
            else:
                continue
            days = (now - since).days
            if days >= self.stuck_days:
                out.append((c, where, since, days))
        return sorted(out, key=lambda x: (x[1], -x[3]))

    # ---------------------------------------------------------------- нагрузка
    def weekday_load(self) -> list[int]:
        cnt = [0] * 7
        for c in self.cases:
            t = c.at(CREATED) or c.doc_date
            cnt[t.weekday()] += 1
        return cnt

    def hour_load(self) -> dict[int, int]:
        return dict(sorted(Counter((c.at(CREATED) or c.doc_date).hour for c in self.cases).items()))

    def who(self, step: str) -> list[tuple[str, int]]:
        return Counter(c.events[step][1] or "(не указан)" for c in self.cases if step in c.events).most_common()

    def posts(self) -> list[tuple[str, int, Stat | None]]:
        groups = defaultdict(list)
        for c in self.cases:
            groups[c.post or "(не указан)"].append(c)
        out = []
        for p, cs in groups.items():
            vals = [h for h in (_hours(c.at(WORK), c.at(DONE)) for c in cs) if h is not None and h >= 0]
            out.append((p, len(cs), _stat(vals)))
        return sorted(out, key=lambda x: -x[1])

    # ---------------------------------------------------------------- качество и продажи
    def recommendations(self) -> tuple[int, int, int]:
        """(ЗН с рекомендациями, всего рекомендаций, ЗН, созданных из рекомендации)."""
        return (sum(1 for c in self.cases if c.recs), sum(c.recs for c in self.cases),
                sum(1 for c in self.cases if c.from_rec))

    def repeats(self) -> int:
        return sum(1 for c in self.cases if c.repeat)

    def ratings(self) -> tuple[int, float | None]:
        r = [c.rating for c in self.cases if c.rating]
        return len(r), (sum(r) / len(r) if r else None)

    def refusals(self) -> list[tuple[str, int]]:
        return Counter(c.refusal or "(причина не указана)" for c in self.cases
                       if c.status_is_preliminary() and WORK not in c.events).most_common()


def _hours(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600


def _stat(vals: list[float]) -> Stat | None:
    if not vals:
        return None
    s = sorted(vals)
    p90 = s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]
    return Stat(len(s), statistics.median(s), statistics.fmean(s), p90)


def fmt_hours(h: float | None) -> str:
    """Часы -> «35 мин», «5,2 ч», «3,4 дн.»"""
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)} мин"
    if h < 48:
        return f"{h:.1f}".replace(".", ",") + " ч"
    return f"{h / 24:.1f}".replace(".", ",") + " дн."


# ==================================================================== чтение из 1С
def _dt(v) -> datetime | None:
    """Пустая дата 1С (01.01.0001) -> None."""
    if v is None or not hasattr(v, "year") or v.year < 1990:
        return None
    return com_datetime(v)


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


Q_ZN = """ВЫБРАТЬ
	З.Номер КАК Номер,
	З.Дата КАК Дата,
	З.ДатаСоздания КАК Создан,
	З.ДатаПереведенВРаботу КАК ВРаботе,
	З.ДатаЗавершен КАК Завершен,
	З.ДатаОтгружен КАК Отгружен,
	З.ПланируемаяДатаНачалаРабот КАК ПланНачало,
	З.ПланируемаяДатаЗавершения КАК ПланКонец,
	ПРЕДСТАВЛЕНИЕ(З.СтатусЗН) КАК Статус,
	ПРЕДСТАВЛЕНИЕ(З.ТипЗН) КАК Тип,
	ПРЕДСТАВЛЕНИЕ(З.Автор) КАК Автор,
	ПРЕДСТАВЛЕНИЕ(З.КтоПеревелВРаботу) КАК КтоВРаботу,
	ПРЕДСТАВЛЕНИЕ(З.КтоЗавершил) КАК КтоЗавершил,
	ПРЕДСТАВЛЕНИЕ(З.КтоОтгрузил) КАК КтоОтгрузил,
	ПРЕДСТАВЛЕНИЕ(З.МастерПриемщик) КАК Мастер,
	ПРЕДСТАВЛЕНИЕ(З.Пост) КАК Пост,
	З.Повтор КАК Повтор,
	З.Оценка КАК Оценка,
	ПРЕДСТАВЛЕНИЕ(З.ПричинаОтказа) КАК Отказ,
	ВЫБОР КОГДА З.ДокументОснование ССЫЛКА Документ.Рекомендация ТОГДА ИСТИНА ИНАЧЕ ЛОЖЬ КОНЕЦ КАК ИзРекомендации
ИЗ
	Документ.ЗаказНаряд КАК З
ГДЕ
	НЕ З.ПометкаУдаления
	И З.Дата МЕЖДУ &Начало И &Конец"""

# связанные документы: номер ЗН и время первого (и последнего) документа
_LINKED = """ВЫБРАТЬ
	ВЫРАЗИТЬ(Д.{field} КАК Документ.ЗаказНаряд).Номер КАК Номер,
	МИНИМУМ(Д.Дата) КАК Первый,
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	Документ.{doc} КАК Д
ГДЕ
	Д.Проведен
	И Д.{field} ССЫЛКА Документ.ЗаказНаряд
	И Д.Дата >= &Начало
СГРУППИРОВАТЬ ПО
	ВЫРАЗИТЬ(Д.{field} КАК Документ.ЗаказНаряд).Номер"""

LINKED = {
    PARTS_IN: ("ПриходнаяНакладная", "ДокументОснование"),
    PARTS_OUT: ("ВнутренняяНакладная", "ДокументОснование"),
    PAID: ("ПриходДенег", "ДокументОснование"),
}

Q_RECS = """ВЫБРАТЬ
	ВЫРАЗИТЬ(Р.ДокументОснование КАК Документ.ЗаказНаряд).Номер КАК Номер,
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	Документ.Рекомендация КАК Р
ГДЕ
	НЕ Р.ПометкаУдаления
	И Р.ДокументОснование ССЫЛКА Документ.ЗаказНаряд
	И Р.Дата >= &Начало
СГРУППИРОВАТЬ ПО
	ВЫРАЗИТЬ(Р.ДокументОснование КАК Документ.ЗаказНаряд).Номер"""

ZN_COLS = ("Номер", "Дата", "Создан", "ВРаботе", "Завершен", "Отгружен", "ПланНачало", "ПланКонец",
           "Статус", "Тип", "Автор", "КтоВРаботу", "КтоЗавершил", "КтоОтгрузил", "Мастер", "Пост",
           "Повтор", "Оценка", "Отказ", "ИзРекомендации")


def load_process(conn, d1: date, d2: date, on: date | None = None, stuck_days: int = 7) -> ProcessData:
    start = datetime(d1.year, d1.month, d1.day)
    end = datetime(d2.year, d2.month, d2.day, 23, 59, 59)

    def run(text: str, cols: tuple[str, ...]):
        q = conn.NewObject("Запрос")
        q.Text, _ = inline_dates(text, {"Начало": start, "Конец": end})
        sel = q.Execute().Choose()
        rows = []
        while sel.Next():
            rows.append({c: getattr(sel, c) for c in cols})
        return rows

    data = ProcessData(d1, d2, on or date.today(), stuck_days, [])
    by_num: dict[str, Case] = {}
    for r in run(Q_ZN, ZN_COLS):
        created = _dt(r["Создан"]) or _dt(r["Дата"])
        c = Case(_s(r["Номер"]), _dt(r["Дата"]) or created, _s(r["Статус"]), _s(r["Тип"]),
                 master=_s(r["Мастер"]), post=_s(r["Пост"]), repeat=bool(r["Повтор"]),
                 rating=_num(r["Оценка"]), refusal=_s(r["Отказ"]), from_rec=bool(r["ИзРекомендации"]),
                 plan_start=_dt(r["ПланНачало"]), plan_end=_dt(r["ПланКонец"]))
        for step, col, who in ((CREATED, None, "Автор"), (WORK, "ВРаботе", "КтоВРаботу"),
                               (DONE, "Завершен", "КтоЗавершил"), (SHIPPED, "Отгружен", "КтоОтгрузил")):
            t = created if col is None else _dt(r[col])
            if t:
                c.events[step] = (t, _s(r[who]))
        data.cases.append(c)
        by_num[c.number] = c

    for step, (doc, fld) in LINKED.items():
        try:
            rows = run(_LINKED.format(doc=doc, field=fld), ("Номер", "Первый", "Кол"))
        except Exception as e:  # noqa: BLE001 - в другой базе документа может не быть
            data.notes.append(f"«{step}»: не прочитано ({e})")
            continue
        for r in rows:
            c = by_num.get(_s(r["Номер"]))
            t = _dt(r["Первый"])
            if c and t:
                c.events[step] = (t, "")
    try:
        for r in run(Q_RECS, ("Номер", "Кол")):
            c = by_num.get(_s(r["Номер"]))
            if c:
                c.recs = int(_num(r["Кол"]))
    except Exception as e:  # noqa: BLE001
        data.notes.append(f"рекомендации: не прочитаны ({e})")
    return data


def default_period(today: date, months: int = 6) -> tuple[date, date]:
    from .stock import months_ago  # noqa: PLC0415
    return months_ago(today, months) + timedelta(days=1), today
