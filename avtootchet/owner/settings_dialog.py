"""Настройки программы владельца."""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..common.crypto import new_secret
from ..common.xlsx_crypto import MIN_PASSWORD
from ..common.ui import run_in_background
from .probe import run_probe
from .stock_probe import run_stock_probe
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
        self.oil_groups = QLineEdit(com.get("oil_groups", "Масло"))
        self.oil_groups.setPlaceholderText("группы номенклатуры через запятую, например: Масло")
        self.suppliers = QLineEdit(com.get("main_suppliers", ""))
        self.suppliers.setPlaceholderText("через запятую: Берг, Вюрт, …")
        self.cashboxes = QLineEdit(com.get("cashboxes", ""))
        self.cashboxes.setPlaceholderText("через запятую; пусто = все кассы")
        for label, w in (("Путь к базе / сервер:", self.server), ("База на сервере:", self.base),
                         ("Пользователь:", self.user), ("Пароль:", self.password),
                         ("Версия платформы:", self.platform),
                         ("Организация:", self.organization), ("Кассы:", self.cashboxes),
                         ("Группы масла:", self.oil_groups), ("Основные поставщики:", self.suppliers)):
            f.addRow(label, w)
        self.btn_test = QPushButton("Проверить подключение и показать кассы")
        self.btn_test.clicked.connect(self._test)
        f.addRow(self.btn_test)
        self.btn_probe = QPushButton("Проверить структуру базы (без сумм)")
        self.btn_probe.setToolTip("Как в этой базе устроены оплаты: только количества и названия, "
                                  "без сумм и клиентов. За последние 31 день.")
        self.btn_probe.clicked.connect(self._probe)
        f.addRow(self.btn_probe)
        self.btn_stock = QPushButton("Проверить структуру склада (без сумм)")
        self.btn_stock.setToolTip("Какие документы двигают запчасти и где в них ответственный: "
                                  "только названия и количества, без сумм и номенклатуры. За 31 день.")
        self.btn_stock.clicked.connect(self._stock_probe)
        f.addRow(self.btn_stock)
        self.com_widgets = [self.server, self.base, self.user, self.password, self.platform,
                            self.organization, self.cashboxes, self.oil_groups, self.suppliers,
                            self.btn_test, self.btn_probe, self.btn_stock]
        lay.addWidget(g)

        # --- пароль отчётов
        g = QGroupBox("Пароль для отчётов")
        f = QFormLayout(g)
        self.rep_pass = QLineEdit(settings["report_password"])
        self.rep_pass.setEchoMode(QLineEdit.Password)
        self.rep_pass2 = QLineEdit(settings["report_password"])
        self.rep_pass2.setEchoMode(QLineEdit.Password)
        show = QCheckBox("Показать")
        show.toggled.connect(lambda on: [w.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
                                         for w in (self.rep_pass, self.rep_pass2)])
        f.addRow("Пароль:", self.rep_pass)
        f.addRow("Ещё раз:", self.rep_pass2)
        f.addRow(show)
        hint = QLabel(f"Каждый сохранённый отчёт шифруется этим паролем (AES-256): Excel спросит его "
                      f"при открытии. Не меньше {MIN_PASSWORD} символов. Если пароль забыть, "
                      "отчёт не открыть — запишите его в надёжном месте.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
        lay.addWidget(g)

        # --- утренний отчёт по складу
        st = settings["stock"]
        g = QGroupBox("Отчёт «Залежавшиеся запчасти»")
        f = QFormLayout(g)
        self.stock_days = QSpinBox()
        self.stock_days.setRange(1, 365)
        self.stock_days.setValue(int(st.get("days") or 14))
        self.stock_days.setSuffix(" дн.")
        f.addRow("Лежат дольше:", self.stock_days)
        self.stock_months = QSpinBox()
        self.stock_months.setRange(0, 120)
        self.stock_months.setValue(int(st.get("months", 6) or 0))
        self.stock_months.setSuffix(" мес.")
        self.stock_months.setSpecialValueText("за всё время")
        f.addRow("Приходы за последние:", self.stock_months)
        self.stock_exclude = QLineEdit(st.get("exclude", ""))
        self.stock_exclude.setPlaceholderText("слова через запятую: Масло, Фильтр")
        f.addRow("Не учитывать группы:", self.stock_exclude)
        self.stock_article = QLineEdit(st.get("article", ""))
        self.stock_article.setPlaceholderText("пусто — найти автоматически")
        f.addRow("Реквизит артикула:", self.stock_article)
        self.stock_folder = QLineEdit(st.get("folder", ""))
        pick = QPushButton("…")
        pick.setFixedWidth(32)
        pick.clicked.connect(self._pick_stock_folder)
        row = QHBoxLayout()
        row.addWidget(self.stock_folder)
        row.addWidget(pick)
        row.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(row)
        f.addRow("Папка утреннего отчёта:", w)
        hint = QLabel("Группы — по словам в названии группы номенклатуры. Утренний отчёт "
                      "(задача Windows, «AvtoOtchet-Vladelec.exe --stock-report») кладётся в эту "
                      "папку с паролем для отчётов; вчерашний удаляется.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
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
                         self.cashboxes.text(), self.organization.text(), self.platform.text(),
                         self.oil_groups.text(), self.suppliers.text())

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
            lines = "\n".join(f"• {box}  ({org})" if org else f"• {box}" for box, org in boxes)
            QMessageBox.information(
                self, "1С: подключение работает",
                f"Кассы в базе:\n\n{lines}\n\nДля кассовой книги одной организации "
                "впишите её название в поле «Организация» (или нужные кассы в «Кассы»).")

        run_in_background(lambda: self._com_source().list_cashboxes(), done)

    def _probe(self):
        self.btn_probe.setEnabled(False)
        self.btn_probe.setText("Проверяю базу… (до минуты)")

        def done(text, err):
            self.btn_probe.setEnabled(True)
            self.btn_probe.setText("Проверить структуру базы (без сумм)")
            if err:
                QMessageBox.critical(self, "1С", str(err))
                return
            TextDialog("Структура базы", text, self).exec()

        run_in_background(lambda: run_probe(self._com_source()), done)

    def _stock_probe(self):
        self.btn_stock.setEnabled(False)
        self.btn_stock.setText("Проверяю склад… (до пары минут)")

        def done(text, err):
            self.btn_stock.setEnabled(True)
            self.btn_stock.setText("Проверить структуру склада (без сумм)")
            if err:
                QMessageBox.critical(self, "1С", str(err))
                return
            TextDialog("Структура склада", text, self).exec()

        run_in_background(lambda: run_stock_probe(self._com_source()), done)

    def _pick_stock_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Папка утреннего отчёта", self.stock_folder.text())
        if path:
            self.stock_folder.setText(path.replace("/", "\\"))

    def _pick_demo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл выгрузки", self.demo_file.text(),
                                              "Выгрузка (*.json)")
        if path:
            self.demo_file.setText(path)

    def _save(self):
        pw, pw2 = self.rep_pass.text(), self.rep_pass2.text()
        if pw != pw2:
            QMessageBox.warning(self, "Пароль для отчётов", "Пароли не совпадают.")
            return
        if pw and len(pw) < MIN_PASSWORD:
            QMessageBox.warning(self, "Пароль для отчётов",
                                f"Пароль слишком короткий: нужно не меньше {MIN_PASSWORD} символов.")
            return
        self.s["report_password"] = pw
        self.s["source"] = self.source.currentData()
        self.s["demo_file"] = self.demo_file.text().strip()
        self.s["com"] = {"server": self.server.text().strip(), "base": self.base.text().strip(),
                         "user": self.user.text().strip(), "password": self.password.text(),
                         "cashboxes": self.cashboxes.text().strip(),
                         "oil_groups": self.oil_groups.text().strip() or "Масло",
                         "main_suppliers": self.suppliers.text().strip(),
                         "platform": self.platform.text().strip(),
                         "organization": self.organization.text().strip()}
        self.s["stock"] = {"days": self.stock_days.value(), "months": self.stock_months.value(),
                           "article": self.stock_article.text().strip(),
                           "exclude": self.stock_exclude.text().strip(),
                           "folder": self.stock_folder.text().strip()}
        self.s["port"] = self.port.value()
        self.s["secret"] = self.secret.text().strip()
        self.s.save()
        self.accept()


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
