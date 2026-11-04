"""Аналитика для владельца поверх отчёта по выручке. Только чтение базы.

* Прибыль по продажам - регистр «Продажи» Далиона: продажа, себестоимость запчастей,
  зарплата механиков, сторонние работы -> валовая прибыль и наценка по направлениям.
* Долги клиентов - заказ-наряды, у которых сумма больше, чем по ним оплачено.
* Выработка механиков и мастеров-приёмщиков - регистр «ЗаказНаряды»: нормочасы и суммы.
* Эквайринг и СБП - по перемещениям из касс терминала/QR на расчётный счёт: сколько
  прошло, сколько банк зачислил, комиссия.
* Динамика - выручка по месяцам за год и сравнение периода с прошлым периодом
  и тем же периодом прошлого года.

Каждый раздел считается отдельно: если 1С отвергнет один запрос, остальные разделы
и сам отчёт по выручке всё равно построятся, а ошибка попадёт на лист раздела.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .revenue import (CASH, METAL, QR, SERVICE, TERMINAL, Expense, Transfer, classify_cashbox,
                      _division_direction)

# ------------------------------------------------------------------ данные


@dataclass
class SalesRow:
    """Строка регистра «Продажи» за период: направление × работы/товары/материалы."""
    division: str
    kind: str                # Услуги / Товары / Материалы (как в 1С)
    sales: float
    cost: float              # себестоимость
    salary: float            # зарплата механиков
    subcontract: float       # сторонние организации
    extra: float             # доп. затраты
    agents: float            # посредникам

    @property
    def direction(self) -> str:
        return _division_direction(self.division) or "Подразделение не указано"

    @property
    def gross(self) -> float:
        return round(self.sales - self.cost - self.salary - self.subcontract - self.extra - self.agents, 2)


@dataclass
class Debt:
    dt: datetime
    number: str
    client: str
    car: str
    plate: str
    status: str
    order_type: str
    organization: str
    master: str
    amount: float
    paid: float
    refunded: float
    bonuses: float

    @property
    def debt(self) -> float:
        return round(self.amount - self.paid + self.refunded - self.bonuses, 2)


@dataclass
class WorkerRow:
    name: str
    hours_norm: float = 0.0
    hours_fact: float = 0.0
    works: float = 0.0
    parts: float = 0.0
    orders: int = 0


@dataclass
class AcquiringDay:
    day: date
    cashbox: str
    method: str
    payments: float          # оплаты клиентов в эту кассу за день
    sent: float              # списано из кассы на счёт (перемещения)
    credited: float          # зачислено банком

    @property
    def commission(self) -> float:
        return round(self.sent - self.credited, 2)


@dataclass
class Analytics:
    sales: list[SalesRow] = field(default_factory=list)
    debts: list[Debt] = field(default_factory=list)
    debts_since: date | None = None
    mechanics: list[WorkerRow] = field(default_factory=list)
    masters: list[WorkerRow] = field(default_factory=list)
    acquiring: list[AcquiringDay] = field(default_factory=list)
    commissions: list[Expense] = field(default_factory=list)
    # динамика: (день, направление) -> выручка
    daily: dict = field(default_factory=dict)
    daily_from: date | None = None
    errors: dict = field(default_factory=dict)   # раздел -> текст ошибки 1С


# ------------------------------------------------------------------ запросы
Q_SALES = """ВЫБРАТЬ
	ПРЕДСТАВЛЕНИЕ(П.Подразделение) КАК Подразделение,
	ПРЕДСТАВЛЕНИЕ(П.УслугиТоварыМатериалы) КАК Вид,
	СУММА(П.ВсегоУпрОборот) КАК Продажа,
	СУММА(П.СтоимостьУпрОборот) КАК Себестоимость,
	СУММА(П.ЗарплатаУпрОборот) КАК Зарплата,
	СУММА(П.СторонниеОргУпрОборот) КАК Сторонние,
	СУММА(П.ДопЗатратыУпрОборот) КАК ДопЗатраты,
	СУММА(П.ПосредническиеУпрОборот) КАК Посредникам
ИЗ
	РегистрНакопления.Продажи.Обороты(&Начало, &Конец) КАК П
