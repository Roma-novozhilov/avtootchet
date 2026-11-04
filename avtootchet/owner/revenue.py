"""Выручка из 1С:Далион: оплаты по кассам, разложенные так же, как лист выручки,
который раньше вёлся вручную.

* Направление (Сервис / Металлообработка) и способ оплаты (Терминал / QR / наличные
  с чеком / без чека) определяются по кассе. Для общей кассы «наличные с чеком»
  направление берётся из подразделения ПКО.
* Оплата по заказ-наряду делится на Работы и Запчасти пропорционально суммам работ
  (табличная часть «Услуги») и запчастей («Товары» + «Материалы») в заказ-наряде.
* Оплата по расходной накладной - это «Продажа запчастей» и «Продажа масла»:
  масло = номенклатура из групп, указанных в настройках (по умолчанию «Масло»).
* Возврат (РКО на основании заказ-наряда или накладной) идёт минусом.
* Приходы без основания («Приход в доход») и перемещения между кассами - не выручка,
  они показываются на отдельных листах.

Только чтение базы.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..common.comdate import com_datetime, inline_dates

SERVICE, METAL = "Сервис", "Металлообработка"
TERMINAL, QR, CASH, CASH_NC = "Терминал", "QR код", "Наличные (чек)", "Наличные (без чека)"
METHODS = (TERMINAL, QR, CASH, CASH_NC)
DIRECTIONS = (SERVICE, METAL)


@dataclass
class Payment:
    dt: datetime
    doc: str                 # ПКО / РКО
    number: str
    cashbox: str
    organization: str
    division: str
    operation: str
    amount: float            # возврат - со знаком минус
    client: str
    basis: str               # ЗН / РН / ""
    basis_number: str = ""
    car: str = ""
    plate: str = ""
    master: str = ""
    order_type: str = ""
    works: float = 0.0
    parts: float = 0.0
    parts_sale: float = 0.0
    oil_sale: float = 0.0
    direction: str = ""
    method: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_revenue(self) -> bool:
        return bool(self.basis and self.method and self.direction)


@dataclass
class Transfer:
    dt: datetime
    number: str
    source: str
    target: str
    amount_out: float
    amount_in: float
    comment: str


@dataclass
class Balance:
    day: date
    cashbox: str
    opening: float
    income: float
    outcome: float
    closing: float


@dataclass
class RevenueData:
    d1: date
    d2: date
    payments: list[Payment]
    transfers: list[Transfer]
    balances: list[Balance]
    rko_totals: dict = field(default_factory=dict)   # (день, касса) -> сумма всех РКО
    expenses: list["Expense"] = field(default_factory=list)
    main_suppliers: list[str] = field(default_factory=list)
    analytics: object = None          # analytics.Analytics
    loaded_at: datetime = field(default_factory=datetime.now)

    @property
    def revenue(self) -> list[Payment]:
        return [p for p in self.payments if p.is_revenue]

    @property
    def other(self) -> list[Payment]:
        return [p for p in self.payments if not p.is_revenue]

    @property
    def notes(self) -> list[Payment]:
        return [p for p in self.payments if p.notes]

    @property
    def reconciliation(self) -> list["ReconRow"]:
        return reconcile_cash(self)


# ------------------------------------------------------------------ расходы
REFUND_GROUP = "Возврат покупателю"


@dataclass
class Expense:
    """Документ «Расход денег» (касса или расчётный счёт)."""
    dt: datetime
    number: str
    account: str          # касса или расчётный счёт
    source: str           # «Касса» / «Банк»
    operation: str
    counterparty: str
    article: str          # статья затрат
    movement: str         # вид движения денежных средств
    division: str
    organization: str
    basis: str            # вид документа-основания
    amount: float
    comment: str = ""
    purpose: str = ""

    @property
    def group(self) -> str:
        """Статья; если её нет - вид движения ДС; возвраты покупателям - отдельно."""
        if self.basis in ("Заказ-наряд", "Расходная накладная"):
            return REFUND_GROUP
        if self.article:
            return self.article
        if self.movement:
            return self.movement
        return f"Без статьи ({self.operation or 'вид операции не указан'})"

    @property
    def group_kind(self) -> str:
        if self.group == REFUND_GROUP:
            return "возврат"
        return "статья" if self.article else ("вид движения ДС" if self.movement else "—")


def supplier_matches(counterparty: str, supplier: str) -> bool:
    """«Берг» найдёт и «БЕРГ ХОЛДИНГ ООО», и «ООО "Берг"»."""
    norm = lambda t: re.sub(r"[^0-9a-zа-яё]", "", t.lower().replace("ё", "е"))  # noqa: E731
    return bool(norm(supplier)) and norm(supplier) in norm(counterparty)


# ------------------------------------------------------------------ сверка с регистром
TOLERANCE = 0.01


@dataclass
class ReconRow:
    """Касса за день: приход и расход по регистру денег Далиона против того,
    из чего они должны складываться."""
    day: date
    cashbox: str
    reg_in: float = 0.0
    pko_revenue: float = 0.0
    pko_other: float = 0.0
    transfers_in: float = 0.0
    reg_out: float = 0.0
    refunds: float = 0.0
    rko_other: float = 0.0
    transfers_out: float = 0.0

    @property
    def other_in(self) -> float:
        return round(self.reg_in - self.pko_revenue - self.pko_other - self.transfers_in, 2)

    @property
    def other_out(self) -> float:
        return round(self.reg_out - self.refunds - self.rko_other - self.transfers_out, 2)

    @property
    def ok(self) -> bool:
        return abs(self.other_in) < TOLERANCE and abs(self.other_out) < TOLERANCE


def reconcile_cash(data: "RevenueData") -> list[ReconRow]:
    rows: dict[tuple[date, str], ReconRow] = {}

    def row(day: date, box: str) -> ReconRow:
        return rows.setdefault((day, box), ReconRow(day, box))

    for b in data.balances:
        r = row(b.day, b.cashbox)
        r.reg_in += b.income
        r.reg_out += b.outcome
    for p in data.payments:
        r = row(p.dt.date(), p.cashbox)
        if p.doc == "ПКО":
            if p.is_revenue:
                r.pko_revenue += p.amount
            else:
                r.pko_other += p.amount
        else:                       # РКО-возврат: в отчёте со знаком минус
            r.refunds += -p.amount
    for (day, box), total in data.rko_totals.items():
        row(day, box).rko_other += total
    for r in rows.values():         # возвраты входят в сумму всех РКО
        r.rko_other -= r.refunds
    known = {k[1] for k in rows}    # кассы; перемещения с расчётными счетами не учитываются
    for t in data.transfers:
        if t.target in known:
            row(t.dt.date(), t.target).transfers_in += t.amount_in
        if t.source in known:
            row(t.dt.date(), t.source).transfers_out += t.amount_out
    for r in rows.values():
        for f in ("reg_in", "pko_revenue", "pko_other", "transfers_in", "reg_out", "refunds",
                  "rko_other", "transfers_out"):
            setattr(r, f, round(getattr(r, f), 2))
    return sorted(rows.values(), key=lambda r: (r.day, r.cashbox))


# ------------------------------------------------------------------ классификация касс
def classify_cashbox(name: str) -> tuple[str, str]:
    """Касса -> (направление, способ оплаты). ("", "") - касса не для выручки (Вывод ДС)."""
    n = name.lower()
    if "вывод" in n:
        return "", ""
    direction = METAL if re.search(r"\(м\)", n) else SERVICE
    if "эквайр" in n or "терминал" in n:
        method = TERMINAL
    elif "qr" in n or "сбп" in n or "куар" in n:
        method = QR
    elif "бч" in n or "без чека" in n:
        method = CASH_NC
    elif "нал" in n:
        method = CASH
    else:
        method = ""
    return direction, method


def _division_direction(division: str) -> str:
    d = division.lower()
    if "металл" in d:
        return METAL
    if "сервис" in d:
        return SERVICE
    return ""


def _split(amount: float, parts: list[float]) -> list[float]:
    """Разделить сумму пропорционально частям, до копейки; остаток копеек - в последнюю."""
    total = sum(parts)
    if total <= 0:
        return [amount] + [0.0] * (len(parts) - 1)
    out, left = [], amount
    for i, p in enumerate(parts):
        if i == len(parts) - 1:
            out.append(round(left, 2))
        else:
            v = round(amount * p / total, 2)
            out.append(v)
            left -= v
    return out


def classify(p: Payment, sums: dict) -> Payment:
    """Направление, способ оплаты и разбивка по одной оплате.
    sums: Услуги, Товары, Материалы (заказ-наряд), Масло, ПрочееРН, УслугиРН (накладная)."""
    p.direction, p.method = classify_cashbox(p.cashbox)
    div_dir = _division_direction(p.division)
    if p.method == CASH and div_dir:
        p.direction = div_dir          # общая касса «наличные с чеком»: решает подразделение
    elif div_dir and p.direction and div_dir != p.direction:
        p.notes.append(f"подразделение «{p.division}», а касса — {p.direction}")
    if not p.method:
        if p.basis:
            p.notes.append("касса не относится к выручке")
        return p
    if p.basis == "ЗН":
        works, goods = sums.get("Услуги", 0.0), sums.get("Товары", 0.0) + sums.get("Материалы", 0.0)
        if works + goods <= 0:
            p.notes.append("в заказ-наряде нет работ и запчастей — вся сумма в «Работы»")
        p.works, p.parts = _split(p.amount, [works, goods])
        if p.direction == METAL and p.parts:
            p.notes.append("в заказ-наряде Металлообработки есть запчасти")
    elif p.basis == "РН":
        oil = sums.get("Масло", 0.0)
        other = sums.get("ПрочееРН", 0.0) + sums.get("УслугиРН", 0.0)
        p.parts_sale, p.oil_sale = _split(p.amount, [other, oil])
        if oil + other <= 0:
            p.notes.append("в накладной нет товаров — вся сумма в «Продажа запчастей»")
    return p


# ------------------------------------------------------------------ запросы 1С
Q_PAYMENTS = """ВЫБРАТЬ
	1 КАК Знак,
	"ПКО" КАК Документ,
	П.Дата КАК Дата,
	П.Номер КАК Номер,
	П.СчетДенежныхСредств КАК Касса,
	П.Подразделение КАК Подразделение,
	П.ВидОперации КАК ВидОперации,
	П.СуммаДокумента КАК Сумма,
	П.Контрагент КАК Контрагент,
	П.ДокументОснование КАК Основание
