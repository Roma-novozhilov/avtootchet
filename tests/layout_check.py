"""Состав отчёта: листы и колонки по выбору владельца, порядок, окно настройки.

    QT_QPA_PLATFORM=offscreen python tests/layout_check.py
"""
import os
import runpy
import sys
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp()

from openpyxl import load_workbook  # noqa: E402

from avtootchet.owner import report_layout as L  # noqa: E402
from avtootchet.owner.revenue_report import build_revenue_report  # noqa: E402

# --- настройки: мусор отбрасывается, новое добавляется включённым
lay = L.normalize({"sheets": [["Итоги", True], ["Нет такого", True], ["Выручка по дням", False]],
                   "columns": {"Перемещения ДС": [["Куда", True], ["Откуда", False], ["Лишняя", True]]}})
names = [n for n, _ in lay["sheets"]]
assert names[:2] == ["Итоги", "Выручка по дням"] and "Нет такого" not in names
assert set(names) == set(L.SHEETS) and len(names) == len(L.SHEETS)
assert L.enabled_sheets(lay)[0] == "Итоги" and "Выручка по дням" not in L.enabled_sheets(lay)
assert L.enabled_columns(lay, "Перемещения ДС")[:2] == ["Куда", "Дата"]
assert L.enabled_columns(lay, "Прибыль") == []
assert L.enabled_columns(lay, "Замечания") == L.PAYMENT_COLUMNS, "Замечания - по настройке «Оплаты подробно»"
assert L.select(["a", "b", "c"], [[1, 2, 3]], [5, 6, 7], ["c", "a"]) == (["c", "a"], [[3, 1]], [7, 5])

# --- отчёт с выбранным составом
data = runpy.run_path(str(ROOT / "tests" / "revenue_check.py"))["data"]
layout = L.default_layout()
order = ["Долги клиентов", "Итоги", "Оплаты подробно", "Расходы подробно", "Перемещения ДС"]
layout["sheets"] = [[n, True] for n in order] + [[n, False] for n in L.SHEETS if n not in order]
layout["columns"]["Оплаты подробно"] = [["Клиент", True], ["Итого", True], ["Дата", True]] + \
    [[c, False] for c in L.PAYMENT_COLUMNS if c not in ("Клиент", "Итого", "Дата")]
layout["columns"]["Долги клиентов"] = [["Клиент", True], ["Долг", True]] + \
    [[c, False] for c in L.DEBT_COLUMNS if c not in ("Клиент", "Долг")]
wb = load_workbook(BytesIO(build_revenue_report(data, layout)))
assert wb.sheetnames == order, wb.sheetnames
hdr = [c.value for c in wb["Оплаты подробно"][3]]
assert hdr == ["Клиент", "Итого", "Дата"], hdr
total = [r for r in wb["Оплаты подробно"].iter_rows(values_only=True) if r[0] == "Итого"][0]
assert total[1] == round(sum(p.amount for p in data.revenue), 2), total
assert wb["Оплаты подробно"]["B4"].number_format.startswith("#,##0.00"), "Итого - денежный формат"
debt = [r for r in wb["Долги клиентов"].iter_rows(values_only=True) if r and r[0] == "Петров"]
assert debt and debt[0][:2] == ("Петров", 3500), debt
assert [c.value for c in wb["Перемещения ДС"][3]] == L.TRANSFER_COLUMNS

# без настройки - всё как раньше
wb = load_workbook(BytesIO(build_revenue_report(data, {})))
assert wb.sheetnames[0] == "Выручка по дням" and len(wb.sheetnames) == 16

# --- окно
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from avtootchet.common.settings import Settings  # noqa: E402
from avtootchet.owner.app import DEFAULTS  # noqa: E402
from avtootchet.owner.layout_dialog import ReportLayoutDialog  # noqa: E402

app = QApplication.instance() or QApplication([])
st = Settings("owner", DEFAULTS)
dlg = ReportLayoutDialog(st)
dlg.sheets.item(0).setCheckState(Qt.Unchecked)          # «Выручка по дням» выключить
dlg.sheets.setCurrentRow(1)                              # «Итоги» поднять наверх
dlg.sheets.findItems("Итоги", Qt.MatchExactly)[0]
from avtootchet.owner.layout_dialog import _move  # noqa: E402
_move(dlg.sheets, -1)
row = [dlg.sheets.item(i).text() for i in range(dlg.sheets.count())].index("Перемещения ДС")
dlg.sheets.setCurrentRow(row)                            # колонки перемещений
assert dlg.cols.isEnabled() and dlg.cols.count() == len(L.TRANSFER_COLUMNS)
dlg.cols.item(L.TRANSFER_COLUMNS.index("Комментарий")).setCheckState(Qt.Unchecked)
dlg.sheets.setCurrentRow(2)                              # лист без колонок
assert not dlg.cols.isEnabled() and "постоянные" in dlg.cols_title.text()
dlg._save()
saved = Settings("owner", DEFAULTS)["report_layout"]
assert [n for n, _ in saved["sheets"]][:2] == ["Итоги", "Выручка по дням"]
assert dict(map(tuple, saved["sheets"]))["Выручка по дням"] is False
assert "Комментарий" not in L.enabled_columns(saved, "Перемещения ДС")
dlg2 = ReportLayoutDialog(st)
for i in range(dlg2.sheets.count()):
    dlg2.sheets.item(i).setCheckState(Qt.Unchecked)
from PySide6.QtWidgets import QMessageBox  # noqa: E402
warned = []
QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2]))
dlg2._save()
assert warned and "хотя бы один лист" in warned[0], "без листов сохранять нельзя"
print("OK: состав отчёта")
