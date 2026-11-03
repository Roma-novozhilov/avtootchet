"""Сверка кассовой книги бухгалтера с данными 1С. Только код, без ИИ:
каждое расхождение можно проверить руками по номеру документа."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..common.models import (IN, OUT, BookEntry, CashBook, CashOp, OneCData, full_number,
                             same_money)

ERROR = "Ошибка"
WARNING = "Предупреждение"

# Порядок категорий в отчёте
CATEGORIES = [
    "Нет в кассовой книге",
    "Нет в 1С",
    "Сумма не совпадает",
    "Дата не совпадает",
    "Дубль в книге",
    "Остаток на начало дня не совпадает с 1С",
    "Неверный остаток на конец дня",
    "Неверный итог за день",
    "Номер документа отличается",
    "Запись вне периода",
    "Не удалось разобрать строку",
]


@dataclass
class Discrepancy:
    severity: str
    category: str
    date: date | None
    document: str
    amount_1c: float | None = None
    amount_book: float | None = None
    comment: str = ""

    @property
    def difference(self) -> float | None:
        if self.amount_1c is None or self.amount_book is None:
            return None
        return self.amount_book - self.amount_1c


@dataclass
class CheckResult:
    period: tuple[date, date]
    onec: OneCData
    book: CashBook
    items: list[Discrepancy] = field(default_factory=list)
    book_status: dict[int, str] = field(default_factory=dict)  # id(BookEntry) -> статус
    matched: int = 0
    summary: dict = field(default_factory=dict)

    @property
    def errors(self) -> int:
        return sum(1 for d in self.items if d.severity == ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for d in self.items if d.severity == WARNING)

    @property
    def is_clean(self) -> bool:
        return self.errors == 0


def _rub(x: float) -> str:
    return f"{x:+,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def _key_amount(x: float) -> int:
    return round(x * 100)


def reconcile(onec: OneCData, book: CashBook) -> CheckResult:
    d1, d2 = onec.period
    res = CheckResult(period=(d1, d2), onec=onec, book=book)

    def add(severity, category, day, document, a1=None, ab=None, comment=""):
        res.items.append(Discrepancy(severity, category, day, document, a1, ab, comment))

    for w in book.warnings:
        add(WARNING, "Не удалось разобрать строку", None, "", comment=w)

    # --- записи книги вне периода
    entries: list[BookEntry] = []
    for e in book.entries:
        if d1 <= e.date <= d2:
            entries.append(e)
        else:
            res.book_status[id(e)] = "Вне периода проверки"
    outside = len(book.entries) - len(entries)
    if outside:
        add(WARNING, "Запись вне периода", None, "",
            comment=f"{outside} записей книги вне выбранного периода - в сверке не участвуют. "
                    "Проверьте период.")

    # --- дубли в книге
    seen: dict[tuple, BookEntry] = {}
    unique: list[BookEntry] = []
    for e in entries:
        key = (e.kind, full_number(e.raw_number) or e.number)
        if e.number and key in seen:
            add(ERROR, "Дубль в книге", e.date, e.doc_name, None, e.amount,
                f"Документ записан повторно (лист «{e.sheet}», строка {e.row})")
            res.book_status[id(e)] = "Дубль"
        else:
            seen[key] = e
            unique.append(e)

    # --- 1. сопоставление по виду и номеру
    # 1а. по полному номеру (с префиксом кассы), 1б. по цифрам номера - только если
    # такие цифры у документа одного вида встречаются один раз (иначе можно спутать кассы)
    full_idx: dict[tuple, CashOp] = {}
    digits_idx: dict[tuple, list[CashOp]] = defaultdict(list)
    for o in onec.ops:
        if o.number and o.is_cash_order:
            full_idx.setdefault((o.kind, full_number(o.raw_number)), o)
            digits_idx[(o.kind, o.number)].append(o)
    used_ops: set[int] = set()
    rest_book: list[BookEntry] = []
    for e in unique:
        op = None
        if e.number:
            cand = full_idx.get((e.kind, full_number(e.raw_number)))
            if cand is not None and id(cand) not in used_ops:
                op = cand
            else:
                same = [o for o in digits_idx.get((e.kind, e.number), []) if id(o) not in used_ops]
                if len(same) == 1:
                    op = same[0]
        if op is None:
            rest_book.append(e)
            continue
        used_ops.add(id(op))
        problems = []
        if op.dt.date() != e.date:
            add(ERROR, "Дата не совпадает", e.date, op.doc_name, op.amount, e.amount,
                f"В 1С документ от {op.dt:%d.%m.%Y}, в книге - от {e.date:%d.%m.%Y}")
            problems.append("дата")
        if not same_money(op.amount, e.amount):
            add(ERROR, "Сумма не совпадает", op.dt.date(), op.doc_name, op.amount, e.amount,
                f"Контрагент в 1С: {op.counterparty or '—'}")
            problems.append("сумма")
        res.book_status[id(e)] = "OK" if not problems else "Ошибка: " + ", ".join(problems)
        res.matched += 1

    # --- 2. запасной вариант: по виду, дате и сумме (номер записан иначе)
    rest_ops = [o for o in onec.ops if id(o) not in used_ops]
    pool: dict[tuple, list[CashOp]] = defaultdict(list)
    for o in rest_ops:
        pool[(o.kind, o.dt.date(), _key_amount(o.amount))].append(o)
    still_book: list[BookEntry] = []
    for e in rest_book:
        cand = pool.get((e.kind, e.date, _key_amount(e.amount)))
        if cand:
            op = cand.pop(0)
            used_ops.add(id(op))
            add(WARNING, "Номер документа отличается", e.date, op.doc_name, op.amount, e.amount,
                f"В книге номер «{e.raw_number or 'не указан'}», совпали дата и сумма")
            res.book_status[id(e)] = f"OK (в 1С это {op.doc_name})"
            res.matched += 1
        else:
            still_book.append(e)

    # --- 3. что осталось без пары
    for op in onec.ops:
        if id(op) not in used_ops:
            add(ERROR, "Нет в кассовой книге", op.dt.date(), op.doc_name, op.amount, None,
                "; ".join(x for x in (op.cashbox, op.article, op.counterparty) if x))
    for e in still_book:
        add(ERROR, "Нет в 1С", e.date, e.doc_name, None, e.amount,
            f"{e.counterparty or 'контрагент не указан'} (лист «{e.sheet}», строка {e.row})")
        res.book_status[id(e)] = "Ошибка: нет в 1С"

    # --- 4. остатки и итоги по дням книги
    flow: dict[date, float] = defaultdict(float)
    for o in onec.ops:
        flow[o.dt.date()] += o.amount if o.kind == IN else -o.amount
    book_in: dict[date, float] = defaultdict(float)
    book_out: dict[date, float] = defaultdict(float)
    for e in unique:
        (book_in if e.kind == IN else book_out)[e.date] += e.amount

    def opening_1c(day: date) -> float:
        return onec.opening + sum(v for k, v in flow.items() if k < day)

    # Одна пропущенная запись сдвигает остатки всех следующих дней. Показываем остаток
    # только там, где расхождение появилось или изменилось, а не каждый день подряд.
    carried = 0.0
    for day in sorted(book.days):
        if not d1 <= day <= d2:
            continue
        bd = book.days[day]
        if bd.opening is not None:
            diff = bd.opening - opening_1c(day)
            if not same_money(diff, carried):
                if same_money(diff, 0):
                    comment = "Остаток снова совпадает с 1С"
                elif same_money(carried, 0):
                    comment = ("Расхождение появилось к этому дню; дальше остатки сдвинуты "
                               "на ту же сумму, пока не выровняются")
                else:
                    comment = f"Расхождение изменилось: было {_rub(carried)}, стало {_rub(diff)}"
                if not same_money(diff, 0):
                    add(ERROR, "Остаток на начало дня не совпадает с 1С", day,
                        "Остаток на начало дня", opening_1c(day), bd.opening, comment)
                carried = diff
        if bd.total_in is not None and not same_money(bd.total_in, book_in[day]):
            add(ERROR, "Неверный итог за день", day, "Итого за день: приход",
                book_in[day], bd.total_in, "Итог в книге не равен сумме её же строк")
        if bd.total_out is not None and not same_money(bd.total_out, book_out[day]):
            add(ERROR, "Неверный итог за день", day, "Итого за день: расход",
                book_out[day], bd.total_out, "Итог в книге не равен сумме её же строк")
        if bd.opening is not None and bd.closing is not None:
            expected = bd.opening + book_in[day] - book_out[day]
            if not same_money(bd.closing, expected):
                add(ERROR, "Неверный остаток на конец дня", day, "Остаток на конец дня",
                    expected, bd.closing,
                    "Остаток в книге ≠ остаток на начало + приход − расход по её же строкам")

    # --- сводка
    in_1c = sum(o.amount for o in onec.ops if o.kind == IN)
    out_1c = sum(o.amount for o in onec.ops if o.kind == OUT)
    in_b = sum(e.amount for e in unique if e.kind == IN)
    out_b = sum(e.amount for e in unique if e.kind == OUT)
    book_days = sorted(d for d in book.days if d1 <= d <= d2)
    first_open = next((book.days[d].opening for d in book_days if book.days[d].opening is not None), None)
    last_close = next((book.days[d].closing for d in reversed(book_days)
                       if book.days[d].closing is not None), None)
    res.summary = {
        "opening": (onec.opening, first_open),
        "in": (in_1c, in_b),
        "out": (out_1c, out_b),
        "closing": (onec.opening + in_1c - out_1c, last_close),
        "docs": (len(onec.ops), len(unique)),
    }
    order = {c: i for i, c in enumerate(CATEGORIES)}
    res.items.sort(key=lambda x: (x.severity != ERROR, order.get(x.category, 99),
                                  x.date or date.min))
    return res


def days_with_ops(onec: OneCData) -> list[date]:
    return sorted({o.dt.date() for o in onec.ops})


def daily_balances(onec: OneCData) -> dict[date, tuple[float, float, float, float]]:
    """{день: (остаток на начало, приход, расход, остаток на конец)} - только дни с операциями."""
    out: dict[date, tuple] = {}
    bal = onec.opening
    by_day: dict[date, list[CashOp]] = defaultdict(list)
    for o in onec.ops:
        by_day[o.dt.date()].append(o)
    day = onec.period[0]
    while day <= onec.period[1]:
        ops = by_day.get(day)
        if ops:
            i = sum(o.amount for o in ops if o.kind == IN)
            e = sum(o.amount for o in ops if o.kind == OUT)
            out[day] = (bal, i, e, bal + i - e)
            bal += i - e
        day += timedelta(days=1)
    return out