ПОМЕСТИТЬ ВТ_П
ИЗ
	Документ.ПриходДенег КАК П
ГДЕ
	П.Проведен
	И П.Дата МЕЖДУ &Начало И &Конец
	И П.СчетДенежныхСредств ССЫЛКА Справочник.Кассы

ОБЪЕДИНИТЬ ВСЕ

ВЫБРАТЬ
	-1,
	"РКО",
	Р.Дата,
	Р.Номер,
	Р.СчетДенежныхСредств,
	Р.Подразделение,
	Р.ВидОперации,
	Р.СуммаДокумента,
	Р.Контрагент,
	Р.ДокументОснование
ИЗ
	Документ.РасходДенег КАК Р
ГДЕ
	Р.Проведен
	И Р.Дата МЕЖДУ &Начало И &Конец
	И Р.СчетДенежныхСредств ССЫЛКА Справочник.Кассы
	И (ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
		ИЛИ ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.РасходнаяНакладная))
;
ВЫБРАТЬ
	П.Знак КАК Знак,
	П.Документ КАК Документ,
	П.Дата КАК Дата,
	П.Номер КАК Номер,
	ПРЕДСТАВЛЕНИЕ(П.Касса) КАК Касса,
	ЕСТЬNULL(ВЫРАЗИТЬ(П.Касса КАК Справочник.Кассы).Организация.Наименование, "") КАК Организация,
	ПРЕДСТАВЛЕНИЕ(П.Подразделение) КАК Подразделение,
	ПРЕДСТАВЛЕНИЕ(П.ВидОперации) КАК ВидОперации,
	П.Сумма КАК Сумма,
	ПРЕДСТАВЛЕНИЕ(П.Контрагент) КАК Клиент,
	ВЫБОР
		КОГДА ТИПЗНАЧЕНИЯ(П.Основание) = ТИП(Документ.ЗаказНаряд) ТОГДА "ЗН"
		КОГДА ТИПЗНАЧЕНИЯ(П.Основание) = ТИП(Документ.РасходнаяНакладная) ТОГДА "РН"
		ИНАЧЕ ""
	КОНЕЦ КАК ВидОснования,
	ЕСТЬNULL(ЗН.Номер, ЕСТЬNULL(РН.Номер, "")) КАК НомерОснования,
	ПРЕДСТАВЛЕНИЕ(ЗН.Автомобиль) КАК Автомобиль,
	ПРЕДСТАВЛЕНИЕ(ЗН.ГосНомер) КАК ГосНомер,
	ПРЕДСТАВЛЕНИЕ(ЗН.МастерПриемщик) КАК Мастер,
	ПРЕДСТАВЛЕНИЕ(ЗН.ТипЗН) КАК ТипЗН,
	ЕСТЬNULL(У.С, 0) КАК Услуги,
	ЕСТЬNULL(Т.С, 0) КАК Товары,
	ЕСТЬNULL(М.С, 0) КАК Материалы,
	ЕСТЬNULL(РМ.С, 0) КАК Масло,
	ЕСТЬNULL(РТ.С, 0) - ЕСТЬNULL(РМ.С, 0) КАК ПрочееРН,
	ЕСТЬNULL(РУ.С, 0) КАК УслугиРН
