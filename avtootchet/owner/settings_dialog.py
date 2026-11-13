"""Настройки программы владельца."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..common.crypto import new_secret
from ..common.xlsx_crypto import MIN_PASSWORD
from ..common.ui import form_layout, run_in_background, short_spin, show_start, tool_button
from .probe import run_probe
from .stock_probe import run_stock_probe
from .process_probe import run_process_probe
from .crm_probe import run_crm_probe
from ..common.text_dialog import TextDialog  # noqa: F401 - используется и снаружи
from .sources import ComSource


class OwnerSettingsDialog(QDialog):
    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(720)
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body)
        # разделы слева, содержимое справа
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(200)
        self.pages = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        # --- раздел «Подключение к 1С»
        lay = self._page("Подключение к 1С")
        g = QGroupBox("Данные 1С")
        f = form_layout(g)
        self.source = QComboBox()
        self.source.addItem("Демо — файл выгрузки учебной базы", "demo")
        self.source.addItem("1С:Далион через COM", "com")
        self.source.setCurrentIndex(0 if settings["source"] == "demo" else 1)
        f.addRow("Источник:", self.source)

        self.demo_file = QLineEdit(settings["demo_file"])
        pick = tool_button("…", "Выбрать файл выгрузки")
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
        show_start(self.demo_file, self.server, self.suppliers)
        self.btn_test = QPushButton("Проверить подключение и показать кассы")
        self.btn_test.clicked.connect(self._test)
        f.addRow(self.btn_test)
        lay.addWidget(g)

        # --- раздел «Проверки базы»
        lay = self._page("Проверки базы")
        g = QGroupBox("Проверки структуры (только чтение, без сумм)")
        f = form_layout(g)
        hint = QLabel("Показывают, как устроена база, чтобы подстроить под неё отчёты. "
                      "В результат попадают только названия и количества. Работают, когда в разделе "
                      "«Подключение к 1С» выбран источник «1С:Далион через COM».")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
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
        self.btn_process = QPushButton("Проверить данные для анализа процессов (без сумм)")
        self.btn_process.setToolTip("Журнал регистрации, статусы и даты заказ-нарядов, связи документов: "
                                    "только названия и количества, без сумм, клиентов и имён. За 6 месяцев.")
        self.btn_process.clicked.connect(self._process_probe)
        f.addRow(self.btn_process)
        self.btn_crm = QPushButton("Проверить структуру CRM и звонков (без телефонов)")
        self.btn_crm.setToolTip("Заявки, задачи, лиды, «Простые звонки»: какие есть объекты и поля, сколько записей "
                                "за 30 дней. Без телефонов, клиентов и имён.")
        self.btn_crm.clicked.connect(self._crm_probe)
        f.addRow(self.btn_crm)
        self.com_widgets = [self.server, self.base, self.user, self.password, self.platform,
                            self.organization, self.cashboxes, self.oil_groups, self.suppliers,
                            self.btn_test, self.btn_probe, self.btn_stock, self.btn_process, self.btn_crm]
        lay.addWidget(g)

        # --- раздел «Пароли»
        lay = self._page("Пароли")

        # --- пароль на вход в программу
        g = QGroupBox("Пароль на вход в программу")
        f = form_layout(g)
        from ..common import app_lock  # noqa: PLC0415
        self.lock_state = QLabel()
        self.btn_lock = QPushButton("Задать / сменить пароль…")
        self.btn_lock.clicked.connect(self._app_password)
        f.addRow(self.lock_state)
        f.addRow(self.btn_lock)
        self._lock_label = lambda: self.lock_state.setText(
            ("Пароль задан: программа спрашивает его при запуске." if app_lock.is_set(self.s)
             else "Пароль не задан: программа открывается без пароля.")
            + " Меняется сразу, кнопка «Отмена» внизу его не возвращает.")
        self.lock_state.setWordWrap(True)
        self._lock_label()
        lay.addWidget(g)

        # --- пароль отчётов
        g = QGroupBox("Пароль для отчётов")
        f = form_layout(g)
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
        lay = self._page("Склад")
        st = settings["stock"]
        g = QGroupBox("Отчёт «Залежавшиеся запчасти»")
        f = form_layout(g)
        self.stock_days = QSpinBox()
        self.stock_days.setRange(1, 365)
        self.stock_days.setValue(int(st.get("days") or 14))
        self.stock_days.setSuffix(" дн.")
        short_spin(self.stock_days)
        f.addRow("Лежат дольше:", self.stock_days)
        self.stock_months = QSpinBox()
        self.stock_months.setRange(0, 120)
        self.stock_months.setValue(int(st.get("months", 6) or 0))
        self.stock_months.setSuffix(" мес.")
        self.stock_months.setSpecialValueText("за всё время")
        short_spin(self.stock_months, 150)
        f.addRow("Приходы за последние:", self.stock_months)
        from .stock_job import stock_settings  # noqa: PLC0415
        sst = stock_settings(settings)
        self.stock_exclude = QLineEdit(sst["exclude"])
        self.stock_exclude.setPlaceholderText("слова через запятую: Расходные, Инструмент")
        show_start(self.stock_exclude)
        f.addRow("Не учитывать группы:", self.stock_exclude)
        self.stock_article = QLineEdit(st.get("article", ""))
        self.stock_article.setPlaceholderText("пусто — найти автоматически")
        f.addRow("Реквизит артикула:", self.stock_article)
        self.stock_encrypt = QCheckBox("Шифровать паролем для отчётов")
        self.stock_encrypt.setChecked(sst["encrypt"])
        f.addRow("", self.stock_encrypt)
        self.stock_folder = QLineEdit(st.get("folder", ""))
        pick = tool_button("…", "Выбрать папку")
        pick.clicked.connect(self._pick_stock_folder)
        row = QHBoxLayout()
        row.addWidget(self.stock_folder)
        row.addWidget(pick)
        row.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(row)
        f.addRow("Папка утреннего отчёта:", w)
        hint = QLabel("Группы — по словам в названии группы номенклатуры. Утренний отчёт "
                      "(задача Windows, «ACMineevo-Vladelec.exe --stock-report») кладётся в эту "
                      "папку (с паролем, если он включён); вчерашний удаляется. Список групп и что из них учтено — на листе «Группы» в самом отчёте.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
        lay.addWidget(g)

        # --- анализ процессов
        lay = self._page("Анализ процессов")
        from .process_job import process_settings  # noqa: PLC0415
        ps = process_settings(settings)
        g = QGroupBox("Отчёт «Анализ процессов» (PDF по ГОСТ 7.32)")
        f = form_layout(g)
        self.proc_months = QSpinBox()
        self.proc_months.setRange(1, 36)
        self.proc_months.setValue(ps["months"])
        self.proc_months.setSuffix(" мес.")
        short_spin(self.proc_months)
        f.addRow("Период:", self.proc_months)
        self.proc_stuck = QSpinBox()
        self.proc_stuck.setRange(1, 365)
        self.proc_stuck.setValue(ps["stuck_days"])
        self.proc_stuck.setSuffix(" дн.")
        short_spin(self.proc_stuck)
        f.addRow("ЗН считается зависшим через:", self.proc_stuck)
        self.proc_org = QLineEdit(ps["org"])
        f.addRow("Организация (титульный лист):", self.proc_org)
        self.proc_city = QLineEdit(ps["city"])
        f.addRow("Город:", self.proc_city)
        hint = QLabel("Схема процесса строится по заказ-нарядам за указанный период: даты создания, "
                      "перевода в работу, завершения, выдачи, а также приходы запчастей, выдача запчастей "
                      "и оплаты. Отчёт сохраняется туда же, куда остальные отчёты.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow(hint)
        lay.addWidget(g)

        # --- приём файлов
        lay = self._page("Приём от бухгалтера")
        g = QGroupBox("Приём кассовой книги от бухгалтера")
        f = form_layout(g)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings["port"])
        short_spin(self.port)
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
        root.addWidget(buttons)
        self._finish_pages()
        self.nav.setCurrentRow(0)

        self.source.currentIndexChanged.connect(self._toggle)
        self._toggle()

    def _page(self, title: str) -> QVBoxLayout:
        """Новый раздел настроек: пункт слева и страница справа."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pages.addWidget(page)
        self.nav.addItem(title)
        return lay

    def _finish_pages(self):
        for i in range(self.pages.count()):
            self.pages.widget(i).layout().addStretch(1)

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

    def _process_probe(self):
        self.btn_process.setEnabled(False)
        self.btn_process.setText("Проверяю… (до нескольких минут)")

        def done(text, err):
            self.btn_process.setEnabled(True)
            self.btn_process.setText("Проверить данные для анализа процессов (без сумм)")
            if err:
                QMessageBox.critical(self, "1С", str(err))
                return
            TextDialog("Данные для анализа процессов", text, self).exec()

        run_in_background(lambda: run_process_probe(self._com_source()), done)

    def _app_password(self):
        from ..common.app_lock import SetPasswordDialog  # noqa: PLC0415
        SetPasswordDialog(self.s, self).exec()
        self._lock_label()

    def _crm_probe(self):
        self.btn_crm.setEnabled(False)
        self.btn_crm.setText("Проверяю… (до нескольких минут)")

        def done(text, err):
            self.btn_crm.setEnabled(True)
            self.btn_crm.setText("Проверить структуру CRM и звонков (без телефонов)")
            if err:
                QMessageBox.critical(self, "1С", str(err))
                return
            TextDialog("Структура CRM и звонков", text, self).exec()

        run_in_background(lambda: run_crm_probe(self._com_source()), done)

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
                           "encrypt": self.stock_encrypt.isChecked(),
                           "exclude": self.stock_exclude.text().strip(),
                           "folder": self.stock_folder.text().strip()}
        self.s["process"] = {"months": self.proc_months.value(), "stuck_days": self.proc_stuck.value(),
                             "org": self.proc_org.text().strip(), "city": self.proc_city.text().strip()}
        self.s["port"] = self.port.value()
        self.s["secret"] = self.secret.text().strip()
        self.s.save()
        self.accept()
