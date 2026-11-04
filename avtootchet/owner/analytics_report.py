"""Листы аналитики в отчёте по выручке: Прибыль, Динамика, Эквайринг и СБП,
Долги клиентов, Механики. Только значения и диаграммы Excel, без формул."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from .analytics import Analytics, compare_periods, monthly, period_sum
from .revenue import METAL, REFUND_GROUP, SERVICE, RevenueData
from .revenue_report import F_DAY, F_SUB, F_TITLE, FB, FILL_ERR, FONT, _table

F_ERR = Font(name=FONT, size=10, bold=True, color="9C0006")
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def _pct(a: float, b: float) -> str:
    return f"{a / b:.1%}".replace(".", ",") if b else "—"


def _title(ws: Worksheet, title: str, sub: str) -> None:
    ws["A1"], ws["A2"] = title, sub
    ws["A1"].font, ws["A2"].font = F_TITLE, F_SUB


def _error(ws: Worksheet, row: int, a: Analytics, section: str) -> int:
    """Если раздел не посчитался - написать почему. Возвращает следующую строку."""
    if section not in a.errors:
        return row
    c = ws.cell(row, 1, f"Раздел «{section}» не построен: 1С вернула ошибку. Пришлите этот текст "
                       f"разработчику: {a.errors[section]}")
    c.font = F_ERR
    c.fill = FILL_ERR
    return row + 2


# ------------------------------------------------------------------ прибыль
def sheet_profit(wb: Workbook, data: RevenueData) -> None:
    a: Analytics = data.analytics
    ws = wb.create_sheet("Прибыль")
    _title(ws, f"Прибыль {data.d1:%d.%m.%Y}–{data.d2:%d.%m.%Y}",
           "Блок 1 — по продажам (регистр «Продажи» Далиона: что продано за период и по какой "
           "себестоимости). Блок 2 — деньги: сколько пришло и сколько потрачено за период.")
    row = _error(ws, 4, a, "Прибыль")
    if "Прибыль" not in a.errors:
        ws.cell(row, 1, "1. Прибыль по продажам").font = F_DAY
        rows = []
        order = {SERVICE: 0, METAL: 1}
        for s in sorted(a.sales, key=lambda s: (order.get(s.direction, 2), s.kind)):
            margin = _pct(s.sales - s.cost, s.cost) if s.cost else "—"
            rows.append([s.direction, s.kind or "—", round(s.sales, 2), round(s.cost, 2), round(s.salary, 2),
                         round(s.subcontract, 2), round(s.extra + s.agents, 2), s.gross, margin,
                         _pct(s.gross, s.sales)])
        for d in (SERVICE, METAL):
            part = [s for s in a.sales if s.direction == d]
            if part:
                sales, cost = sum(s.sales for s in part), sum(s.cost for s in part)
                gross = sum(s.gross for s in part)
                rows.append([f"{d}: итого", "", round(sales, 2), round(cost, 2),
                             round(sum(s.salary for s in part), 2), round(sum(s.subcontract for s in part), 2),
                             round(sum(s.extra + s.agents for s in part), 2), round(gross, 2), "",
                             _pct(gross, sales)])
        row = _table(ws, row + 1, ["Направление", "Работы / товары", "Продажа", "Себестоимость",
                                   "Зарплата механиков", "Сторонние работы", "Доп. затраты и посредники",
                                   "Валовая прибыль", "Наценка на себестоимость", "Рентабельность"],
                     [r for r in rows if not str(r[0]).endswith("итого")], money_from=3,
                     widths=[24, 18, 15, 15, 15, 15, 15, 15, 15, 14], total=True)
        sub = [r for r in rows if str(r[0]).endswith("итого")]
        if sub:
            ws.cell(row - 1, 1, "По направлениям").font = FB
            row = _table(ws, row, ["Направление", "", "Продажа", "Себестоимость", "Зарплата механиков",
                                   "Сторонние работы", "Доп. затраты и посредники", "Валовая прибыль", "",
                                   "Рентабельность"], sub, money_from=3)
        ws.cell(row - 1, 1, "Наценка — (продажа − себестоимость) / себестоимость: для запчастей и "
                            "материалов. Рентабельность — валовая прибыль / продажа.").font = F_SUB

    # деньги за период
    row += 1
    ws.cell(row, 1, "2. Деньги за период (касса и расчётные счета)").font = F_DAY
    orgs = sorted({p.organization for p in data.revenue} | {e.organization for e in data.expenses},
                  key=lambda n: ("бч" in n.lower(), n == "", n))
    cols = orgs + (["Всего"] if len(orgs) > 1 else [])

    def by_org(values) -> list[float]:
        acc = defaultdict(float)
        for org, v in values:
            acc[org] += v
        out = [round(acc[o], 2) for o in orgs]
        return out + ([round(sum(out), 2)] if len(orgs) > 1 else [])

    income = by_org((p.organization, p.amount) for p in data.revenue)
    spent = [e for e in data.expenses if e.group != REFUND_GROUP]
    outgo = by_org((e.organization, e.amount) for e in spent)
    supplier = by_org((e.organization, e.amount) for e in spent if e.basis in ("Приходная накладная",
                                                                               "Затраты на покупку товаров"))
    rows = [["Выручка (поступило от клиентов, за вычетом возвратов)"] + income,
            ["Расходы всего (Расход денег + комиссия банка)"] + outgo,
            ["   в т.ч. оплата поставщикам за товар"] + supplier,
            ["Остаток: выручка − расходы"] + [round(i - o, 2) for i, o in zip(income, outgo)]]
    _table(ws, row + 1, ["Показатель"] + [o or "Организация не указана" for o in cols], rows, money_from=2,
           widths=[52] + [18] * len(cols))
    ws.cell(row + 7, 1, "Это движение денег: оплата поставщику за запчасти, которые ещё лежат на складе, "
                        "уже расход, а себестоимость проданных запчастей — в блоке 1.").font = F_SUB


# ------------------------------------------------------------------ динамика
def sheet_dynamics(wb: Workbook, data: RevenueData) -> None:
    a: Analytics = data.analytics
    ws = wb.create_sheet("Динамика")
    _title(ws, "Динамика выручки", "Выручка = оплаты клиентов по заказ-нарядам и накладным в кассы, "
                                   "за вычетом возвратов.")
    row = _error(ws, 4, a, "Динамика")
    if "Динамика" in a.errors:
        return
    ws.cell(row, 1, "Сравнение периодов").font = F_DAY
    cmp_rows = []
    base = None
    for name, p1, p2 in compare_periods(a.daily, data.d1, data.d2):
        s, m = period_sum(a.daily, p1, p2, SERVICE), period_sum(a.daily, p1, p2, METAL)
        total = round(s + m, 2)
        if base is None:
            base = total
            change = ""
        else:
            change = _pct(base - total, total) if total else "—"
        cmp_rows.append([name, p1, p2, s, m, total, change])
    first = row + 2
    row = _table(ws, row + 1, ["Период", "С", "По", "Сервис", "Металлообработка", "Всего",
                               "Этот период к нему"], cmp_rows, money_from=4,
                 widths=[30, 12, 12, 16, 16, 16, 14])
    chart = BarChart()
    chart.title = "Выручка: сравнение периодов"
    chart.y_axis.title = "₽"
    chart.add_data(Reference(ws, min_col=4, max_col=5, min_row=first - 1, max_row=first + len(cmp_rows) - 1),
                   titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=first + len(cmp_rows) - 1))
    chart.grouping, chart.overlap = "stacked", 100
    chart.height, chart.width = 7.5, 18
    ws.add_chart(chart, "I4")

    ws.cell(row, 1, "По месяцам за год").font = F_DAY
    months = monthly(a.daily, data.d2)
    m_rows = [[f"{MONTHS[d.month - 1]} {d.year}", v[SERVICE], v[METAL], round(v[SERVICE] + v[METAL], 2)]
              for d, v in months]
    first = row + 2
    end = _table(ws, row + 1, ["Месяц", "Сервис", "Металлообработка", "Всего"], m_rows, money_from=2,
                 total=True)
    line = LineChart()
    line.title = "Выручка по месяцам"
    line.y_axis.title = "₽"
    line.add_data(Reference(ws, min_col=2, max_col=4, min_row=first - 1, max_row=first + len(m_rows) - 1),
                  titles_from_data=True)
    line.set_categories(Reference(ws, min_col=1, min_row=first, max_row=first + len(m_rows) - 1))
    for series in line.series:
        series.smooth = False
    line.height, line.width = 9, 18
    ws.add_chart(line, "I21")
    ws.cell(end, 1, "Последний месяц может быть неполным (по дату окончания периода).").font = F_SUB


# ------------------------------------------------------------------ эквайринг
def sheet_acquiring(wb: Workbook, data: RevenueData) -> None:
    a: Analytics = data.analytics
    ws = wb.create_sheet("Эквайринг и СБП")
    _title(ws, "Терминал и QR: сколько прошло → сколько пришло на счёт",
           "Оплаты клиентов в кассы терминала и QR против перемещений этих денег на расчётный счёт. "
           "Комиссия банка = списано из кассы − зачислено на счёт. Банк перечисляет с задержкой, поэтому "
           "оплаты и перечисление часто стоят в разные дни — сравнивайте итоги по кассе за период.")
    row = _error(ws, 4, a, "Эквайринг и СБП")
    rows = [[r.day, r.cashbox, r.method, r.payments, r.sent, r.credited, r.commission,
             _pct(r.commission, r.sent) if r.sent else ""] for r in a.acquiring]
    row = _table(ws, row, ["Дата", "Касса", "Способ", "Оплаты клиентов", "Перечислено (списано из кассы)",
                           "Зачислено банком", "Комиссия", "Комиссия, %"], rows, money_from=4,
                 widths=[11, 24, 12, 16, 18, 16, 14, 11], total=True)
    ws.cell(row, 1, "Итог по кассам за период").font = FB
    per = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in a.acquiring:
        acc = per[r.cashbox]
        acc[0] += r.payments
        acc[1] += r.sent
        acc[2] += r.credited
    closing = {}
    for b in sorted(data.balances, key=lambda b: b.day):
        closing[b.cashbox] = b.closing
    box_rows = [[box, round(v[0], 2), round(v[1], 2), round(v[2], 2), round(v[1] - v[2], 2),
                 _pct(v[1] - v[2], v[1]), round(closing.get(box, 0.0), 2)] for box, v in sorted(per.items())]
    _table(ws, row + 1, ["Касса", "Оплаты клиентов", "Перечислено", "Зачислено банком", "Комиссия",
                         "Комиссия, %", "Ещё не перечислено (остаток кассы)"], box_rows, money_from=2,
           total=True)


# ------------------------------------------------------------------ долги
def sheet_debts(wb: Workbook, data: RevenueData) -> None:
    a: Analytics = data.analytics
    ws = wb.create_sheet("Долги клиентов")
    since = f"{a.debts_since:%d.%m.%Y}" if a.debts_since else "—"
    _title(ws, f"Заказ-наряды, оплаченные не полностью (с {since} по {data.d2:%d.%m.%Y})",
           "Долг = сумма заказ-наряда − все оплаты по нему + возвраты − оплата бонусами. Сюда попадают и "
           "заказ-наряды, которые ещё в работе (смотрите статус), и погашенные взаимозачётом — их "
           "программа не видит.")
    row = _error(ws, 4, a, "Долги клиентов")
    if "Долги клиентов" in a.errors:
        return
    today = data.d2
    debts = sorted(a.debts, key=lambda d: -d.debt)
    rows = [[d.dt.date(), d.number, d.client, d.car, d.plate, d.status, d.order_type, d.organization, d.master,
             d.amount, d.paid, d.refunded, d.bonuses, d.debt, (today - d.dt.date()).days] for d in debts]
    end = _table(ws, row, ["Дата ЗН", "№", "Клиент", "Автомобиль", "Госномер", "Статус", "Тип ЗН",
                           "Организация", "Мастер", "Сумма ЗН", "Оплачено", "Возвращено", "Бонусами", "Долг",
                           "Дней"], rows, money_from=10,
                 widths=[11, 13, 28, 22, 11, 16, 13, 20, 20, 14, 14, 13, 12, 14, 7], total=True)
    # «Дней» - не деньги: убрать формат и итог
    for r in range(row + 1, end - 1):
        ws.cell(r, 15).number_format = "0"
    ws.cell(end - 2, 15).value = None
    ws.freeze_panes = ws.cell(row + 1, 4)


# ------------------------------------------------------------------ механики
def sheet_workers(wb: Workbook, data: RevenueData) -> None:
    a: Analytics = data.analytics
    ws = wb.create_sheet("Механики")
    _title(ws, f"Выработка {data.d1:%d.%m.%Y}–{data.d2:%d.%m.%Y}",
           "Регистр «ЗаказНаряды» Далиона: нормочасы и суммы работ по механикам, суммы по мастерам-приёмщикам.")
    row = _error(ws, 4, a, "Механики")
    if "Механики" not in a.errors:
        ws.cell(row, 1, "Механики").font = F_DAY
        rows = [[w.name, w.orders, round(w.hours_norm, 2), round(w.hours_fact, 2), round(w.works, 2),
                 round(w.works / w.hours_norm, 2) if w.hours_norm else None] for w in a.mechanics]
        end = _table(ws, row + 1, ["Механик", "Заказ-нарядов", "Нормочасы", "Нормочасы факт", "Сумма работ",
                                   "Работ на 1 нормочас"], rows, money_from=5,
                     widths=[30, 14, 13, 14, 16, 16], total=True)
        for r in range(row + 2, end - 1):
            for c in (3, 4):
                ws.cell(r, c).number_format = "0.00"
        if rows:                        # итог: средние - по общим суммам, а не сложением
            hours = sum(w.hours_norm for w in a.mechanics)
            ws.cell(end - 2, 2).value = sum(w.orders for w in a.mechanics)
            ws.cell(end - 2, 3).value = round(hours, 2)
            ws.cell(end - 2, 4).value = round(sum(w.hours_fact for w in a.mechanics), 2)
            ws.cell(end - 2, 6).value = round(sum(w.works for w in a.mechanics) / hours, 2) if hours else None
        row = end
    row = _error(ws, row, a, "Мастера-приёмщики")
    if "Мастера-приёмщики" not in a.errors:
        ws.cell(row, 1, "Мастера-приёмщики").font = F_DAY
        rows = [[w.name, w.orders, round(w.works, 2), round(w.parts, 2), round(w.works + w.parts, 2),
                 round((w.works + w.parts) / w.orders, 2) if w.orders else None] for w in a.masters]
        end = _table(ws, row + 1, ["Мастер-приёмщик", "Заказ-нарядов", "Работы", "Запчасти", "Всего",
                                   "Средний чек"], rows, money_from=3, total=True)
        if rows:
            orders = sum(w.orders for w in a.masters)
            ws.cell(end - 2, 2).value = orders
            ws.cell(end - 2, 6).value = (round(sum(w.works + w.parts for w in a.masters) / orders, 2)
                                         if orders else None)


def add_analytics_sheets(wb: Workbook, data: RevenueData) -> None:
    if data.analytics is None:
        return
    for fn in (sheet_profit, sheet_dynamics, sheet_acquiring, sheet_debts, sheet_workers):
        fn(wb, data)
