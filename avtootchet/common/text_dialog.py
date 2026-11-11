"""Окно с текстом и кнопкой «Скопировать» (результаты проверок структуры базы)."""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class TextDialog(QDialog):
    """Текст с кнопкой «Скопировать» (через удалённый стол копируется на свой компьютер)."""

    def __init__(self, title: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        hint = QLabel("Только количества и названия, без сумм и клиентов. Нажмите «Скопировать» "
                      "и вставьте текст в чат.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.text = QPlainTextEdit(text)
        self.text.setReadOnly(True)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        row.addStretch()
        copy = QPushButton("Скопировать")
        copy.setObjectName("primary")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.copy_btn = copy

    def _copy(self):
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        self.copy_btn.setText("Скопировано ✓")