ИЗ
	ВТ_П КАК П
		ЛЕВОЕ СОЕДИНЕНИЕ Документ.ЗаказНаряд КАК ЗН
		ПО (ЗН.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ Документ.РасходнаяНакладная КАК РН
		ПО (РН.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.ЗаказНаряд.Услуги КАК Х
			ГДЕ Х.Ссылка В (ВЫБРАТЬ ВВ.Основание ИЗ ВТ_П КАК ВВ)
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК У
		ПО (У.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.ЗаказНаряд.Товары КАК Х
			ГДЕ Х.Ссылка В (ВЫБРАТЬ ВВ.Основание ИЗ ВТ_П КАК ВВ)
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК Т
		ПО (Т.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.ЗаказНаряд.Материалы КАК Х
			ГДЕ Х.Ссылка В (ВЫБРАТЬ ВВ.Основание ИЗ ВТ_П КАК ВВ)
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК М
		ПО (М.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.РасходнаяНакладная.Товары КАК Х
			ГДЕ Х.Ссылка В (ВЫБРАТЬ ВВ.Основание ИЗ ВТ_П КАК ВВ)
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК РТ
		ПО (РТ.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.РасходнаяНакладная.Товары КАК Х
			ГДЕ Х.Ссылка В (ВЫБРАТЬ ВВ.Основание ИЗ ВТ_П КАК ВВ)
				И Х.Номенклатура В ИЕРАРХИИ (&ГруппыМасла)
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК РМ
		ПО (РМ.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.РасходнаяНакладная.Услуги КАК Х
			ГДЕ Х.Ссылка В (ВЫБРАТЬ ВВ.Основание ИЗ ВТ_П КАК ВВ)
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК РУ
		ПО (РУ.Ссылка = П.Основание)
УПОРЯДОЧИТЬ ПО
	П.Дата"""

Q_OIL_GROUPS = """ВЫБРАТЬ
	Г.Ссылка КАК Ссылка
ИЗ
	Справочник.Номенклатура КАК Г
ГДЕ
	Г.ЭтоГруппа
	И Г.Наименование В (&Имена)"""

Q_TRANSFERS = """ВЫБРАТЬ
	К.Дата КАК Дата,
	К.Номер КАК Номер,
	ПРЕДСТАВЛЕНИЕ(К.ИсходныйСчетДенежныхСредств) КАК Откуда,
	ПРЕДСТАВЛЕНИЕ(К.КонечныйСчетДенежныхСредств) КАК Куда,
	К.ИсходнаяСумма КАК Списано,
	К.КонечнаяСумма КАК Зачислено,
	ВЫРАЗИТЬ(К.Комментарий КАК СТРОКА(200)) КАК Комментарий
ИЗ
	Документ.КонвертацияДС КАК К
ГДЕ
	К.Проведен
	И К.Дата МЕЖДУ &Начало И &Конец
УПОРЯДОЧИТЬ ПО
	К.Дата"""

Q_RKO_ALL = """ВЫБРАТЬ
	НАЧАЛОПЕРИОДА(Р.Дата, ДЕНЬ) КАК День,
	ПРЕДСТАВЛЕНИЕ(Р.СчетДенежныхСредств) КАК Касса,
	СУММА(Р.СуммаДокумента) КАК Сумма
ИЗ
	Документ.РасходДенег КАК Р
ГДЕ
	Р.Проведен
	И Р.Дата МЕЖДУ &Начало И &Конец
	И Р.СчетДенежныхСредств ССЫЛКА Справочник.Кассы
СГРУППИРОВАТЬ ПО
	НАЧАЛОПЕРИОДА(Р.Дата, ДЕНЬ),
	Р.СчетДенежныхСредств"""

Q_EXPENSES = """ВЫБРАТЬ
	Р.Дата КАК Дата,
	Р.Номер КАК Номер,
	ПРЕДСТАВЛЕНИЕ(Р.СчетДенежныхСредств) КАК Счет,
	ВЫБОР КОГДА Р.СчетДенежныхСредств ССЫЛКА Справочник.Кассы ТОГДА "Касса" ИНАЧЕ "Банк" КОНЕЦ КАК Источник,
	ПРЕДСТАВЛЕНИЕ(Р.ВидОперации) КАК ВидОперации,
	ПРЕДСТАВЛЕНИЕ(Р.Контрагент) КАК Контрагент,
	ПРЕДСТАВЛЕНИЕ(Р.Статья) КАК Статья,
	ПРЕДСТАВЛЕНИЕ(Р.ВидДвиженияДенежныхСредств) КАК ВидДвижения,
	ПРЕДСТАВЛЕНИЕ(Р.Подразделение) КАК Подразделение,
	ПРЕДСТАВЛЕНИЕ(Р.Организация) КАК Организация,
	ВЫБОР
		КОГДА ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗаказНаряд) ТОГДА "Заказ-наряд"
		КОГДА ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.РасходнаяНакладная) ТОГДА "Расходная накладная"
		КОГДА ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ПриходнаяНакладная) ТОГДА "Приходная накладная"
		КОГДА ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗатратыНаПокупкуТоваров) ТОГДА "Затраты на покупку товаров"
		КОГДА ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ПриходДенег) ТОГДА "Приход денег"
		ИНАЧЕ ""
	КОНЕЦ КАК Основание,
	Р.СуммаДокумента КАК Сумма,
	ВЫРАЗИТЬ(Р.Комментарий КАК СТРОКА(200)) КАК Комментарий,
	ВЫРАЗИТЬ(Р.НазначениеПлатежа КАК СТРОКА(200)) КАК Назначение
ИЗ
	Документ.РасходДенег КАК Р
ГДЕ
	Р.Проведен
	И Р.Дата МЕЖДУ &Начало И &Конец
УПОРЯДОЧИТЬ ПО
	Р.Дата"""

Q_BALANCES = """ВЫБРАТЬ
	О.Период КАК День,
	ПРЕДСТАВЛЕНИЕ(О.СчетДенежныхСредств) КАК Касса,
	О.СуммаНачальныйОстаток КАК Нач,
	О.СуммаПриход КАК Приход,
	О.СуммаРасход КАК Расход,
	О.СуммаКонечныйОстаток КАК Кон
ИЗ
	РегистрНакопления.ДенежныеСредства.ОстаткиИОбороты(&Начало, &Конец, День, , СчетДенежныхСредств ССЫЛКА Справочник.Кассы) КАК О
УПОРЯДОЧИТЬ ПО
	О.Период"""


def _dt(v) -> datetime:
    return com_datetime(v)


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def load_revenue(conn, d1: date, d2: date, oil_groups: list[str] | None = None,
                 main_suppliers: list[str] | None = None) -> RevenueData:
    """conn - подключение COM (V83.COMConnector.Connect)."""
    start = datetime(d1.year, d1.month, d1.day)
    end = datetime(d2.year, d2.month, d2.day, 23, 59, 59)

    def run(text: str, cols: tuple[str, ...], **extra):
        q = conn.NewObject("Запрос")
        q.Text, rest = inline_dates(text, {"Начало": start, "Конец": end, **extra})
        for k, v in rest.items():
            q.SetParameter(k, v)
        sel = q.Execute().Choose()
        rows = []
        while sel.Next():
            rows.append({c: getattr(sel, c) for c in cols})
        return rows

    # группы масла: ссылки на группы номенклатуры с этими названиями
    names = conn.NewObject("Массив")
    for g in (oil_groups or ["Масло"]):
        names.Add(g)
    groups = conn.NewObject("Массив")
    for r in run(Q_OIL_GROUPS, ("Ссылка",), Имена=names):
        groups.Add(r["Ссылка"])

    pay_cols = ("Знак", "Документ", "Дата", "Номер", "Касса", "Организация", "Подразделение",
                "ВидОперации", "Сумма", "Клиент", "ВидОснования", "НомерОснования", "Автомобиль",
                "ГосНомер", "Мастер", "ТипЗН", "Услуги", "Товары", "Материалы", "Масло",
                "ПрочееРН", "УслугиРН")
    payments = []
    for r in run(Q_PAYMENTS, pay_cols, ГруппыМасла=groups):
        sign = int(_num(r["Знак"])) or 1
        p = Payment(
            dt=_dt(r["Дата"]), doc=_str(r["Документ"]), number=_str(r["Номер"]),
            cashbox=_str(r["Касса"]), organization=_str(r["Организация"]),
            division=_str(r["Подразделение"]), operation=_str(r["ВидОперации"]),
            amount=sign * _num(r["Сумма"]), client=_str(r["Клиент"]),
            basis=_str(r["ВидОснования"]), basis_number=_str(r["НомерОснования"]),
            car=_str(r["Автомобиль"]), plate=_str(r["ГосНомер"]), master=_str(r["Мастер"]),
            order_type=_str(r["ТипЗН"]))
        sums = {k: _num(r[k]) for k in ("Услуги", "Товары", "Материалы", "Масло", "ПрочееРН", "УслугиРН")}
        payments.append(classify(p, sums))

    transfers = [Transfer(_dt(r["Дата"]), _str(r["Номер"]), _str(r["Откуда"]), _str(r["Куда"]),
                          _num(r["Списано"]), _num(r["Зачислено"]), _str(r["Комментарий"]))
                 for r in run(Q_TRANSFERS, ("Дата", "Номер", "Откуда", "Куда", "Списано", "Зачислено",
                                            "Комментарий"))]
    balances = [Balance(_dt(r["День"]).date(), _str(r["Касса"]), _num(r["Нач"]), _num(r["Приход"]),
                        _num(r["Расход"]), _num(r["Кон"]))
                for r in run(Q_BALANCES, ("День", "Касса", "Нач", "Приход", "Расход", "Кон"))]
    rko: dict = {}
    for r in run(Q_RKO_ALL, ("День", "Касса", "Сумма")):
        key = (_dt(r["День"]).date(), _str(r["Касса"]))
        rko[key] = rko.get(key, 0.0) + _num(r["Сумма"])
    exp_cols = ("Дата", "Номер", "Счет", "Источник", "ВидОперации", "Контрагент", "Статья",
                "ВидДвижения", "Подразделение", "Организация", "Основание", "Сумма", "Комментарий",
                "Назначение")
    expenses = [Expense(_dt(r["Дата"]), _str(r["Номер"]), _str(r["Счет"]), _str(r["Источник"]),
                        _str(r["ВидОперации"]), _str(r["Контрагент"]), _str(r["Статья"]),
                        _str(r["ВидДвижения"]), _str(r["Подразделение"]), _str(r["Организация"]),
                        _str(r["Основание"]), _num(r["Сумма"]), _str(r["Комментарий"]),
                        _str(r["Назначение"]))
                for r in run(Q_EXPENSES, exp_cols)]
    from .analytics import load_analytics  # noqa: PLC0415
    analytics = load_analytics(conn, d1, d2, payments, transfers, run)
    expenses += analytics.commissions          # комиссия банка за эквайринг/СБП - тоже расход
    expenses.sort(key=lambda e: e.dt)
    return RevenueData(d1, d2, payments, transfers, balances, rko, expenses, list(main_suppliers or []),
                       analytics)
