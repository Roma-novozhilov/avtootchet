"""Excel-отчёт «Залежавшиеся запчасти». Только значения, без формул.

Листы: «Залежавшиеся» (лежат на складе N+ дней, сначала самые старые и дорогие),
«Под заказ-наряды» (пришли под конкретный ЗН и лежат), «Выданы в ЗН» (выданы в
заказ-наряд внутренней накладной, ЗН не закрыт), «По менеджерам», «По поставщикам».
"""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO

from openpyxl import Workbook

from .revenue_report import F_SUB, F_TITLE, _cell, _table
from .stock import StaleRow, StockData

STALE_HEADERS = ["Запчасть", "Артикул", "Группа", "Лежит, шт", "Дата прихода", "Дней лежит",
                 "Приходная накладная", "Поставщик", "Провёл (пользователь 1С)", "Менеджер",
                 "Цена прихода", "Сумма"]
STALE_WIDTHS = [44, 16, 22, 10, 12, 10, 16, 28, 22, 22, 12, 14]
MONEY_COLS = {"Цена прихода", "Сумма"}


def _row(r: StaleRow) -> list:
    return [r.name, r.article, r.group, r.qty, r.received, r.days if r.days is not None else "нет данных",
            r.number, r.supplier, r.author, r.manager, r.price, r.amount]


def _head(ws, title: str, sub: str) -> int:
    _cell(ws, 1, 1, title, font=F_TITLE, border=False)
    _cell(ws, 2, 1, sub, font=F_SUB, border=False)
    return 4


def _sheet_rows(wb: Workbook, name: str, title: str, sub: str, rows: list[StaleRow], extra: bool = False):
    ws = wb.create_sheet(name)
    row = _head(ws, title, sub)
    headers = STALE_HEADERS + (["Заказ-наряд"] if extra else [])
    widths = STALE_WIDTHS + ([30] if extra else [])
    body = [_row(r) + ([r.order] if extra else []) for r in rows]
    if not body:
        _cell(ws, row, 1, "Таких позиций нет.", border=False)
        return
    _table(ws, row, headers, body, widths=widths, total=True, money=MONEY_COLS)
    ws.freeze_panes = ws.cell(row=row + 1, column=2)
    ws.auto_filter.ref = f"A{row}:{ws.cell(row=row, column=len(headers)).column_letter}{row + len(body)}"


def _summary(wb: Workbook, name: str, title: str, key, rows: list[StaleRow], label: str):
    ws = wb.create_sheet(name)
    row = _head(ws, title, "Только позиции с листа «Залежавшиеся».")
    agg = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        a = agg[key(r) or "(не указан)"]
        a[0] += 1
        a[1] += r.amount
        a[2] = max(a[2], r.days or 10 ** 4)
    body = [[k, n, round(s, 2), d if d < 10 ** 4 else "нет данных"]
            for k, (n, s, d) in sorted(agg.items(), key=lambda kv: -kv[1][1])]
    if not body:
        _cell(ws, row, 1, "Таких позиций нет.", border=False)
        return
    _table(ws, row, [label, "Позиций", "Сумма", "Дольше всего лежит, дней"], body,
           widths=[36, 10, 16, 14], total=True, money={"Сумма"})


def build_stock_report(data: StockData) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    n = data.min_days
    excl = f" Не учитываются группы: {', '.join(data.excluded_groups)}." if data.excluded_groups else ""
    total = f"{data.total:,.2f}".replace(",", " ")
    sub = (f"На {data.on:%d.%m.%Y} · лежат {n} дней и больше · позиций {len(data.stale)} · "
           f"сумма по ценам прихода {total} ₽.{excl}")
    _sheet_rows(wb, "Залежавшиеся", f"Запчасти на складе больше {n} дней", sub, data.stale)
    _sheet_rows(wb, "Под заказ-наряды", "Пришли под заказ-наряд и лежат на складе",
                "Заказаны под конкретную машину, но не установлены и не проданы.", data.ordered, extra=True)

    ws = wb.create_sheet("Выданы в ЗН")
    row = _head(ws, f"Выданы в заказ-наряд больше {n} дней назад, ЗН не закрыт",
                "Запчасть уже не на полке, но и не продана: машина ждёт или ЗН забыли закрыть.")
    body = [[r.order, r.order_date, r.days, r.name, r.article, r.qty] for r in data.issued]
    if body:
        _table(ws, row, ["Заказ-наряд", "Дата ЗН", "Дней", "Запчасть", "Артикул", "Кол-во"], body,
               widths=[36, 12, 8, 44, 16, 10], money=set())
    else:
        _cell(ws, row, 1, "Таких позиций нет.", border=False)

    _summary(wb, "По менеджерам", "Залежавшиеся по тем, кто провёл приход",
             lambda r: r.author, data.stale, "Провёл (пользователь 1С)")
    _summary(wb, "По поставщикам", "Залежавшиеся по поставщикам", lambda r: r.supplier, data.stale, "Поставщик")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def stock_filename(data: StockData) -> str:
    return f"Залежавшиеся запчасти {data.on:%d.%m.%Y}.xlsx"
