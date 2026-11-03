"""Демо-кассовая книга (как печатная форма КО-4 из 1С) с намеренными ошибками.

    python tools/make_demo_cashbook.py [ГГГГ-ММ-ДД ГГГГ-ММ-ДД] [--clean]

Ошибки, которые вносятся (без --clean):
  1. пропущен один приходный ордер;
  2. в одном расходном ордере неверная сумма;
  3. один ордер записан не тем днём;
  4. добавлена лишняя запись, которой нет в 1С;
  5. в одном дне неверно посчитан «Итого за день» по приходу.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avtootchet.common.models import IN  # noqa: E402
from avtootchet.owner.sources import DemoSource  # noqa: E402


def build(d1: date, d2: date, clean: bool, out: Path) -> list[str]:
    onec = DemoSource(ROOT / "demo" / "export.json").load(d1, d2)
    rows = [{"date": o.dt.date(), "kind": o.kind, "num": o.raw_number, "who": o.counterparty,
             "acc": "62.01" if o.kind == IN else ("70" if "Зарплата" in o.article else "60.01"),
             "amount": o.amount} for o in onec.ops]
    notes = []
    break_total_day = None
    if not clean and len(rows) > 20:
        ins = [r for r in rows if r["kind"] == IN]
        outs = [r for r in rows if r["kind"] != IN]
        miss = ins[len(ins) // 3]
        rows.remove(miss)
        notes.append(f"пропущен ПКО №{miss['num'].lstrip('0')} от {miss['date']:%d.%m}")
        wrong = outs[len(outs) // 2]
        wrong["amount"] += 1000
        notes.append(f"в РКО №{wrong['num'].lstrip('0')} сумма больше на 1000")
        shifted = ins[len(ins) // 2]
        shifted["date"] += timedelta(days=1)
        notes.append(f"ПКО №{shifted['num'].lstrip('0')} записан на день позже")
        extra_day = rows[len(rows) * 2 // 3]["date"]
        rows.append({"date": extra_day, "kind": IN, "num": "9999", "who": "Неизвестный клиент",
                     "acc": "62.01", "amount": 4500.0})
        notes.append(f"лишний ПКО №9999 от {extra_day:%d.%m}")
        break_total_day = rows[len(rows) // 4]["date"]
        notes.append(f"неверный «Итого за день» по приходу за {break_total_day:%d.%m}")

    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)

    wb = Workbook()
    ws = wb.active
    ws.title = "Кассовая книга"
    bold = Font(bold=True)
    for col, w in zip("ABCDE", (16, 34, 18, 16, 16)):
        ws.column_dimensions[col].width = w
    ws.append(["ООО «Автотехцентр» (демо)"])
    ws.append([f"КАССОВАЯ КНИГА за {d1:%d.%m.%Y} - {d2:%d.%m.%Y}"])
    balance = onec.opening
    for day in sorted(by_day):
        day_rows = sorted(by_day[day], key=lambda r: (r["kind"] != IN, r["num"]))
        ws.append([])
        ws.append([f"КАССА за {day:%d.%m.%Y}"])
        ws.cell(ws.max_row, 1).font = bold
        ws.append(["Номер документа", "От кого получено или кому выдано",
                   "Номер корреспондирующего счета, субсчета", "Приход, руб. коп.", "Расход, руб. коп."])
        ws.append([1, 2, 3, 4, 5])
        ws.append(["", "Остаток на начало дня", "", balance, ""])
        t_in = t_out = 0.0
        for r in day_rows:
            if r["kind"] == IN:
                ws.append([r["num"], r["who"], r["acc"], r["amount"], None])
                t_in += r["amount"]
            else:
                ws.append([r["num"], r["who"], r["acc"], None, r["amount"]])
                t_out += r["amount"]
        shown_in = t_in + (500 if day == break_total_day else 0)
        ws.append(["", "Итого за день", "", shown_in, t_out])
        balance += t_in - t_out
        ws.append(["", "Остаток на конец дня", "", balance, ""])
        ws.append(["", "в том числе на выплату заработной платы", "", 0, ""])
    ws.append([])
    ws.append(["Записи в кассовой книге проверил и документы в количестве ____ получил."])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return notes


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    d1 = date.fromisoformat(args[0]) if args else date(2026, 10, 1)
    d2 = date.fromisoformat(args[1]) if len(args) > 1 else date(2026, 10, 31)
    clean = "--clean" in sys.argv
    name = "Кассовая книга (демо, без ошибок).xlsx" if clean else "Кассовая книга (демо, с ошибками).xlsx"
    out = ROOT / "demo" / name
    for n in build(d1, d2, clean, out):
        print(" -", n)
    print(out)
