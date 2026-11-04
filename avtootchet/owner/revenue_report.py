"""Excel-отчёт по выручке. Только значения, без формул: открывается заполненным
в любом режиме Excel (в том числе в защищённом просмотре).

Листы:
1. «Выручка по дням» - как лист, который вёлся вручную: на каждый день блок Сервиса
   (Терминал / QR код / Наличные с чеком / без чека; Итого, Работы, Запчасти,
   Продажа запчастей, Продажа масла) и блок Металлообработки (Итого, Работы).
2. «Итоги» - суммы по дням, направлениям и способам оплаты.
   «Сверка» - по каждой кассе за каждый день: приход и расход по регистру денег Далиона
   против оплат отчёта, прочих поступлений, РКО и перемещений; расхождение = что-то не учтено.
3. «Оплаты подробно» - каждая оплата со всеми полями.
4. «Расходы по статьям», «Расходы по поставщикам», «Расходы подробно» - все документы
   «Расход денег» (кассы и расчётные счета): по статье затрат, а где статьи нет - по виду
   движения денежных средств; возвраты покупателям - отдельной строкой.
5. «Перемещения ДС», 6. «Остатки касс», 7. «Прочие поступления», 8. «Замечания».
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .revenue import (DIRECTIONS, METAL, METHODS, REFUND_GROUP, SERVICE, Expense, Payment,
                      RevenueData, supplier_matches)

FONT = "Arial"
F = Font(name=FONT, size=10)
FB = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=14, bold=True, color="1F3864")
F_DAY = Font(name=FONT, size=11, bold=True, italic=True)
F_SUB = Font(name=FONT, size=10, italic=True, color="595959")
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
FILL_HEAD = PatternFill("solid", fgColor="1F3864")
FILL_BLOCK = PatternFill("solid", fgColor="D9D9D9")
FILL_TOTAL = PatternFill("solid", fgColor="D9E1F2")
FILL_WARN = PatternFill("solid", fgColor="FFEB9C")
FILL_OK = PatternFill("solid", fgColor="C6EFCE")
FILL_ERR = PatternFill("solid", fgColor="FFC7CE")
F_OK = Font(name=FONT, size=12, bold=True, color="006100")
F_BAD = Font(name=FONT, size=12, bold=True, color="9C0006")
THIN = Side(style="thin", color="808080")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = '#,##0.00;-#,##0.00;"–"'
DATE = "DD.MM.YYYY"
WRAP = Alignment(wrap_text=True, vertical="center", horizontal="center")

SERVICE_COLS = ("Итого по заказ-наряду", "Работы", "Запчасти", "Продажа запчастей", "Продажа масла")
METAL_COLS = ("Итого по заказ-наряду", "Работы")


def _values(p: Payment, n: int) -> list[float]:
    vals = [p.amount, p.works, p.parts, p.parts_sale, p.oil_sale]
    return vals[:n]


def _cell(ws: Worksheet, r: int, c: int, v=None, *, font=F, fill=None, fmt=None, align=None, border=True):
    cell = ws.cell(r, c, v)
    cell.font = font
    if fill:
        cell.fill = fill
    if fmt:
        cell.number_format = fmt
    if align:
        cell.alignment = align
    if border:
        cell.border = BORDER
    return cell


def _days(data: RevenueData) -> list[date]:
    return sorted({p.dt.date() for p in data.revenue})


# ------------------------------------------------------------------ 1. выручка по дням
def _block(ws: Worksheet, row: int, day: date, direction: str, pays: list[Payment]) -> int:
    cols = SERVICE_COLS if direction == SERVICE else METAL_COLS
    width = len(cols)
    by_method = {m: [p for p in pays if p.method == m] for m in METHODS}
    total_cols = 1 + width * len(METHODS)
    _cell(ws, row, 1, f"{day:%d.%m.%Y} {direction}", font=F_DAY, border=False)
    row += 1
    _cell(ws, row, 1, "", fill=FILL_BLOCK)
    _cell(ws, row + 1, 1, "№", font=FB, fill=FILL_BLOCK, align=WRAP)
    for i, m in enumerate(METHODS):
        c0 = 2 + i * width
        ws.merge_cells(start_row=row, start_column=c0, end_row=row, end_column=c0 + width - 1)
        _cell(ws, row, c0, m.upper(), font=FB, fill=FILL_BLOCK, align=WRAP)
        for j in range(1, width):
            _cell(ws, row, c0 + j, None, fill=FILL_BLOCK)
        for j, name in enumerate(cols):
            _cell(ws, row + 1, c0 + j, name, font=FB, fill=FILL_BLOCK, align=WRAP)
    ws.row_dimensions[row + 1].height = 30
    row += 2
    n = max([len(v) for v in by_method.values()] + [1])
    sums = defaultdict(float)
    for k in range(n):
        _cell(ws, row + k, 1, k + 1, align=Alignment(horizontal="right"))
        for i, m in enumerate(METHODS):
            c0 = 2 + i * width
            items = by_method[m]
            vals = _values(items[k], width) if k < len(items) else [None] * width
            for j, v in enumerate(vals):
                _cell(ws, row + k, c0 + j, v if v else None, fmt=MONEY)
                if v:
                    sums[(m, j)] += v
    row += n
    # строка итогов по колонкам
    _cell(ws, row, 1, "Σ", font=FB, fill=FILL_TOTAL, align=Alignment(horizontal="center"))
    for i, m in enumerate(METHODS):
        c0 = 2 + i * width
        for j in range(width):
            _cell(ws, row, c0 + j, round(sums[(m, j)], 2), font=FB, fill=FILL_TOTAL, fmt=MONEY)
    row += 1
    # итог по каждому способу оплаты
    _cell(ws, row, 1, "", fill=FILL_TOTAL)
    day_total = 0.0
    for i, m in enumerate(METHODS):
        c0 = 2 + i * width
        t = round(sums[(m, 0)], 2)
        day_total += t
        ws.merge_cells(start_row=row, start_column=c0, end_row=row, end_column=c0 + width - 1)
        _cell(ws, row, c0, t, font=FB, fill=FILL_TOTAL, fmt=MONEY, align=Alignment(horizontal="center"))
        for j in range(1, width):
            _cell(ws, row, c0 + j, None, fill=FILL_TOTAL)
    row += 1
    _cell(ws, row, total_cols - 1, "Итого за день:", font=FB, border=False,
          align=Alignment(horizontal="right"))
    _cell(ws, row, total_cols, round(day_total, 2), font=FB, fmt=MONEY, border=False)
    return row + 2


def _sheet_days(wb: Workbook, data: RevenueData):
    ws = wb.active
    ws.title = "Выручка по дням"
    ws["A1"] = f"Выручка {data.d1:%d.%m.%Y}–{data.d2:%d.%m.%Y}"
    ws["A1"].font = F_TITLE
    ws["A2"] = (f"Построено из 1С {data.loaded_at:%d.%m.%Y %H:%M}. Работы и запчасти по заказ-наряду "
                "разделены пропорционально его составу; возвраты — минусом.")
    ws["A2"].font = F_SUB
    ws.column_dimensions["A"].width = 5
    for c in range(2, 2 + len(SERVICE_COLS) * len(METHODS)):
        ws.column_dimensions[get_column_letter(c)].width = 13
    row = 4
    days = _days(data)
    if not days:
        ws.cell(row, 1, "За период нет оплат.").font = FB
    for d in days:
        for direction in DIRECTIONS:
            pays = [p for p in data.revenue if p.dt.date() == d and p.direction == direction]
            if pays or direction == SERVICE:
                row = _block(ws, row, d, direction, pays)
    ws.freeze_panes = "B4"


# ------------------------------------------------------------------ 2. итоги
def _table(ws: Worksheet, row: int, headers: list[str], rows: list[list], money_from: int,
           widths: list[int] | None = None, total: bool = False) -> int:
    for i, h in enumerate(headers, start=1):
        _cell(ws, row, i, h, font=F_HEAD, fill=FILL_HEAD, align=WRAP)
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 30
    sums = defaultdict(float)
    for r in rows:
        row += 1
        for i, v in enumerate(r, start=1):
            is_money = i >= money_from and isinstance(v, (int, float)) and not isinstance(v, bool)
            fmt = MONEY if is_money else (DATE if isinstance(v, date) else None)
            _cell(ws, row, i, v, fmt=fmt)
            if is_money:
                sums[i] += v
    if total and rows:
        row += 1
        _cell(ws, row, 1, "Итого", font=FB, fill=FILL_TOTAL)
        for i in range(2, len(headers) + 1):
            _cell(ws, row, i, round(sums[i], 2) if i in sums else None, font=FB, fill=FILL_TOTAL,
                  fmt=MONEY)
    return row + 2


def _sheet_totals(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Итоги")
    ws["A1"] = f"Итоги {data.d1:%d.%m.%Y}–{data.d2:%d.%m.%Y}"
    ws["A1"].font = F_TITLE
    headers = ["Дата"]
    keys = []
    for d in DIRECTIONS:
        for m in METHODS:
            headers.append(f"{d}: {m}")
            keys.append((d, m))
        headers.append(f"{d}: итого")
        keys.append((d, None))
    headers.append("Всего")
    rows = []
    for day in _days(data):
        pays = [p for p in data.revenue if p.dt.date() == day]
        r = [day]
        for d, m in keys:
            r.append(round(sum(p.amount for p in pays if p.direction == d and (m is None or p.method == m)), 2))
        r.append(round(sum(p.amount for p in pays), 2))
        rows.append(r)
    row = _table(ws, 3, headers, rows, money_from=2, widths=[12] + [14] * (len(headers) - 1), total=True)

    ws.cell(row, 1, "По видам выручки за период").font = FB
    cat_rows = []
    for d in DIRECTIONS:
        pays = [p for p in data.revenue if p.direction == d]
        cat_rows.append([d, round(sum(p.works for p in pays), 2), round(sum(p.parts for p in pays), 2),
                         round(sum(p.parts_sale for p in pays), 2), round(sum(p.oil_sale for p in pays), 2),
                         round(sum(p.amount for p in pays), 2)])
    _table(ws, row + 1, ["Направление", "Работы", "Запчасти", "Продажа запчастей", "Продажа масла",
                         "Всего"], cat_rows, money_from=2, total=True)
    ws.freeze_panes = "B4"


# ------------------------------------------------------------------ сверка
RECON_HEADERS = ["Дата", "Касса", "Приход по регистру", "ПКО: выручка", "ПКО: прочие",
                 "Перемещения (вход)", "Другие документы (приход)", "Расход по регистру", "Возвраты",
                 "Прочие РКО", "Перемещения (выход)", "Другие документы (расход)", "Результат"]


def _sheet_recon(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Сверка")
    rows = data.reconciliation
    bad = [r for r in rows if not r.ok]
    ws["A1"] = "Сверка с регистром денег Далиона"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Приход кассы за день = ПКО (выручка + прочие) + входящие перемещения; "
                "расход = возвраты + прочие РКО + исходящие перемещения. «Другие документы» — то, "
                "что двигало кассу, но не вошло ни в одну графу (должно быть 0).")
    ws["A2"].font = F_SUB
    if bad:
        ws["A3"] = f"Не сходится: {len(bad)} из {len(rows)} (касса × день) — см. красные строки"
        ws["A3"].font = F_BAD
    else:
        ws["A3"] = f"Всё сходится: {len(rows)} из {len(rows)} (касса × день)"
        ws["A3"].font = F_OK
    table = [[r.day, r.cashbox, r.reg_in, r.pko_revenue, r.pko_other, r.transfers_in, r.other_in,
              r.reg_out, r.refunds, r.rko_other, r.transfers_out, r.other_out,
              "сходится" if r.ok else "РАСХОЖДЕНИЕ"] for r in rows]
    end = _table(ws, 5, RECON_HEADERS, table, money_from=3,
                 widths=[11, 24] + [14] * 10 + [15], total=True)
    for i, r in enumerate(rows):
        res = ws.cell(6 + i, len(RECON_HEADERS))
        res.fill = FILL_OK if r.ok else FILL_ERR
        for col, v in ((7, r.other_in), (12, r.other_out)):
            if abs(v) >= 0.01:
                ws.cell(6 + i, col).fill = FILL_ERR
    # итог по кассам за период
    ws.cell(end, 1, "По кассам за период").font = FB
    per = {}
    for r in rows:
        a = per.setdefault(r.cashbox, [0.0] * 10)
        for j, v in enumerate((r.reg_in, r.pko_revenue, r.pko_other, r.transfers_in, r.other_in,
                               r.reg_out, r.refunds, r.rko_other, r.transfers_out, r.other_out)):
            a[j] += v
    box_rows = [[box] + [round(v, 2) for v in vals] +
                ["сходится" if abs(vals[4]) < 0.01 and abs(vals[9]) < 0.01 else "РАСХОЖДЕНИЕ"]
                for box, vals in sorted(per.items())]
    _table(ws, end + 1, ["Касса"] + RECON_HEADERS[2:], box_rows, money_from=2, total=False)
    ws.freeze_panes = "C6"


# ------------------------------------------------------------------ расходы
def _dir(e: Expense) -> str:
    d = e.division.lower()
    return METAL if "металл" in d else (SERVICE if "сервис" in d else "не указано")


def _sheet_expense_articles(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Расходы по статьям")
    ws["A1"] = f"Расходы по статьям {data.d1:%d.%m.%Y}–{data.d2:%d.%m.%Y}"
    ws["A1"].font = F_TITLE
    ws["A2"] = ("Все документы «Расход денег» (кассы и расчётные счета). Группа — статья затрат; "
                "если статьи нет — вид движения денежных средств. Перемещения между кассами и "
                "счетами сюда не входят (лист «Перемещения ДС»).")
    ws["A2"].font = F_SUB
    groups: dict[str, dict] = {}
    for e in data.expenses:
        g = groups.setdefault(e.group, {"kind": e.group_kind, "n": 0, "Касса": 0.0, "Банк": 0.0,
                                        SERVICE: 0.0, METAL: 0.0, "не указано": 0.0})
        g["n"] += 1
        g["Банк" if e.source == "Банк" else "Касса"] += e.amount
        g[_dir(e)] += e.amount
    total = sum(e.amount for e in data.expenses) or 1.0
    rows = []
    for name, g in sorted(groups.items(), key=lambda kv: -(kv[1]["Касса"] + kv[1]["Банк"])):
        all_ = g["Касса"] + g["Банк"]
        rows.append([name, g["kind"], g["n"], round(g["Касса"], 2), round(g["Банк"], 2),
                     round(g[SERVICE], 2), round(g[METAL], 2), round(g["не указано"], 2), round(all_, 2),
                     f"{all_ / total:.1%}".replace(".", ",")])
    end = _table(ws, 4, ["Статья / вид движения", "Что это", "Документов", "Из касс", "С расч. счетов",
                         "Сервис", "Металлообработка", "Подразделение не указано", "Всего", "Доля"],
                 rows, money_from=4, widths=[40, 15, 11, 15, 15, 15, 15, 15, 15, 8], total=True)
    for r in range(5, 5 + len(rows)):
        if ws.cell(r, 1).value == REFUND_GROUP:
            for c in range(1, 11):
                ws.cell(r, c).fill = FILL_WARN
    ws.cell(end, 1, "Возврат покупателю — деньги, вернувшиеся клиентам по заказ-нарядам и накладным "
                    "(в выручке они уже стоят минусом).").font = F_SUB
    ws.freeze_panes = "B5"


def _sheet_expense_suppliers(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Расходы по поставщикам")
    ws["A1"] = f"Расходы по контрагентам {data.d1:%d.%m.%Y}–{data.d2:%d.%m.%Y}"
    ws["A1"].font = F_TITLE
    exp = [e for e in data.expenses if e.group != REFUND_GROUP]
    row = 3
    if data.main_suppliers:
        ws.cell(row, 1, "Основные поставщики").font = FB
        rows = []
        for sup in data.main_suppliers:
            items = [e for e in exp if supplier_matches(e.counterparty, sup)]
            names = sorted({e.counterparty for e in items})
            rows.append([sup, "; ".join(names) if names else "не найден за период", len(items),
                         round(sum(e.amount for e in items if e.source != "Банк"), 2),
                         round(sum(e.amount for e in items if e.source == "Банк"), 2),
                         round(sum(e.amount for e in items), 2)])
        row = _table(ws, row + 1, ["Поставщик", "Контрагенты в 1С", "Документов", "Из касс",
                                   "С расч. счетов", "Всего"], rows, money_from=4,
                     widths=[34, 44, 11, 15, 15, 15], total=True)
    ws.cell(row, 1, "Все контрагенты").font = FB
    per: dict[str, dict] = {}
    for e in exp:
        c = per.setdefault(e.counterparty or "(контрагент не указан)",
                           {"n": 0, "Касса": 0.0, "Банк": 0.0, "groups": defaultdict(float)})
        c["n"] += 1
        c["Банк" if e.source == "Банк" else "Касса"] += e.amount
        c["groups"][e.group] += e.amount
    total = sum(e.amount for e in exp) or 1.0
    rows = []
    for name, c in sorted(per.items(), key=lambda kv: -(kv[1]["Касса"] + kv[1]["Банк"])):
        all_ = c["Касса"] + c["Банк"]
        main_group = max(c["groups"].items(), key=lambda kv: kv[1])[0]
        rows.append([name, main_group, c["n"], round(c["Касса"], 2), round(c["Банк"], 2), round(all_, 2),
                     f"{all_ / total:.1%}".replace(".", ",")])
    _table(ws, row + 1, ["Контрагент", "Основная статья / вид движения", "Документов", "Из касс",
                         "С расч. счетов", "Всего", "Доля"], rows, money_from=4, total=True)
    ws.column_dimensions["G"].width = 8


EXP_HEADERS = ["Дата", "Время", "№", "Касса / счёт", "Источник", "Организация", "Подразделение",
               "Вид операции", "Контрагент", "Статья затрат", "Вид движения ДС", "Группа в отчёте",
               "Основание", "Сумма", "Комментарий", "Назначение платежа"]


def _sheet_expense_detail(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Расходы подробно")
    ws["A1"] = "Все документы «Расход денег» за период"
    ws["A1"].font = F_TITLE
    rows = [[e.dt.date(), e.dt.strftime("%H:%M"), e.number, e.account, e.source, e.organization,
             e.division, e.operation, e.counterparty, e.article, e.movement, e.group, e.basis, e.amount,
             e.comment, e.purpose] for e in data.expenses]
    end = _table(ws, 3, EXP_HEADERS, rows, money_from=14,
                 widths=[11, 7, 13, 24, 9, 20, 16, 18, 30, 24, 24, 28, 20, 14, 30, 40], total=True)
    ws.freeze_panes = "D4"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(EXP_HEADERS))}{max(3, end - 3)}"


# ------------------------------------------------------------------ 3-7
DETAIL_HEADERS = ["Дата", "Время", "Документ", "№", "Касса", "Организация", "Направление",
                  "Способ оплаты", "Подразделение", "Основание", "Тип ЗН", "Клиент", "Автомобиль",
                  "Госномер", "Мастер-приёмщик", "Итого", "Работы", "Запчасти", "Продажа запчастей",
                  "Продажа масла", "Замечание"]
DETAIL_WIDTHS = [11, 7, 9, 13, 20, 20, 15, 16, 16, 18, 13, 26, 22, 11, 20, 13, 13, 13, 13, 13, 40]


def _detail_row(p: Payment) -> list:
    basis = {"ЗН": "Заказ-наряд", "РН": "Расходная накладная"}.get(p.basis, "")
    return [p.dt.date(), p.dt.strftime("%H:%M"), p.doc, p.number, p.cashbox, p.organization,
            p.direction, p.method, p.division, f"{basis} {p.basis_number}".strip(), p.order_type,
            p.client, p.car, p.plate, p.master, p.amount, p.works, p.parts, p.parts_sale, p.oil_sale,
            "; ".join(p.notes)]


def _sheet_detail(wb: Workbook, name: str, title: str, pays: list[Payment], total: bool = True):
    ws = wb.create_sheet(name)
    ws["A1"] = title
    ws["A1"].font = F_TITLE
    end = _table(ws, 3, DETAIL_HEADERS, [_detail_row(p) for p in pays], money_from=16,
                 widths=DETAIL_WIDTHS, total=total)
    for r in range(4, end):
        if ws.cell(r, len(DETAIL_HEADERS)).value:
            ws.cell(r, len(DETAIL_HEADERS)).fill = FILL_WARN
    ws.freeze_panes = "E4"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(DETAIL_HEADERS))}{max(3, end - 3)}"


def _sheet_transfers(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Перемещения ДС")
    ws["A1"] = "Перемещения между кассами (Конвертация ДС) — не выручка"
    ws["A1"].font = F_TITLE
    rows = [[t.dt.date(), t.dt.strftime("%H:%M"), t.number, t.source, t.target, t.amount_out,
             t.amount_in, t.comment] for t in data.transfers]
    _table(ws, 3, ["Дата", "Время", "№", "Откуда", "Куда", "Списано", "Зачислено", "Комментарий"],
           rows, money_from=6, widths=[11, 7, 13, 24, 24, 14, 14, 40], total=True)


def _sheet_balances(wb: Workbook, data: RevenueData):
    ws = wb.create_sheet("Остатки касс")
    ws["A1"] = "Остатки касс по дням (дни с движением)"
    ws["A1"].font = F_TITLE
    rows = [[b.day, b.cashbox, b.opening, b.income, b.outcome, b.closing]
            for b in sorted(data.balances, key=lambda b: (b.day, b.cashbox))]
    _table(ws, 3, ["День", "Касса", "На начало", "Приход", "Расход", "На конец"], rows,
           money_from=3, widths=[11, 26, 15, 15, 15, 15])


def build_revenue_report(data: RevenueData) -> bytes:
    wb = Workbook()
    _sheet_days(wb, data)
    _sheet_totals(wb, data)
    _sheet_recon(wb, data)
    _sheet_detail(wb, "Оплаты подробно", "Все оплаты, вошедшие в выручку", data.revenue)
    _sheet_expense_articles(wb, data)
    _sheet_expense_suppliers(wb, data)
    _sheet_expense_detail(wb, data)
    _sheet_transfers(wb, data)
    _sheet_balances(wb, data)
    if data.other:
        _sheet_detail(wb, "Прочие поступления",
                      "Поступления без заказ-наряда и накладной (не выручка)", data.other)
    if data.notes:
        _sheet_detail(wb, "Замечания", "Оплаты, на которые стоит посмотреть", data.notes, total=False)
    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def revenue_filename(data: RevenueData) -> str:
    return f"Выручка {data.d1:%d.%m.%Y}-{data.d2:%d.%m.%Y}.xlsx"
