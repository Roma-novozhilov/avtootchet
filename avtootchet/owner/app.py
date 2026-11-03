"""Программа владельца: получить данные 1С, принять книгу бухгалтера, проверить,
сгрузить правильный отчёт на флешку, удалить всё с ПК.

Данные хранятся только в памяти программы. На диск пишется лишь готовый отчёт
(на выбранную флешку), настройки и журнал без самих данных."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit, QFileDialog,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..common.crypto import new_secret, wipe
from ..common.journal import Journal
from ..common.models import CashBook, OneCData
from ..common.paths import resource
from ..common.settings import Settings
from ..common.transfer import DEFAULT_PORT, DiscoveryResponder, Receiver, local_ips
from ..common import updater
from ..common.update_ui import UpdateController
from ..common.ui import STYLE, StatusDot, error_box, money, run_in_background
from .cashbook_parser import BookParseError, parse_cashbook
from .drives import in_remote_session, removable_drives, write_to_drive
from .reconcile import ERROR, CheckResult, reconcile
from .report import build_report, report_filename
from .settings_dialog import OwnerSettingsDialog
from .sources import SourceError, make_source

DEFAULTS = {
    "source": "demo",
    "demo_file": str(resource("demo/export.json")),
    "com": {"server": "", "base": "", "user": "", "password": "",
            "cashboxes": "", "organization": "", "platform": "8.3.27.2325"},
    "port": DEFAULT_PORT,
    "secret": "",
}


class Session:
    """Всё, что получено за сеанс. Живёт только в памяти."""

    def __init__(self):
        self.onec: OneCData | None = None
        self.book: CashBook | None = None
        self.book_raw: bytearray | None = None
        self.result: CheckResult | None = None
        self.report: bytearray | None = None

    def clear_check(self):
        wipe(self.report)
        self.result, self.report = None, None

    def clear(self):
        wipe(self.book_raw)
        self.clear_check()
        self.onec = self.book = self.book_raw = None

    @property
    def has_data(self) -> bool:
        return any((self.onec, self.book, self.result))


class _Inbox(QObject):
    """Переносит принятый по сети файл из сетевого потока в поток интерфейса."""
    received = Signal(object, object, str, str)   # book, raw, sender, peer
    rejected = Signal(str, str)


class OwnerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings("owner", DEFAULTS)
        if not self.settings["secret"]:
            self.settings["secret"] = new_secret()
            self.settings.save()
        self.journal = Journal("owner")
        self.session = Session()
        self.receiver: Receiver | None = None
        self.discovery: DiscoveryResponder | None = None
        self.inbox = _Inbox()
        self.inbox.received.connect(self._on_book_received)
        self.inbox.rejected.connect(self._on_reject)

        self.setWindowTitle(f"АвтоОтчёт — владелец  v{__version__}")
        self.resize(1080, 760)
        self._build_ui()
        self._start_receiver()
        self.refresh_drives()
        self._refresh_state()
        self._log("Программа открыта")

        self.drive_timer = QTimer(self)
        self.drive_timer.timeout.connect(self.refresh_drives)
        self.drive_timer.start(5000)
        self.updates.start()

    # ============================================================ интерфейс
    def _build_ui(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Проверка кассовой книги")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        self.updates = UpdateController(self, self._before_update, self._log)
        top.addWidget(self.updates.button)
        btn_settings = QPushButton("⚙  Настройки")
        btn_settings.clicked.connect(self.open_settings)
        top.addWidget(btn_settings)
        lay.addLayout(top)

        # --- период
        g = QGroupBox("Период проверки")
        row = QHBoxLayout(g)
        today = date.today()
        self.d_from = self._date_edit(today.replace(day=1))
        self.d_to = self._date_edit(today)
        row.addWidget(QLabel("с"))
        row.addWidget(self.d_from)
        row.addWidget(QLabel("по"))
        row.addWidget(self.d_to)
        for text, fn in (("Этот месяц", self._this_month), ("Прошлый месяц", self._prev_month),
                         ("Как в книге бухгалтера", self._period_from_book)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
            if text.startswith("Как"):
                self.btn_book_period = b
        row.addStretch()
        self.d_from.dateChanged.connect(self._period_changed)
        self.d_to.dateChanged.connect(self._period_changed)
        lay.addWidget(g)

        # --- источники
        g = QGroupBox("Данные")
        grid = QVBoxLayout(g)
        r1 = QHBoxLayout()
        self.dot_1c = StatusDot()
        self.lbl_1c = QLabel()
        self.btn_load = QPushButton("Получить данные из 1С")
        self.btn_load.clicked.connect(self.load_1c)
        r1.addWidget(self.dot_1c)
        r1.addWidget(self.lbl_1c, 1)
        r1.addWidget(self.btn_load)
        grid.addLayout(r1)
        r2 = QHBoxLayout()
        self.dot_book = StatusDot()
        self.lbl_book = QLabel()
        self.lbl_book.setWordWrap(True)
        self.btn_open_book = QPushButton("Открыть файл вручную…")
        self.btn_open_book.clicked.connect(self.open_book_file)
        r2.addWidget(self.dot_book)
        r2.addWidget(self.lbl_book, 1)
        r2.addWidget(self.btn_open_book)
        grid.addLayout(r2)
        self.lbl_net = QLabel()
        self.lbl_net.setObjectName("hint")
        grid.addWidget(self.lbl_net)
        lay.addWidget(g)

        # --- проверка
        row = QHBoxLayout()
        self.btn_check = QPushButton("Проверить отчётность")
        self.btn_check.setObjectName("primary")
        self.btn_check.clicked.connect(self.run_check)
        row.addWidget(self.btn_check)
        self.lbl_verdict = QLabel()
        row.addWidget(self.lbl_verdict, 1)
        lay.addLayout(row)

        # --- вкладки
        self.tabs = QTabWidget()
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Важность", "Вид", "Дата", "Документ",
                                              "По 1С", "По книге", "Пояснение"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        for i, w in enumerate((100, 300, 90, 150, 110, 110)):
            self.table.setColumnWidth(i, w)
        h.setSectionResizeMode(6, QHeaderView.Stretch)
        self.tabs.addTab(self.table, "Расхождения")
        self.journal_view = QPlainTextEdit()
        self.journal_view.setReadOnly(True)
        self.tabs.addTab(self.journal_view, "Журнал")
        lay.addWidget(self.tabs, 1)

        # --- низ: флешка и удаление
        g = QGroupBox("Отчёт")
        row = QHBoxLayout(g)
        row.addWidget(QLabel("Флешка:"))
        self.drives = QComboBox()
        self.drives.setMinimumWidth(320)
        row.addWidget(self.drives)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(36)
        refresh.setToolTip("Обновить список флешек")
        refresh.clicked.connect(self.refresh_drives)
        row.addWidget(refresh)
        self.btn_usb = QPushButton("Сгрузить на флешку")
        self.btn_usb.clicked.connect(self.export_usb)
        row.addWidget(self.btn_usb)
        row.addStretch()
        self.btn_delete = QPushButton("Удалить отчётность")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self.delete_all)
        row.addWidget(self.btn_delete)
        lay.addWidget(g)

        note = QLabel("Данные хранятся только в памяти программы и удаляются кнопкой "
                      "«Удалить отчётность» или при закрытии окна. Из базы 1С ничего не удаляется.")
        note.setObjectName("hint")
        lay.addWidget(note)
        self.setCentralWidget(root)

    @staticmethod
    def _date_edit(d: date) -> QDateEdit:
        w = QDateEdit(QDate(d.year, d.month, d.day))
        w.setCalendarPopup(True)
        w.setDisplayFormat("dd.MM.yyyy")
        return w

    def period(self) -> tuple[date, date]:
        return self.d_from.date().toPython(), self.d_to.date().toPython()

    def _set_period(self, d1: date, d2: date):
        self.d_from.setDate(QDate(d1.year, d1.month, d1.day))
        self.d_to.setDate(QDate(d2.year, d2.month, d2.day))

    def _this_month(self):
        t = date.today()
        self._set_period(t.replace(day=1), t)

    def _prev_month(self):
        end = date.today().replace(day=1) - timedelta(days=1)
        self._set_period(end.replace(day=1), end)

    def _period_from_book(self):
        if self.session.book and self.session.book.date_range:
            self._set_period(*self.session.book.date_range)

    def _period_changed(self):
        # другой период - старые данные 1С и результат проверки больше не подходят
        if self.session.onec and self.session.onec.period != self.period():
            self.session.onec = None
        self.session.clear_check()
        self._refresh_state()

    # ============================================================ состояние
    def _refresh_state(self):
        s = self.session
        d1, d2 = self.period()
        if s.onec:
            self.dot_1c.set("green")
            self.lbl_1c.setText(f"1С: {len(s.onec.ops)} кассовых операций за {d1:%d.%m.%Y}–{d2:%d.%m.%Y} "
                                f"· получено в {s.onec.loaded_at:%H:%M}")
        else:
            self.dot_1c.set("grey")
            self.lbl_1c.setText(f"1С: данные не загружены ({make_source(self.settings).name})")
        if s.book:
            rng = s.book.date_range
            rng_text = f" за {rng[0]:%d.%m.%Y}–{rng[1]:%d.%m.%Y}" if rng else ""
            self.dot_book.set("green")
            self.lbl_book.setText(f"Кассовая книга бухгалтера: «{s.book.filename}», "
                                  f"{len(s.book.entries)} записей{rng_text} · получена "
                                  f"{s.book.received_at:%d.%m %H:%M}")
        else:
            self.dot_book.set("yellow" if self.receiver else "grey")
            self.lbl_book.setText("Кассовая книга бухгалтера: ожидается…")
        self.btn_book_period.setEnabled(bool(s.book))
        self.btn_check.setEnabled(bool(s.onec and s.book))
        self.btn_usb.setEnabled(bool(s.report) and self.drives.count() > 0
                                and self.drives.currentData() is not None)
        self.btn_delete.setEnabled(s.has_data)
        if not s.result:
            self.table.setRowCount(0)
            self.lbl_verdict.setText("")
        self.journal_view.setPlainText("\n".join(self.journal.tail()))
        self.journal_view.verticalScrollBar().setValue(self.journal_view.verticalScrollBar().maximum())

    def _log(self, action: str, details: str = ""):
        self.journal.write(action, details)
        self.journal_view.setPlainText("\n".join(self.journal.tail()))
        self.journal_view.verticalScrollBar().setValue(self.journal_view.verticalScrollBar().maximum())

    # ============================================================ сеть
    def _start_receiver(self):
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        port, secret = self.settings["port"], self.settings["secret"]

        def on_file(header: dict, payload: bytes, peer: str) -> str:
            name = header.get("filename", "кассовая книга.xlsx")
            try:
                book = parse_cashbook(payload, name)
            except BookParseError as e:
                self.inbox.rejected.emit(peer, str(e))
                raise
            self.inbox.received.emit(book, bytearray(payload), header.get("sender", peer), peer)
            rng = book.date_range
            period = f" за {rng[0]:%d.%m.%Y}–{rng[1]:%d.%m.%Y}" if rng else ""
            return f"Владелец получил кассовую книгу: {len(book.entries)} записей{period}"

        self.receiver = Receiver(port, secret, on_file,
                                 lambda peer, msg: self.inbox.rejected.emit(peer, msg))
        try:
            self.receiver.start()
        except OSError as e:
            self.receiver = None
            self.lbl_net.setText(f"⚠ Приём по сети не запущен: порт {port} занят ({e}). "
                                 "Смените порт в настройках.")
            return
        ips = ", ".join(local_ips()) or "адрес не определён"
        auto = ""
        try:
            self.discovery = DiscoveryResponder(port, secret)
            self.discovery.start()
            auto = " · бухгалтер найдёт этот ПК автоматически"
        except OSError:
            self.discovery = None   # автопоиск недоступен - бухгалтер введёт адрес вручную
        self.lbl_net.setText(f"Приём книги по сети включён · адрес этого ПК: {ips} · порт {port}{auto}")

    def _on_book_received(self, book: CashBook, raw: bytearray, sender: str, peer: str):
        replace = self.session.book is not None
        wipe(self.session.book_raw)
        self.session.book, self.session.book_raw = book, raw
        self.session.clear_check()
        self._log("Получена кассовая книга по сети",
                  f"от {sender} ({peer}), записей: {len(book.entries)}" + (", заменила предыдущую" if replace else ""))
        if not self.session.onec and book.date_range:
            self._set_period(*book.date_range)
        self._refresh_state()
        self.statusBar().showMessage(f"Получена кассовая книга от {sender}", 8000)
        QApplication.alert(self)

    def _on_reject(self, peer: str, msg: str):
        self._log("Отклонён файл по сети", f"с {peer}: {msg.splitlines()[0][:120]}")

    # ============================================================ действия
    def open_settings(self):
        old = (self.settings["port"], self.settings["secret"])
        if OwnerSettingsDialog(self.settings, self).exec():
            self._log("Изменены настройки")
            if (self.settings["port"], self.settings["secret"]) != old:
                self._start_receiver()
            self.session.onec = None
            self.session.clear_check()
            self._refresh_state()

    def load_1c(self):
        d1, d2 = self.period()
        if d1 > d2:
            error_box(self, "Период", "Дата начала больше даты окончания.")
            return
        source = make_source(self.settings)
        self.btn_load.setEnabled(False)
        self.btn_load.setText("Получаю…")
        self.dot_1c.set("blue")

        def done(result, err):
            self.btn_load.setEnabled(True)
            self.btn_load.setText("Получить данные из 1С")
            if err:
                self.dot_1c.set("red")
                self.lbl_1c.setText(f"1С: ошибка — {err}")
                self._log("Ошибка получения данных 1С", type(err).__name__)
                if not isinstance(err, SourceError):
                    error_box(self, "1С", f"Не удалось получить данные: {err}")
                return
            self.session.onec = result
            self.session.clear_check()
            self._log("Получены данные 1С",
                      f"период {d1:%d.%m.%Y}–{d2:%d.%m.%Y}, операций: {len(result.ops)}")
            self._refresh_state()

        run_in_background(lambda: source.load(d1, d2), done)

    def open_book_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Кассовая книга бухгалтера", "",
                                              "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            raw = bytearray(Path(path).read_bytes())
            book = parse_cashbook(bytes(raw), Path(path).name)
        except (OSError, BookParseError) as e:
            error_box(self, "Кассовая книга", str(e))
            return
        wipe(self.session.book_raw)
        self.session.book, self.session.book_raw = book, raw
        self.session.clear_check()
        self._log("Кассовая книга открыта вручную", f"записей: {len(book.entries)}")
        if not self.session.onec and book.date_range:
            self._set_period(*book.date_range)
        self._refresh_state()

    def run_check(self):
        s = self.session
        if not (s.onec and s.book):
            return
        res = reconcile(s.onec, s.book)
        s.result = res
        s.report = bytearray(build_report(res))
        self._fill_table(res)
        if res.is_clean:
            self.lbl_verdict.setObjectName("verdict_ok")
            self.lbl_verdict.setText("✓  Кассовая книга совпадает с 1С"
                                     + (f" (предупреждений: {res.warnings})" if res.warnings else ""))
        else:
            self.lbl_verdict.setObjectName("verdict_bad")
            self.lbl_verdict.setText(f"✗  Найдено ошибок: {res.errors}, предупреждений: {res.warnings}")
        self.lbl_verdict.style().unpolish(self.lbl_verdict)
        self.lbl_verdict.style().polish(self.lbl_verdict)
        self._log("Проверка выполнена", f"ошибок: {res.errors}, предупреждений: {res.warnings}")
        self.tabs.setCurrentIndex(0)
        self._refresh_state()

    def _fill_table(self, res: CheckResult):
        self.table.setRowCount(0)
        rows = res.items or []
        self.table.setRowCount(max(1, len(rows)))
        if not rows:
            self.table.setItem(0, 1, QTableWidgetItem("Расхождений нет"))
            return
        err_bg, warn_bg = QColor("#fde2e1"), QColor("#fff4d6")
        for r, d in enumerate(rows):
            values = [d.severity, d.category, f"{d.date:%d.%m.%Y}" if d.date else "", d.document,
                      money(d.amount_1c), money(d.amount_book), d.comment]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setBackground(err_bg if d.severity == ERROR else warn_bg)
                if c in (4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setToolTip(v)
                self.table.setItem(r, c, item)

    def refresh_drives(self):
        """Поиск флешек в фоне: проверка дисков удалённого стола может занять пару секунд."""
        if getattr(self, "_drives_busy", False):
            return
        self._drives_busy = True

        def done(drives, err):
            self._drives_busy = False
            if err is None:
                self._apply_drives(drives)

        run_in_background(removable_drives, done)

    def _apply_drives(self, drives):
        current = self.drives.currentData()
        roots = [d.root for d in drives]
        if roots == [self.drives.itemData(i) for i in range(self.drives.count())]:
            return
        self.drives.clear()
        for d in drives:
            self.drives.addItem(d.title, d.root)
        if not drives:
            hint = ("Флешка не видна: включите «Диски» в настройках удалённого стола"
                    if in_remote_session() else "Флешка не вставлена")
            self.drives.addItem(hint, None)
        elif current in roots:
            self.drives.setCurrentIndex(roots.index(current))
        self._refresh_state()

    def export_usb(self):
        s = self.session
        root = self.drives.currentData()
        if not (s.report and s.result and root):
            return
        if not s.result.is_clean:
            ans = QMessageBox.question(
                self, "Есть расхождения",
                f"Найдено ошибок: {s.result.errors}. В отчёт попадут правильная кассовая книга "
                "по данным 1С и список всех расхождений.\n\nСгрузить на флешку?")
            if ans != QMessageBox.Yes:
                return
        try:
            target = write_to_drive(root, report_filename(s.result), bytes(s.report))
        except OSError as e:
            error_box(self, "Флешка", f"Не удалось записать отчёт: {e}")
            return
        self._log("Отчёт сгружен на флешку", f"{root} {target.name}")
        QMessageBox.information(self, "Готово", f"Отчёт записан на флешку:\n{target}\n\n"
                                "Когда закончите, нажмите «Удалить отчётность», чтобы стереть "
                                "данные с этого ПК.")

    def delete_all(self, ask: bool = True):
        if ask and QMessageBox.question(
                self, "Удалить отчётность",
                "Удалить из программы данные 1С, кассовую книгу бухгалтера и результат проверки?\n\n"
                "База 1С и файл на ПК бухгалтера не затрагиваются.") != QMessageBox.Yes:
            return
        self.session.clear()
        self._log("Отчётность удалена с ПК")
        self._refresh_state()

    def _before_update(self) -> bool:
        if not self.session.has_data:
            return True
        return QMessageBox.question(
            self, "Обновление",
            "В программе есть загруженные данные. При обновлении они будут удалены из памяти "
            "(как при закрытии). Продолжить?") == QMessageBox.Yes

    def closeEvent(self, event):
        had = self.session.has_data
        self.session.clear()
        if self.receiver:
            self.receiver.stop()
        if self.discovery:
            self.discovery.stop()
        self.journal.write("Программа закрыта" + (", данные удалены из памяти" if had else ""))
        super().closeEvent(event)


def main():
    updated = updater.after_start()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = OwnerWindow()
    if updated:
        w._log("Программа обновлена", f"версия {__version__}")
    w.show()
    sys.exit(app.exec())