СГРУППИРОВАТЬ ПО
	П.Подразделение,
	П.УслугиТоварыМатериалы"""

Q_DEBTS = """ВЫБРАТЬ
	З.Дата КАК Дата,
	З.Номер КАК Номер,
	ПРЕДСТАВЛЕНИЕ(З.Контрагент) КАК Клиент,
	ПРЕДСТАВЛЕНИЕ(З.Автомобиль) КАК Автомобиль,
	ПРЕДСТАВЛЕНИЕ(З.ГосНомер) КАК ГосНомер,
	ПРЕДСТАВЛЕНИЕ(З.СтатусЗН) КАК Статус,
	ПРЕДСТАВЛЕНИЕ(З.ТипЗН) КАК ТипЗН,
	ПРЕДСТАВЛЕНИЕ(З.Организация) КАК Организация,
	ПРЕДСТАВЛЕНИЕ(З.МастерПриемщик) КАК Мастер,
	З.СуммаДокумента КАК Сумма,
	ЕСТЬNULL(З.СуммаОплатыИзБонусов, 0) КАК Бонусы,
	ЕСТЬNULL(ОП.С, 0) КАК Оплачено,
	ЕСТЬNULL(ВЗ.С, 0) КАК Возвращено
ИЗ
	Документ.ЗаказНаряд КАК З
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ П.ДокументОснование КАК Ссылка, СУММА(П.СуммаДокумента) КАК С
			ИЗ Документ.ПриходДенег КАК П
			ГДЕ П.Проведен И ТИПЗНАЧЕНИЯ(П.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
			СГРУППИРОВАТЬ ПО П.ДокументОснование) КАК ОП
		ПО (ОП.Ссылка = З.Ссылка)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Р.ДокументОснование КАК Ссылка, СУММА(Р.СуммаДокумента) КАК С
			ИЗ Документ.РасходДенег КАК Р
			ГДЕ Р.Проведен И ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
			СГРУППИРОВАТЬ ПО Р.ДокументОснование) КАК ВЗ
		ПО (ВЗ.Ссылка = З.Ссылка)
ГДЕ
	З.Проведен
	И З.Дата МЕЖДУ &НачалоДолгов И &Конец
	И З.СуммаДокумента > 0
	И З.СуммаДокумента - ЕСТЬNULL(ОП.С, 0) + ЕСТЬNULL(ВЗ.С, 0) - ЕСТЬNULL(З.СуммаОплатыИзБонусов, 0) > 1
УПОРЯДОЧИТЬ ПО
	З.Дата"""

Q_MECHANICS = """ВЫБРАТЬ
	ПРЕДСТАВЛЕНИЕ(Р.{dim}) КАК Имя,
	СУММА(Р.КоличествоНЧОборот) КАК НЧ,
	СУММА(Р.КоличествоНЧФактОборот) КАК НЧФакт,
	СУММА(Р.СуммаРаботОборот) КАК Работы,
	СУММА(Р.СуммаЗЧОборот) КАК ЗЧ,
	КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Р.ЗаказНаряд) КАК ЗН
ИЗ
	РегистрНакопления.ЗаказНаряды.Обороты(&Начало, &Конец) КАК Р
СГРУППИРОВАТЬ ПО
	Р.{dim}"""

Q_DAILY = """ВЫБРАТЬ
	НАЧАЛОПЕРИОДА(П.Дата, ДЕНЬ) КАК День,
	ПРЕДСТАВЛЕНИЕ(П.СчетДенежныхСредств) КАК Касса,
	ПРЕДСТАВЛЕНИЕ(П.Подразделение) КАК Подразделение,
	СУММА(П.СуммаДокумента) КАК Сумма
ИЗ
	Документ.ПриходДенег КАК П
ГДЕ
	П.Проведен
	И П.Дата МЕЖДУ &НачалоДинамики И &Конец
	И П.СчетДенежныхСредств ССЫЛКА Справочник.Кассы
	И (ТИПЗНАЧЕНИЯ(П.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
		ИЛИ ТИПЗНАЧЕНИЯ(П.ДокументОснование) = ТИП(Документ.РасходнаяНакладная))
СГРУППИРОВАТЬ ПО
	НАЧАЛОПЕРИОДА(П.Дата, ДЕНЬ),
	П.СчетДенежныхСредств,
	П.Подразделение

ОБЪЕДИНИТЬ ВСЕ

ВЫБРАТЬ
	НАЧАЛОПЕРИОДА(Р.Дата, ДЕНЬ),
	ПРЕДСТАВЛЕНИЕ(Р.СчетДенежныхСредств),
	ПРЕДСТАВЛЕНИЕ(Р.Подразделение),
	-СУММА(Р.СуммаДокумента)
ИЗ
	Документ.РасходДенег КАК Р
ГДЕ
	Р.Проведен
	И Р.Дата МЕЖДУ &НачалоДинамики И &Конец
	И Р.СчетДенежныхСредств ССЫЛКА Справочник.Кассы
	И (ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
		ИЛИ ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.РасходнаяНакладная))
