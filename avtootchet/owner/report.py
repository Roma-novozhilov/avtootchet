"""Итоговый отчёт (Excel) для владельца. Только значения, без формул -
отчёт должен открываться заполненным в любом режиме Excel."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ..common.models import IN, CashOp
from .reconcile import CATEGORIES, ERROR, CheckResult, daily_balances

FONT = "Arial"
F = Font(name=FONT, size=10)
FB = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=14, bold=True, color="1F3864")
F_SUB = Font(name=FONT, size=10, italic=True, color="595959")
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_OK = Font(name=FONT, size=12, bold=True, color="006100")
F_BAD = Font(name=FONT, size=12, bold=True, color="9C0006")
FILL_HEAD = PatternFill("solid", fgColor="1F3864")
FILL_TOTAL = PatternFill("solid", fgColor="D9E1F2")
FILL_DAY = PatternFill("solid", fgColor="EDEDED")
FILL_ERR = PatternFill("solid", fgColor="FFC7CE")
FILL_WARN = PatternFill("solid", fgColor="FFEB9C")
FILL_OK = PatternFill("solid", fgColor="C6EFCE")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = '#,##0.00 "₽";-#,##0.00 "₽";"–"'
DATE = "DD.MM.YYYY"


def _setup(ws: Worksheet, title: str, sub: str, widths: list[int]):
    ws["A1"], ws["A2"] = title, sub
    ws["A1"].font, ws["A2"].font = F_TITLE, F_SUB
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _head(ws: Worksheet, row: int, headers: list[str]):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row, i, h)
        c.font, c.fill, c.border = F_HEAD, FILL_HEAD, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _row(ws: Worksheet, row: int, values: list, fmts: list | None = None,
         font=F, fill=None):
    for i, v in enumerate(values, start=1):
        c = ws.cell(row, i, v)
        c.font, c.border = font, BORDER
        if fill:
            c.fill = fill
        if fmts and i - 1 < len(fmts) and fmts[i - 1]:
            c.number_format = fmts[i - 1]


def build_report(res: CheckResult) -> bytes:
    d1, d2 = res.period
    period = f"Период: {d1:%d.%m.%Y} — {d2:%d.%m.%Y}"
    wb = Workbook()

    # ---------------------------------------------------------- итог
    ws = wb.active
    ws.title = "Итог проверки"
    _setup(ws, "Проверка кассовой книги", period, [44, 20, 22, 18])
    ws["A3"] = (f"Данные 1С: {res.onec.source_name}, получены {res.onec.loaded_at:%d.%m.%Y %H:%M} · "
                f"книга бухгалтера: «{res.book.filename}», получена {res.book.received_at:%d.%m.%Y %H:%M} · "
                f"отчёт сформирован {datetime.now():%d.%m.%Y %H:%M}")
    ws["A3"].font = F_SUB
    if res.is_clean:
        ws["A5"] = "✓ Кассовая книга совпадает с данными 1С"
        ws["A5"].font = F_OK
    else:
        ws["A5"] = f"✗ Найдено ошибок: {res.errors}, предупреждений: {res.warnings}"
        ws["A5"].font = F_BAD

    _head(ws, 7, ["Показатель", "По 1С", "По книге бухгалтера", "Разница"])
    labels = [("opening", "Остаток на начало периода"), ("in", "Приход"), ("out", "Расход"),
              ("closing", "Остаток на конец периода")]
    r = 8
    for key, label in labels:
        a, b = res.summary[key]
        diff = None if b is None else b - a
        fill = FILL_OK if diff is not None and abs(diff) < 0.005 else (FILL_ERR if diff is not None else None)
        _row(ws, r, [label, a, b if b is not None else "нет в книге", diff],
             [None, MONEY, MONEY, MONEY])
        if fill:
            ws.cell(r, 4).fill = fill
        r += 1
    a, b = res.summary["docs"]
    _row(ws, r, ["Количество документов", a, b, b - a], [None, "0", "0", "0;-0;–"])

    r += 2
    _head(ws, r, ["Вид расхождения", "Количество"])
    counts = Counter(d.category for d in res.items)
    r += 1
    if not counts:
        _row(ws, r, ["Расхождений нет", ""])
    for cat in CATEGORIES:
        if counts.get(cat):
            sev = next(d.severity for d in res.items if d.category == cat)
            _row(ws, r, [cat, counts[cat]], fill=FILL_ERR if sev == ERROR else FILL_WARN)
            r += 1

    # ---------------------------------------------------------- расхождения
    ws = wb.create_sheet("Расхождения")
    _setup(ws, "Расхождения", period, [15, 34, 12, 26, 16, 18, 15, 60])
    _head(ws, 4, ["Важность", "Вид", "Дата", "Документ", "По 1С", "По книге", "Разница", "Пояснение"])
    r = 5
    for d in res.items:
        _row(ws, r, [d.severity, d.category, d.date, d.document, d.amount_1c, d.amount_book,
                     d.difference, d.comment],
             [None, None, DATE, None, MONEY, MONEY, MONEY, None],
             fill=FILL_ERR if d.severity == ERROR else FILL_WARN)
        r += 1
    if not res.items:
        _row(ws, r, ["—", "Расхождений нет", None, "", None, None, None, ""])
    ws.freeze_panes = "A5"
    if res.items:
        ws.auto_filter.ref = f"A4:H{r - 1}"

    # ---------------------------------------------------------- правильная книга
    ws = wb.create_sheet("Кассовая книга (по 1С)")
    _setup(ws, "Кассовая книга по данным 1С", period, [14, 18, 34, 24, 24, 16, 16])
    _head(ws, 4, ["Дата", "Документ", "От кого получено / кому выдано", "Статья", "Касса",
                  "Приход", "Расход"])
    by_day: dict = defaultdict(list)
    for o in res.onec.ops:
        by_day[o.dt.date()].append(o)
    r = 5
    for day, (opening, i_sum, o_sum, closing) in daily_balances(res.onec).items():
        _row(ws, r, [f"КАССА за {day:%d.%m.%Y}", "", "", "", "", None, None], font=FB, fill=FILL_DAY)
        r += 1
        _row(ws, r, ["", "", "Остаток на начало дня", "", "", opening, None], [None] * 5 + [MONEY, MONEY],
             font=FB)
        r += 1
        op: CashOp
        for op in by_day[day]:
            _row(ws, r, [day, op.doc_name, op.counterparty, op.article, op.cashbox,
                         op.amount if op.kind == IN else None,
                         None if op.kind == IN else op.amount],
                 [DATE, None, None, None, None, MONEY, MONEY])
            r += 1
        _row(ws, r, ["", "", "Итого за день", "", "", i_sum, o_sum], [None] * 5 + [MONEY, MONEY],
             font=FB, fill=FILL_TOTAL)
        r += 1
        _row(ws, r, ["", "", "Остаток на конец дня", "", "", closing, None], [None] * 5 + [MONEY, MONEY],
             font=FB)
        r += 2
    if not res.onec.ops:
        _row(ws, r, ["", "", "Операций за период нет", "", "", None, None])
    ws.freeze_panes = "A5"

    # ---------------------------------------------------------- книга бухгалтера
    ws = wb.create_sheet("Книга бухгалтера")
    _setup(ws, f"Книга бухгалтера: «{res.book.filename}»", period, [14, 16, 36, 16, 16, 30, 22])
    _head(ws, 4, ["Дата", "Документ", "От кого / кому", "Приход", "Расход", "Результат проверки",
                  "Где в файле"])
    r = 5
    for e in res.book.entries:
        status = res.book_status.get(id(e), "")
        fill = FILL_OK if status.startswith("OK") else (FILL_ERR if status.startswith(("Ошибка", "Дубль"))
                                                         else FILL_WARN)
        _row(ws, r, [e.date, e.doc_name if e.number else (e.raw_number or "—"), e.counterparty,
                     e.amount if e.kind == IN else None, None if e.kind == IN else e.amount,
                     status, f"лист «{e.sheet}», стр. {e.row}"],
             [DATE, None, None, MONEY, MONEY, None, None])
        ws.cell(r, 6).fill = fill
        r += 1
    ws.freeze_panes = "A5"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def report_filename(res: CheckResult) -> str:
    d1, d2 = res.period
    return f"Кассовая книга {d1:%d.%m.%Y}-{d2:%d.%m.%Y} (проверено).xlsx"
