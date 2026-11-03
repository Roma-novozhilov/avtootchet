"""Разбор кассовой книги бухгалтера из Excel.

Понимает два вида файлов:
  1. Печатная форма КО-4 из 1С (листы «КАССА за 01.10.2026», остатки на начало
     и конец дня, «Итого за день», колонки Приход/Расход).
  2. Обычная таблица, где у каждой строки есть дата: Дата | № | Контрагент | Приход | Расход
     (или Дата | № | Вид | Сумма).
Колонки ищутся по заголовкам, поэтому точный порядок не важен.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO

from openpyxl import load_workbook

from ..common.models import IN, OUT, BookEntry, CashBook, norm_number, parse_amount


class BookParseError(Exception):
    pass


MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    s = str(value).lower()
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y += 2000 if y < 100 else 0
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", s)
    if m:
        word = m.group(2)
        for stem, num in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
            if word.startswith(stem):
                try:
                    return date(int(m.group(3)), num, int(m.group(1)))
                except ValueError:
                    return None
    return None


def _header_map(texts: list[str]) -> dict[str, int] | None:
    cols: dict[str, int] = {}
    for i, t in enumerate(texts):
        low = t.lower()
        if not low:
            continue
        if "приход" in low and "in" not in cols:
            cols["in"] = i
        elif "расход" in low and "out" not in cols:
            cols["out"] = i
        elif ("от кого" in low or "кому" in low or "контрагент" in low) and "who" not in cols:
            cols["who"] = i
        elif ("номер" in low and "док" in low or low in ("№", "номер", "№ док.", "№ документа")) \
                and "num" not in cols:
            cols["num"] = i
        elif "корр" in low and "acc" not in cols:
            cols["acc"] = i
        elif low.startswith("дата") and "date" not in cols:
            cols["date"] = i
        elif low.startswith("сумма") and "amount" not in cols:
            cols["amount"] = i
        elif (low.startswith("вид") or low.startswith("тип")) and "kind" not in cols:
            cols["kind"] = i
    if ("in" in cols and "out" in cols) or ("amount" in cols and ("kind" in cols or "num" in cols)):
        return cols
    return None


def _is_numbering_row(values: list) -> bool:
    """Строка «1 2 3 4 5» под шапкой КО-4."""
    nums = [v for v in values if v not in (None, "")]
    if len(nums) < 3:
        return False
    try:
        ints = [int(float(str(v))) for v in nums]
    except ValueError:
        return False
    return ints == list(range(ints[0], ints[0] + len(ints)))


def _cell(values: list, cols: dict, key: str):
    i = cols.get(key)
    return values[i] if i is not None and i < len(values) else None


def _first_amount(values: list, cols: dict) -> float | None:
    for key in ("in", "out", "amount"):
        a = parse_amount(_cell(values, cols, key))
        if a is not None:
            return a
    for v in reversed(values):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


SKIP_PHRASES = ("в том числе", "перенос", "записи в кассовой книге", "количество листов")


def parse_cashbook(data: bytes, filename: str) -> CashBook:
    try:
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise BookParseError(f"Не удалось открыть «{filename}» как Excel-файл (.xlsx): {e}") from None

    book = CashBook(filename=filename, received_at=datetime.now())
    for ws in wb.worksheets:
        cols: dict | None = None
        cur_date: date | None = None
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            values = list(row)
            texts = [str(v).strip() if v is not None else "" for v in values]
            low = " ".join(t for t in texts if t).lower()
            if not low:
                continue

            m = re.search(r"касса\s+за\s+(.+)", low)
            if m:
                found = parse_date(m.group(1)) or next(
                    (parse_date(v) for v in values if isinstance(v, (date, datetime))), None)
                if found:
                    cur_date = found
                continue

            header = _header_map(texts)
            if header:
                cols = header
                continue
            if cols is None or _is_numbering_row(values):
                continue

            if "остаток на начало" in low:
                if cur_date:
                    book.day(cur_date).opening = _first_amount(values, cols)
                continue
            if "остаток на конец" in low:
                if cur_date:
                    book.day(cur_date).closing = _first_amount(values, cols)
                continue
            if "итого за день" in low:
                if cur_date:
                    d = book.day(cur_date)
                    d.total_in = parse_amount(_cell(values, cols, "in")) or 0.0
                    d.total_out = parse_amount(_cell(values, cols, "out")) or 0.0
                continue
            if any(p in low for p in SKIP_PHRASES):
                continue

            row_date = parse_date(_cell(values, cols, "date")) if "date" in cols else None
            row_date = row_date or cur_date
            raw_num = _cell(values, cols, "num")
            who = str(_cell(values, cols, "who") or "").strip()
            acc = str(_cell(values, cols, "acc") or "").strip()

            amounts: list[tuple[str, float]] = []
            if "in" in cols:
                a_in = parse_amount(_cell(values, cols, "in"))
                a_out = parse_amount(_cell(values, cols, "out"))
                if a_in:
                    amounts.append((IN, a_in))
                if a_out:
                    amounts.append((OUT, a_out))
            else:
                a = parse_amount(_cell(values, cols, "amount"))
                kind_text = f"{_cell(values, cols, 'kind') or ''} {raw_num or ''}".lower()
                if a:
                    if "приход" in kind_text or "пко" in kind_text:
                        amounts.append((IN, a))
                    elif "расход" in kind_text or "рко" in kind_text:
                        amounts.append((OUT, a))
                    else:
                        book.warnings.append(f"{ws.title}, строка {row_idx}: не понял, приход это или расход")
            if not amounts:
                continue
            if row_date is None:
                book.warnings.append(f"{ws.title}, строка {row_idx}: не найдена дата операции, строка пропущена")
                continue
            for kind, amount in amounts:
                book.entries.append(BookEntry(
                    date=row_date, kind=kind, number=norm_number(raw_num),
                    raw_number="" if raw_num is None else str(raw_num), amount=amount,
                    counterparty=who, account=acc, sheet=ws.title, row=row_idx))
    wb.close()

    if not book.entries and not book.days:
        raise BookParseError(
            f"В файле «{filename}» не найдено ни одной кассовой операции. Нужна таблица "
            "с колонками «Приход» и «Расход» (или «Сумма» + «Вид»).")
    book.entries.sort(key=lambda e: (e.date, e.kind, int(e.number or 0)))
    return book