СГРУППИРОВАТЬ ПО
	НАЧАЛОПЕРИОДА(Р.Дата, ДЕНЬ),
	Р.СчетДенежныхСредств,
	Р.Подразделение"""


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def direction_of(cashbox: str, division: str) -> str:
    """Направление оплаты - по тем же правилам, что в отчёте по выручке."""
    d, method = classify_cashbox(cashbox)
    div = _division_direction(division)
    if method == CASH and div:
        return div
    return d


def dynamics_start(d1: date, d2: date) -> date:
    """С какой даты нужна выручка: 12 полных месяцев до конца периода, прошлый такой же
    период и тот же период год назад."""
    year_ago = d1.replace(year=d1.year - 1) if not (d1.month == 2 and d1.day == 29) else d1 - timedelta(days=366)
    months12 = date(d2.year - 1, d2.month, 1)
    prev = d1 - (d2 - d1) - timedelta(days=1)
    return min(year_ago, months12, prev)


# ------------------------------------------------------------------ загрузка
def acquiring_days(payments, transfers: list[Transfer]) -> list[AcquiringDay]:
    """Касса терминала/QR: оплаты клиентов за день против перемещений на расчётный счёт."""
    rows: dict[tuple[date, str], AcquiringDay] = {}
    for p in payments:
        if p.is_revenue and p.method in (TERMINAL, QR):
            r = rows.setdefault((p.dt.date(), p.cashbox), AcquiringDay(p.dt.date(), p.cashbox, p.method, 0, 0, 0))
            r.payments += p.amount
    for t in transfers:
        _, method = classify_cashbox(t.source)
        if method in (TERMINAL, QR):
            r = rows.setdefault((t.dt.date(), t.source), AcquiringDay(t.dt.date(), t.source, method, 0, 0, 0))
            r.sent += t.amount_out
            r.credited += t.amount_in
    for r in rows.values():
        r.payments, r.sent, r.credited = round(r.payments, 2), round(r.sent, 2), round(r.credited, 2)
    return sorted(rows.values(), key=lambda r: (r.day, r.cashbox))


def commission_expenses(payments, transfers: list[Transfer]) -> list[Expense]:
    """Комиссия банка (списано из кассы терминала/QR минус зачислено) - как расход."""
    orgs = {p.cashbox: p.organization for p in payments if p.organization}
    out = []
    for t in transfers:
        _, method = classify_cashbox(t.source)
        fee = round(t.amount_out - t.amount_in, 2)
        if method in (TERMINAL, QR) and fee > 0:
            name = "Комиссия банка: эквайринг" if method == TERMINAL else "Комиссия банка: СБП / QR"
            out.append(Expense(dt=t.dt, number=t.number, account=t.source, source="Касса",
                               operation="Конвертация ДС (потери)", counterparty=t.target,
                               article=name, movement="", division="",
                               organization=orgs.get(t.source, ""), basis="Конвертация ДС",
                               amount=fee, comment=t.comment))
    return out


def load_analytics(conn, d1: date, d2: date, payments, transfers: list[Transfer],
                   run: Callable) -> Analytics:
    """run(text, cols, **params) - выполнить запрос (как в load_revenue)."""
    a = Analytics()

    def section(name: str, fn: Callable[[], None]):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - один раздел не должен ронять весь отчёт
            a.errors[name] = str(e)

    def sales():
        a.sales = [SalesRow(_str(r["Подразделение"]), _str(r["Вид"]), _num(r["Продажа"]),
                            _num(r["Себестоимость"]), _num(r["Зарплата"]), _num(r["Сторонние"]),
                            _num(r["ДопЗатраты"]), _num(r["Посредникам"]))
                   for r in run(Q_SALES, ("Подразделение", "Вид", "Продажа", "Себестоимость", "Зарплата",
                                          "Сторонние", "ДопЗатраты", "Посредникам"))]

    def debts():
        since = date(d2.year - 1, d2.month, d2.day) if not (d2.month == 2 and d2.day == 29) else d2 - timedelta(days=366)
        a.debts_since = since
        cols = ("Дата", "Номер", "Клиент", "Автомобиль", "ГосНомер", "Статус", "ТипЗН", "Организация",
                "Мастер", "Сумма", "Бонусы", "Оплачено", "Возвращено")
        from .revenue import _dt  # noqa: PLC0415
        a.debts = [Debt(_dt(r["Дата"]), _str(r["Номер"]), _str(r["Клиент"]), _str(r["Автомобиль"]),
                        _str(r["ГосНомер"]), _str(r["Статус"]), _str(r["ТипЗН"]), _str(r["Организация"]),
                        _str(r["Мастер"]), _num(r["Сумма"]), _num(r["Оплачено"]), _num(r["Возвращено"]),
                        _num(r["Бонусы"]))
                   for r in run(Q_DEBTS, cols, НачалоДолгов=datetime(since.year, since.month, since.day))]

    def workers(dim: str) -> list[WorkerRow]:
        rows = run(Q_MECHANICS.format(dim=dim), ("Имя", "НЧ", "НЧФакт", "Работы", "ЗЧ", "ЗН"))
        out = [WorkerRow(_str(r["Имя"]) or "(не указан)", _num(r["НЧ"]), _num(r["НЧФакт"]), _num(r["Работы"]),
                         _num(r["ЗЧ"]), int(_num(r["ЗН"]))) for r in rows]
        return sorted(out, key=lambda w: -w.works)

    def mechanics():
        a.mechanics = workers("Механик")

    def masters():
        a.masters = workers("МастерПриемщик")

    def acquiring():
        a.acquiring = acquiring_days(payments, transfers)
        a.commissions = commission_expenses(payments, transfers)

    def daily():
        start = dynamics_start(d1, d2)
        a.daily_from = start
        from .revenue import _dt  # noqa: PLC0415
        out: dict = defaultdict(float)
        for r in run(Q_DAILY, ("День", "Касса", "Подразделение", "Сумма"),
                     НачалоДинамики=datetime(start.year, start.month, start.day)):
            direction = direction_of(_str(r["Касса"]), _str(r["Подразделение"]))
            if direction:
                out[(_dt(r["День"]).date(), direction)] += _num(r["Сумма"])
        a.daily = dict(out)

    section("Прибыль", sales)
    section("Долги клиентов", debts)
    section("Механики", mechanics)
    section("Мастера-приёмщики", masters)
    section("Эквайринг и СБП", acquiring)
    section("Динамика", daily)
    return a


# ------------------------------------------------------------------ расчёты для листов
def period_sum(daily: dict, d1: date, d2: date, direction: str | None = None) -> float:
    return round(sum(v for (d, dr), v in daily.items()
                     if d1 <= d <= d2 and (direction is None or dr == direction)), 2)


def monthly(daily: dict, d2: date) -> list[tuple[date, dict]]:
    """12 последних месяцев (включая месяц d2): [(первое число, {направление: сумма})]."""
    months = []
    y, m = d2.year, d2.month
    for _ in range(12):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    out = []
    for first in months:
        totals = {SERVICE: 0.0, METAL: 0.0}
        for (d, dr), v in daily.items():
            if d.year == first.year and d.month == first.month and dr in totals:
                totals[dr] += v
        out.append((first, {k: round(v, 2) for k, v in totals.items()}))
    return out


def compare_periods(daily: dict, d1: date, d2: date) -> list[tuple[str, date, date]]:
    length = d2 - d1
    prev_end = d1 - timedelta(days=1)
    prev_start = prev_end - length
    try:
        ly1, ly2 = d1.replace(year=d1.year - 1), d2.replace(year=d2.year - 1)
    except ValueError:              # 29 февраля
        ly1, ly2 = d1 - timedelta(days=365), d2 - timedelta(days=365)
    return [("Этот период", d1, d2), ("Предыдущий такой же период", prev_start, prev_end),
            ("Тот же период год назад", ly1, ly2)]
