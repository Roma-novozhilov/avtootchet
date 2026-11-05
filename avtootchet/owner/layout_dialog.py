"""Окно «Состав отчёта»: галочками выбрать листы и колонки, кнопками ↑ ↓ (или мышью) - порядок."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from .report_layout import COLUMNS_OF_SHEET, SHEET_HINTS, default_layout, normalize


def _fill(lst: QListWidget, items: list[list], hints: dict | None = None) -> None:
    lst.clear()
    for name, on in items:
        it = QListWidgetItem(name)
        it.setData(Qt.UserRole, name)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        if hints and name in hints:
            it.setToolTip(hints[name])
        lst.addItem(it)


def _read(lst: QListWidget) -> list[list]:
    return [[lst.item(i).data(Qt.UserRole), lst.item(i).checkState() == Qt.Checked]
            for i in range(lst.count())]


def _list() -> QListWidget:
    lst = QListWidget()
    lst.setDragDropMode(QAbstractItemView.InternalMove)
    lst.setDefaultDropAction(Qt.MoveAction)
    lst.setSelectionMode(QAbstractItemView.SingleSelection)
    return lst


def _move(lst: QListWidget, step: int) -> None:
    row = lst.currentRow()
    new = row + step
    if row < 0 or not 0 <= new < lst.count():
        return
    item = lst.takeItem(row)
    lst.insertItem(new, item)
    lst.setCurrentRow(new)


def _arrows(lst: QListWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    for text, step, tip in (("↑", -1, "Выше"), ("↓", 1, "Ниже")):
        b = QPushButton(text)
        b.setFixedWidth(40)
        b.setToolTip(tip)
        b.clicked.connect(lambda _=False, s=step: _move(lst, s))
        row.addWidget(b)
    for text, state in (("Все", Qt.Checked), ("Ни одного", Qt.Unchecked)):
        b = QPushButton(text)
        b.clicked.connect(lambda _=False, st=state: [lst.item(i).setCheckState(st) for i in range(lst.count())])
        row.addWidget(b)
    row.addStretch()
    return row


class ReportLayoutDialog(QDialog):
    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Состав отчёта")
        self.resize(760, 560)
        self.layout_data = normalize(settings["report_layout"])
        self._table: str | None = None

        lay = QVBoxLayout(self)
        hint = QLabel("Галочкой отметьте, что войдёт в отчёт по выручке. Порядок — кнопками ↑ ↓ или "
                      "перетаскиванием. Для таблиц-списков справа можно выбрать и расставить колонки.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        body = QHBoxLayout()
        g = QGroupBox("Листы отчёта")
        v = QVBoxLayout(g)
        self.sheets = _list()
        _fill(self.sheets, self.layout_data["sheets"], SHEET_HINTS)
        self.sheets.currentItemChanged.connect(self._sheet_selected)
        v.addWidget(self.sheets, 1)
        v.addLayout(_arrows(self.sheets))
        body.addWidget(g, 1)

        g = QGroupBox("Колонки")
        v = QVBoxLayout(g)
        self.cols_title = QLabel("Выберите слева лист-список («Оплаты подробно», «Расходы подробно», "
                                 "«Долги клиентов», «Перемещения ДС»).")
        self.cols_title.setWordWrap(True)
        v.addWidget(self.cols_title)
        self.cols = _list()
        v.addWidget(self.cols, 1)
        self.cols_buttons = QWidget()
        self.cols_buttons.setLayout(_arrows(self.cols))
        v.addWidget(self.cols_buttons)
        body.addWidget(g, 1)
        lay.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        reset = buttons.addButton("По умолчанию", QDialogButtonBox.ResetRole)
        reset.clicked.connect(self._reset)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._show_columns(None)
        if self.sheets.count():
            self.sheets.setCurrentRow(0)

    # ------------------------------------------------------------ колонки выбранного листа
    def _store_columns(self):
        if self._table:
            self.layout_data["columns"][self._table] = _read(self.cols)

    def _show_columns(self, table: str | None):
        self._table = table
        enabled = table is not None
        self.cols.setEnabled(enabled)
        self.cols_buttons.setEnabled(enabled)
        if not enabled:
            self.cols.clear()
            return
        _fill(self.cols, self.layout_data["columns"][table])
        shared = [s for s, t in COLUMNS_OF_SHEET.items() if t == table and s != table]
        extra = f" (так же — {', '.join('«' + s + '»' for s in shared)})" if shared else ""
        self.cols_title.setText(f"Колонки листа «{table}»{extra}:")

    def _sheet_selected(self, current, _previous):
        self._store_columns()
        name = current.data(Qt.UserRole) if current else None
        table = COLUMNS_OF_SHEET.get(name) if name else None
        if table is None and name:
            self.cols_title.setText(f"У листа «{name}» колонки постоянные — его можно только включить "
                                    "или выключить и поставить в нужное место.")
        self._show_columns(table)

    # ------------------------------------------------------------ кнопки
    def _reset(self):
        self.layout_data = default_layout()
        self._table = None
        _fill(self.sheets, self.layout_data["sheets"], SHEET_HINTS)
        self.sheets.setCurrentRow(0)

    def _save(self):
        self._store_columns()
        self.layout_data["sheets"] = _read(self.sheets)
        if not any(on for _, on in self.layout_data["sheets"]):
            QMessageBox.warning(self, "Состав отчёта", "Отметьте хотя бы один лист.")
            return
        for table, cols in self.layout_data["columns"].items():
            if not any(on for _, on in cols):
                QMessageBox.warning(self, "Состав отчёта", f"В листе «{table}» отметьте хотя бы одну колонку.")
                return
        self.s["report_layout"] = self.layout_data
        self.s.save()
        self.accept()
