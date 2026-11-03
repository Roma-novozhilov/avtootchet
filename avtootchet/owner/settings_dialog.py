"""Настройки программы владельца."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..common.crypto import new_secret
from ..common.ui import run_in_background
from .sources import ComSource


class OwnerSettingsDialog(QDialog):
    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)

        # --- источник данных
        g = QGroupBox("Данные 1С")
        f = QFormLayout(g)
        self.source = QComboBox()
        self.source.addItem("Демо — файл выгрузки учебной базы", "demo")
        self.source.addItem("1С:Далион через COM", "com")
        self.source.setCurrentIndex(0 if settings["source"] == "demo" else 1)
        f.addRow("Источник:", self.source)

        self.demo_file = QLineEdit(settings["demo_file"])
        pick = QPushButton("…")
        pick.setFixedWidth(32)
        pick.clicked.connect(self._pick_demo)
        row = QHBoxLayout()
        row.addWidget(self.demo_file)
        row.addWidget(pick)
        self.demo_row = QWidget()
        self.demo_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        f.addRow("Файл выгрузки:", self.demo_row)

        com = settings["com"]
        self.server = QLineEdit(com["server"])
        self.server.setPlaceholderText(r"\\SERVER\1C\Dalion  (или имя сервера 1С)")
        self.base = QLineEdit(com["base"])
        self.base.setPlaceholderText("только для серверной базы; для файловой пусто")
        self.user = QLineEdit(com["user"])
        self.user.setPlaceholderText("пользователь 1С только для чтения")
        self.password = QLineEdit(com["password"])
        self.password.setEchoMode(QLineEdit.Password)
        self.organization = QLineEdit(com.get("organization", ""))
        self.organization.setPlaceholderText("пусто = все организации")
        self.platform = QLineEdit(com.get("platform", "8.3.27.2325"))
        self.platform.setPlaceholderText("версия платформы базы, например 8.3.27.2325")
        self.cashboxes = QLineEdit(com.get("cashboxes", ""))
        self.cashboxes.setPlaceholderText("через запятую; пусто = все кассы")
        for label, w in (("Путь к базе / сервер:", self.server), ("База на сервере:", self.base),
                         ("Пользователь:", self.user), ("Пароль:", self.password),
                         ("Версия платформы:", self.platform),
                         ("Организация:", self.organization), ("Кассы:", self.cashboxes)):
            f.addRow(label, w)
        self.btn_test = QPushButton("Проверить подключение и показать кассы")
        self.btn_test.clicked.connect(self._test)
        f.addRow(self.btn_test)
        self.com_widgets = [self.server, self.base, self.user, self.password, self.platform,
                            self.organization, self.cashboxes, self.btn_test]
        lay.addWidget(g)

        # --- приём файлов
        g = QGroupBox("Приём кассовой книги от бухгалтера")
        f = QFormLayout(g)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings["port"])
        f.addRow("Порт:", self.port)
        self.secret = QLineEdit(settings["secret"])
        gen = QPushButton("Новый код")
        gen.clicked.connect(lambda: self.secret.setText(new_secret()))
        row = QHBoxLayout()
        row.addWidget(self.secret)
        row.addWidget(gen)
        row.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(row)
        f.addRow("Код доступа:", w)
        hint = QLabel("Этот же код нужно ввести в настройках программы бухгалтера. "
                      "Файлы с другим кодом не принимаются.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
        lay.addWidget(g)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.source.currentIndexChanged.connect(self._toggle)
        self._toggle()

    def _toggle(self):
        is_com = self.source.currentData() == "com"
        self.demo_row.setEnabled(not is_com)
        for w in self.com_widgets:
            w.setEnabled(is_com)

    def _com_source(self) -> ComSource:
        return ComSource(self.server.text().strip(), self.base.text().strip(),
                         self.user.text().strip(), self.password.text(),
                         self.cashboxes.text(), self.organization.text(), self.platform.text())

    def _test(self):
        self.btn_test.setEnabled(False)
        self.btn_test.setText("Подключаюсь к 1С…")

        def done(boxes, err):
            self.btn_test.setEnabled(True)
            self.btn_test.setText("Проверить подключение и показать кассы")
            if err:
                QMessageBox.critical(self, "1С", str(err))
                return
            if not boxes:
                QMessageBox.information(self, "1С", "Подключение работает, но касс не найдено.")
                return
            lines = "\n".join(f"• {box}  ({org})" for box, org in boxes)
            QMessageBox.information(
                self, "1С: подключение работает",
                f"Кассы в базе:\n\n{lines}\n\nДля кассовой книги одной организации "
                "впишите её название в поле «Организация» (или нужные кассы в «Кассы»).")

        run_in_background(lambda: self._com_source().list_cashboxes(), done)

    def _pick_demo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл выгрузки", self.demo_file.text(),
                                              "Выгрузка (*.json)")
        if path:
            self.demo_file.setText(path)

    def _save(self):
        self.s["source"] = self.source.currentData()
        self.s["demo_file"] = self.demo_file.text().strip()
        self.s["com"] = {"server": self.server.text().strip(), "base": self.base.text().strip(),
                         "user": self.user.text().strip(), "password": self.password.text(),
                         "cashboxes": self.cashboxes.text().strip(),
                         "platform": self.platform.text().strip(),
                         "organization": self.organization.text().strip()}
        self.s["port"] = self.port.value()
        self.s["secret"] = self.secret.text().strip()
        self.s.save()
        self.accept()
