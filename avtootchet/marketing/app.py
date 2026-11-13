"""ACMineevo — маркетинг. Отдельная программа для маркетолога: только CRM, заявки, задачи
и «Простые звонки». Ни кассы, ни выручки, ни склада здесь нет - их код в эту программу не входит.

Подключается к Далиону под пользователем маркетолога (его права в Далионе - вторая защита),
только чтение. Свои настройки (%APPDATA%\\AvtoOtchet\\marketing_settings.json, пароль 1С
зашифрован DPAPI) и свой пароль на вход.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QApplication, QCheckBox, QDateEdit, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..common import app_lock, updater
from ..common.journal import Journal
from ..common.settings import Settings
from ..common.ui import STYLE, form_layout, run_in_background, short_spin, show_start, tool_button
from ..common.update_ui import UpdateController
from ..owner.crm_probe import run_crm_probe
from ..common.text_dialog import TextDialog
from ..owner.sources import ComSource, SourceError
from .export import Directions, load_export, save_export

TITLE = "ACMineevo — маркетинг"
DEFAULTS = {
    "com": {"server": r"C:\1C\Dalion", "base": "", "user": "", "password": "", "platform": ""},
    "app_lock": {},
    # выгрузка для витрины. Коды «НаправлениеЗвонка» Далиона: 0 - входящий, 1 - исходящий
    # (по первой выгрузке 07-13.11.2026: у кода 0 городская линия и почти все пропущенные)
    "export": {"folder": "", "csv": True, "json": True, "phones": False, "in_codes": "0", "out_codes": "1"},
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
        self.resize(720, 760)
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
        btn_pw = QPushButton("🔒  Пароль программы…")
        btn_pw.clicked.connect(lambda: app_lock.SetPasswordDialog(self.settings, self).exec())
        top.addWidget(btn_pw)
        lay.addLayout(top)

        c = self.settings["com"]
        g = QGroupBox("Подключение к 1С:Далион (под вашим пользователем Далиона)")
        f = form_layout(g)
        self.server = QLineEdit(c["server"])
        self.user = QLineEdit(c["user"])
        self.password = QLineEdit(c["password"])
        self.password.setEchoMode(QLineEdit.Password)
        self.platform = QLineEdit(c["platform"])
        self.platform.setPlaceholderText("например 8.3.27.2325; пусто — как установлено")
        f.addRow("Путь к базе:", self.server)
        f.addRow("Пользователь Далиона:", self.user)
        f.addRow("Пароль Далиона:", self.password)
        f.addRow("Версия платформы:", self.platform)
        show_start(self.server)
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

        lay.addWidget(self._export_box())

        g = QGroupBox("Проверка структуры (если что-то в выгрузке не так)")
        v = QVBoxLayout(g)
        self.btn_crm = QPushButton("Проверить структуру CRM и звонков (без телефонов)")
        self.btn_crm.clicked.connect(self._crm)
        row = QHBoxLayout()
        row.addWidget(self.btn_crm)
        row.addStretch()
        v.addLayout(row)
        hint = QLabel("Какие в Далионе есть заявки, задачи, лиды и звонки и как заполнены их поля за 30 дней — "
                      "только названия и количества. Результат можно скопировать и отправить разработчику.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        lay.addWidget(g)
        note = QLabel("Программа только читает данные Далиона и ничего в нём не меняет.")
        note.setObjectName("hint")
        lay.addWidget(note)
        lay.addStretch()
        self.setCentralWidget(root)
        self._log("Программа открыта")

    # ------------------------------------------------------------ выгрузка
    def _export_box(self) -> QGroupBox:
        e = self.settings["export"]
        g = QGroupBox("Выгрузка для витрины (CSV / JSON)")
        f = form_layout(g)
        row = QHBoxLayout()
        self.d1, self.d2 = QDateEdit(), QDateEdit()
        for w in (self.d1, self.d2):
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd.MM.yyyy")
            short_spin(w)
        self._set_period(7)
        row.addWidget(self.d1)
        row.addWidget(QLabel("—"))
        row.addWidget(self.d2)
        for text, days in (("Вчера", 1), ("7 дней", 7), ("30 дней", 30)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, d=days: self._set_period(d))
            row.addWidget(b)
        row.addStretch()
        f.addRow("Период:", row)
        frow = QHBoxLayout()
        self.folder = QLineEdit(e.get("folder") or str(Path.home() / "Documents" / "Выгрузки CRM"))
        show_start(self.folder)
        pick = tool_button("…", "Выбрать папку")
        pick.clicked.connect(self._pick_folder)
        frow.addWidget(self.folder, 1)
        frow.addWidget(pick)
        f.addRow("Папка:", frow)
        opts = QHBoxLayout()
        self.fmt_csv = QCheckBox("CSV (Excel)")
        self.fmt_csv.setChecked(bool(e.get("csv", True)))
        self.fmt_json = QCheckBox("JSON")
        self.fmt_json.setChecked(bool(e.get("json", True)))
        self.phones = QCheckBox("с номерами телефонов")
        self.phones.setChecked(bool(e.get("phones", False)))
        for w in (self.fmt_csv, self.fmt_json, self.phones):
            opts.addWidget(w)
        opts.addStretch()
        f.addRow("Формат:", opts)
        codes = QHBoxLayout()
        # пустые коды из старых настроек (до 0.8.2) - берём известные
        self.in_codes = QLineEdit(e.get("in_codes") or DEFAULTS["export"]["in_codes"])
        self.out_codes = QLineEdit(e.get("out_codes") or DEFAULTS["export"]["out_codes"])
        for w, label in ((self.in_codes, "входящие:"), (self.out_codes, "исходящие:")):
            w.setMaximumWidth(80)
            w.setToolTip("Код «НаправлениеЗвонка» в «Простых звонках». В Далионе: 0 — входящий, 1 — исходящий.")
            codes.addWidget(QLabel(label))
            codes.addWidget(w)
        codes.addStretch()
        f.addRow("Коды звонков:", codes)
        self.btn_export = QPushButton("Выгрузить")
        self.btn_export.setObjectName("primary")
        self.btn_export.clicked.connect(self._export)
        f.addRow(self.btn_export)
        hint = QLabel("Файлы: звонки (пропущен, перезвонили и через сколько), задачи (выполнено, просрочено), "
                      "заявки — заказ-наряды без сумм, клиенты, менеджеры. Номера телефонов — только если "
                      "отмечено: это персональные данные клиентов, в облачные сервисы их без согласия не "
                      "загружать.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
        return g

    def _set_period(self, days: int):
        end = date.today() - timedelta(days=1) if days == 1 else date.today()
        start = end - timedelta(days=days - 1)
        self.d1.setDate(QDate(start.year, start.month, start.day))
        self.d2.setDate(QDate(end.year, end.month, end.day))

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для выгрузки", self.folder.text())
        if d:
            self.folder.setText(d)

    def _export(self):
        d1, d2 = self.d1.date().toPython(), self.d2.date().toPython()
        if d1 > d2:
            QMessageBox.warning(self, TITLE, "Дата «с» позже даты «по».")
            return
        if not (self.fmt_csv.isChecked() or self.fmt_json.isChecked()):
            QMessageBox.warning(self, TITLE, "Отметьте хотя бы один формат: CSV или JSON.")
            return
        self.settings["export"] = {"folder": self.folder.text().strip(), "csv": self.fmt_csv.isChecked(),
                                   "json": self.fmt_json.isChecked(), "phones": self.phones.isChecked(),
                                   "in_codes": self.in_codes.text().strip(),
                                   "out_codes": self.out_codes.text().strip()}
        self.settings.save()
        dirs = Directions.parse(self.in_codes.text(), self.out_codes.text())
        folder = Path(self.folder.text().strip())
        src = self._src()
        csv_on, json_on, phones = self.fmt_csv.isChecked(), self.fmt_json.isChecked(), self.phones.isChecked()
        self._busy(self.btn_export, "Выгружаю…")

        def work():
            src._check_settings()
            ex = load_export(src._connect(), d1, d2, dirs, phones)
            return ex, save_export(ex, folder, csv_on, json_on)

        def done(result, err):
            self._busy(self.btn_export, None)
            if err:
                QMessageBox.critical(self, "Выгрузка", str(err))
                self._log("Выгрузка CRM: ошибка", type(err).__name__)
                return
            ex, files = result
            missed = sum(1 for c in ex.calls if c["пропущен"] == "да")
            self._log("Выгрузка CRM", f"{d1:%d.%m.%Y}-{d2:%d.%m.%Y}: звонков {len(ex.calls)}")
            box = QMessageBox(self)
            box.setWindowTitle("Выгрузка готова")
            box.setText(f"Звонков: {len(ex.calls)} (пропущено {missed}), задач: {len(ex.tasks)}, "
                        f"заявок: {len(ex.requests)}.\n\nПапка: {files[0].parent if files else folder}")
            open_btn = box.addButton("Открыть папку", QMessageBox.AcceptRole)
            box.addButton("Закрыть", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn and files and hasattr(os, "startfile"):
                os.startfile(files[0].parent)  # noqa: S606 - своя папка с выгрузкой

        run_in_background(work, done)

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
