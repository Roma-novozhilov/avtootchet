"""АвтоОтчёт — маркетинг. Отдельная программа для маркетолога: только CRM, заявки, задачи
и «Простые звонки». Ни кассы, ни выручки, ни склада здесь нет - их код в эту программу не входит.

Подключается к Далиону под пользователем маркетолога (его права в Далионе - вторая защита),
только чтение. Свои настройки (%APPDATA%\\AvtoOtchet\\marketing_settings.json, пароль 1С
зашифрован DPAPI) и свой пароль на вход.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import (QApplication, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import __version__
from ..common import app_lock, updater
from ..common.journal import Journal
from ..common.settings import Settings
from ..common.ui import STYLE, run_in_background
from ..common.update_ui import UpdateController
from ..owner.crm_probe import run_crm_probe
from ..common.text_dialog import TextDialog
from ..owner.sources import ComSource, SourceError

TITLE = "АвтоОтчёт — маркетинг"
DEFAULTS = {
    "com": {"server": r"C:\1C\Dalion", "base": "", "user": "", "password": "", "platform": ""},
    "app_lock": {},
}


def check_connection(src: ComSource) -> str:
    """Подключиться и выполнить пустой запрос - проверка пути, пользователя и пароля."""
    src._check_settings()
    conn = src._connect()
    q = conn.NewObject("Запрос")
    q.Text = "ВЫБРАТЬ 1 КАК Один"
    sel = q.Execute().Choose()
    sel.Next()
    return "Подключение к 1С работает."


class MarketingWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings("marketing", DEFAULTS)
        self.journal = Journal("marketing")
        self.setWindowTitle(f"{TITLE}  v{__version__}")
        self.resize(640, 420)
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(16, 12, 16, 12)

        top = QHBoxLayout()
        title = QLabel("CRM и звонки")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        self.updates = UpdateController(self, log=self._log)
        top.addWidget(self.updates.button)
        btn_pw = QPushButton("Пароль программы…")
        btn_pw.clicked.connect(lambda: app_lock.SetPasswordDialog(self.settings, self).exec())
        top.addWidget(btn_pw)
        lay.addLayout(top)

        c = self.settings["com"]
        g = QGroupBox("Подключение к 1С:Далион (под вашим пользователем Далиона)")
        f = QFormLayout(g)
        self.server = QLineEdit(c["server"])
        self.user = QLineEdit(c["user"])
        self.password = QLineEdit(c["password"])
        self.password.setEchoMode(QLineEdit.Password)
        self.platform = QLineEdit(c["platform"])
        self.platform.setPlaceholderText("например 8.3.27.2325; пусто - как установлено")
        f.addRow("Путь к базе:", self.server)
        f.addRow("Пользователь Далиона:", self.user)
        f.addRow("Пароль Далиона:", self.password)
        f.addRow("Версия платформы:", self.platform)
        row = QHBoxLayout()
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save)
        self.btn_test = QPushButton("Проверить подключение")
        self.btn_test.clicked.connect(self._test)
        row.addWidget(save)
        row.addWidget(self.btn_test)
        row.addStretch()
        f.addRow(row)
        lay.addWidget(g)

        g = QGroupBox("Данные CRM")
        v = QVBoxLayout(g)
        self.btn_crm = QPushButton("Проверить структуру CRM и звонков (без телефонов)")
        self.btn_crm.setObjectName("primary")
        self.btn_crm.clicked.connect(self._crm)
        v.addWidget(self.btn_crm)
        hint = QLabel("Показывает, какие в Далионе есть заявки, задачи, лиды и звонки и как заполнены их поля "
                      "за 30 дней — только названия и количества. По результату будет сделана выгрузка для "
                      "витрины. Программа только читает данные Далиона и ничего в нём не меняет.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        lay.addWidget(g)
        lay.addStretch()
        self.setCentralWidget(root)
        self._log("Программа открыта")

    def _log(self, what: str, details: str = ""):
        self.journal.write(what, details)

    def _src(self) -> ComSource:
        return ComSource(self.server.text().strip(), "", self.user.text().strip(), self.password.text(),
                         platform=self.platform.text())

    def _save(self):
        self.settings["com"] = {"server": self.server.text().strip(), "base": "",
                                "user": self.user.text().strip(), "password": self.password.text(),
                                "platform": self.platform.text().strip()}
        self.settings.save()
        QMessageBox.information(self, TITLE, "Настройки сохранены.")

    def _busy(self, btn: QPushButton, text: str | None):
        btn.setEnabled(text is None)
        if text:
            btn.setProperty("orig", btn.text())
            btn.setText(text)
        else:
            btn.setText(btn.property("orig"))

    def _test(self):
        self._busy(self.btn_test, "Подключаюсь…")

        def done(msg, err):
            self._busy(self.btn_test, None)
            if err:
                QMessageBox.critical(self, "1С", str(err))
            else:
                QMessageBox.information(self, "1С", msg)

        run_in_background(lambda: check_connection(self._src()), done)

    def _crm(self):
        self._busy(self.btn_crm, "Проверяю… (до нескольких минут)")

        def done(text, err):
            self._busy(self.btn_crm, None)
            if err:
                QMessageBox.critical(self, "1С", str(err))
                self._log("Проверка CRM: ошибка", type(err).__name__)
                return
            self._log("Проверка структуры CRM")
            TextDialog("Структура CRM и звонков", text, self).exec()

        run_in_background(lambda: run_crm_probe(self._src()), done)


def main():
    updated = updater.after_start()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    if not app_lock.unlock(Settings("marketing", DEFAULTS), TITLE):
        sys.exit(0)
    w = MarketingWindow()
    if updated:
        w._log("Программа обновлена", f"версия {__version__}")
    w.show()
    sys.exit(app.exec())


__all__ = ["main", "MarketingWindow", "SourceError"]
