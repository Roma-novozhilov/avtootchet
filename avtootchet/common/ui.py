"""Общие элементы интерфейса: стиль, индикаторы, фоновые задачи."""
from __future__ import annotations

import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QAbstractSpinBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget

STYLE = """
QWidget { font-family: "Segoe UI", Arial; font-size: 10pt; color: #1f2937; }
QMainWindow, QDialog { background: #f5f6f8; }
QGroupBox { background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px;
            margin-top: 14px; padding: 12px 12px 10px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #4b5563; }
QPushButton { background: #ffffff; border: 1px solid #c9ced6; border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #eef2f7; }
QPushButton:disabled { color: #9ca3af; background: #f3f4f6; border-color: #e5e7eb; }
QPushButton#primary { background: #1f4e9c; color: white; border: none; font-weight: 600;
                      padding: 10px 18px; font-size: 11pt; }
QPushButton#primary:hover { background: #2a5fb8; }
QPushButton#primary:disabled { background: #a7b6d1; color: #eef1f6; }
QPushButton#danger { color: #b42318; border-color: #f0b4ad; }
QPushButton#danger:hover { background: #fdecea; }
QPushButton#tool { padding: 4px 6px; }
QLineEdit, QDateEdit, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #c9ced6;
            border-radius: 5px; padding: 4px 6px; min-height: 22px; }
QTabWidget::pane { border: 1px solid #dfe3e8; border-radius: 6px; background: #ffffff; top: -1px; }
QTabBar::tab { padding: 6px 14px; border: 1px solid #dfe3e8; border-bottom: none;
               border-top-left-radius: 6px; border-top-right-radius: 6px; background: #eceff3;
               margin-right: 2px; }
QTabBar::tab:selected { background: #ffffff; font-weight: 600; }
QTableWidget { gridline-color: #e5e7eb; border: none; }
QListWidget { background: #ffffff; border: 1px solid #dfe3e8; border-radius: 6px; }
QListWidget::item { padding: 3px 4px; }
QListWidget#nav::item { padding: 7px 10px; }
QListWidget#nav::item:selected { background: #1f4e9c; color: white; }
QHeaderView::section { background: #1f3864; color: white; padding: 5px; border: none;
                       border-right: 1px solid #2d4a7a; font-weight: 600; }
QLabel#title { font-size: 15pt; font-weight: 700; color: #1f3864; }
QLabel#hint { color: #6b7280; font-size: 9pt; }
QLabel#verdict_ok { color: #067647; font-weight: 700; font-size: 11pt; }
QLabel#verdict_bad { color: #b42318; font-weight: 700; font-size: 11pt; }
"""

COLORS = {"grey": "#9ca3af", "green": "#12b76a", "yellow": "#f79009", "red": "#f04438",
          "blue": "#2e90fa"}


class StatusDot(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(16)
        self.set("grey")

    def set(self, color: str):
        self.setText("●")
        self.setStyleSheet(f"color: {COLORS[color]}; font-size: 14pt;")


def _log_error():
    """В собранном .exe нет консоли (sys.stderr = None) - пишем технические ошибки в файл."""
    try:
        from datetime import datetime  # noqa: PLC0415

        from .paths import app_dir  # noqa: PLC0415
        with open(app_dir() / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%d.%m.%Y %H:%M:%S}\n{traceback.format_exc()}\n")
    except Exception:  # noqa: BLE001
        pass


class _Bridge(QObject):
    done = Signal(object, object)  # (результат, ошибка)


def run_in_background(fn: Callable, on_done: Callable[[object, Exception | None], None]):
    """Выполнить fn в отдельном потоке, результат вернуть в поток интерфейса."""
    bridge = _Bridge()
    bridge.done.connect(lambda r, e: on_done(r, e), Qt.QueuedConnection)

    def work():
        try:
            bridge.done.emit(fn(), None)
        except Exception as e:  # noqa: BLE001
            _log_error()
            bridge.done.emit(None, e)

    run_in_background._keep = getattr(run_in_background, "_keep", set())
    run_in_background._keep.add(bridge)
    bridge.done.connect(lambda *_: run_in_background._keep.discard(bridge))
    threading.Thread(target=work, daemon=True).start()


class _Form(QFormLayout):
    """Форма, у которой подпись стоит по центру строки, а не прижата к верху поля."""

    def addRow(self, *args):  # noqa: N802 - имя Qt
        if len(args) == 2 and isinstance(args[0], str) and args[0]:
            label = QLabel(args[0])
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            field = args[1]
            hint = field.sizeHint().height() if isinstance(field, QWidget) else field.sizeHint().height()
            label.setMinimumHeight(hint)
            return super().addRow(label, field)
        return super().addRow(*args)


def form_layout(parent: QWidget | None = None) -> QFormLayout:
    return _Form(parent) if parent is not None else _Form()


def tool_button(text: str = "…", tip: str = "") -> QPushButton:
    """Узкая кнопка рядом с полем («…» - выбрать файл или папку)."""
    b = QPushButton(text)
    b.setObjectName("tool")
    b.setFixedWidth(36)
    if tip:
        b.setToolTip(tip)
    return b


def short_spin(w: QAbstractSpinBox, width: int = 130) -> QAbstractSpinBox:
    """Числа и даты не растягиваем на всю ширину окна."""
    w.setMaximumWidth(width)
    return w


def show_start(*edits: QLineEdit) -> None:
    """Длинный текст в поле показывать с начала, а не с конца."""
    for e in edits:
        e.setCursorPosition(0)


def error_box(parent: QWidget, title: str, text: str):
    QMessageBox.critical(parent, title, text)


def money(x: float | None) -> str:
    if x is None:
        return ""
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " ₽"
