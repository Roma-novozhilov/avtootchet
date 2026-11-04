"""Программа бухгалтера: только отправка кассовой книги на ПК владельца.

Исходный файл бухгалтера не трогается. Программа копирует его во временную
папку, отправляет зашифрованным и сразу удаляет эту копию."""
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import __version__
from ..common.crypto import secure_delete
from ..common.journal import Journal
from ..common.paths import temp_dir
from ..common.settings import Settings
from ..common.transfer import DEFAULT_PORT, TransferError, discover_owner, send_file
from ..common import updater
from ..common.update_ui import UpdateController
from ..common.ui import STYLE, StatusDot, run_in_background

DEFAULTS = {"owner_host": "", "port": DEFAULT_PORT, "secret": "", "last_file": ""}


class AccountantSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        g = QGroupBox("ПК владельца")
        f = QFormLayout(g)
        self.host = QLineEdit(settings["owner_host"])
        self.host.setPlaceholderText("пусто = найти автоматически")
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(settings["port"])
        self.secret = QLineEdit(settings["secret"])
        self.secret.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.btn_find = QPushButton("Найти")
        self.btn_find.setToolTip("Найти программу владельца в сети (нужен код доступа)")
        self.btn_find.clicked.connect(self._find)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.host)
        row.addWidget(self.btn_find)
        host_row = QWidget()
        host_row.setLayout(row)
        f.addRow("Код доступа:", self.secret)
        f.addRow("Адрес:", host_row)
        f.addRow("Порт:", self.port)
        hint = QLabel("Достаточно ввести код доступа из настроек программы владельца: "
                      "адрес программа найдёт сама (кнопка «Найти» или при отправке).")
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

    def _find(self):
        secret = self.secret.text().strip()
        if not secret:
            QMessageBox.information(self, "Поиск", "Сначала введите код доступа.")
            return
        self.btn_find.setEnabled(False)
        self.btn_find.setText("Ищу…")

        def done(found, err):
            self.btn_find.setEnabled(True)
            self.btn_find.setText("Найти")
            if err or not found:
                QMessageBox.warning(self, "Поиск", "Программа владельца не найдена. Проверьте, что она "
                                    "открыта и код доступа совпадает. Можно ввести адрес вручную.")
                return
            self.host.setText(found[0]["ip"])
            self.port.setValue(found[0]["port"])
            if len(found) > 1:
                others = ", ".join(f"{x['host']} ({x['ip']})" for x in found)
                QMessageBox.information(self, "Поиск", f"Найдено несколько программ владельца: {others}. "
                                        "Выбрана первая.")

        run_in_background(lambda: discover_owner(secret), done)

    def _save(self):
        self.s["owner_host"] = self.host.text().strip()
        self.s["port"] = self.port.value()
        self.s["secret"] = self.secret.text().strip()
        self.s.save()
        self.accept()


class AccountantWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings("accountant", DEFAULTS)
        self.journal = Journal("accountant")
        self.setWindowTitle(f"АвтоОтчёт — бухгалтер  v{__version__}")
        self.resize(640, 360)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        top = QHBoxLayout()
        title = QLabel("Отправка кассовой книги")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        self.updates = UpdateController(self, log=self.journal.write)
        top.addWidget(self.updates.button)
        b = QPushButton("⚙  Настройки")
        b.clicked.connect(self.open_settings)
        top.addWidget(b)
        lay.addLayout(top)

        g = QGroupBox("Файл кассовой книги (Excel)")
        row = QHBoxLayout(g)
        self.path = QLineEdit(self.settings["last_file"])
        self.path.setPlaceholderText("Выберите файл .xlsx")
        self.path.textChanged.connect(self._update)
        pick = QPushButton("Выбрать…")
        pick.clicked.connect(self.pick_file)
        row.addWidget(self.path, 1)
        row.addWidget(pick)
        lay.addWidget(g)

        self.lbl_target = QLabel()
        self.lbl_target.setObjectName("hint")
        lay.addWidget(self.lbl_target)

        self.btn_send = QPushButton("Отправить владельцу")
        self.btn_send.setObjectName("primary")
        self.btn_send.clicked.connect(self.send)
        lay.addWidget(self.btn_send)

        st = QHBoxLayout()
        self.dot = StatusDot()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        st.addWidget(self.dot, 0, Qt.AlignTop)
        st.addWidget(self.status, 1)
        lay.addLayout(st)
        lay.addStretch()
        note = QLabel("Ваш файл остаётся на месте. Программа отправляет его зашифрованную копию "
                      "и сразу удаляет копию из своей временной папки.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.setCentralWidget(root)
        self._cleanup_temp()
        self._update()
        # обновления проверяются только по кнопке «Обновления»

    def _cleanup_temp(self):
        """Копии, оставшиеся после аварийного закрытия программы."""
        for f in temp_dir().glob("outgoing_*"):
            secure_delete(f)

    def _update(self):
        host, secret = self.settings["owner_host"], self.settings["secret"]
        if not secret:
            self.lbl_target.setText("⚠ Введите код доступа в настройках (он есть в программе владельца)")
        elif host:
            self.lbl_target.setText(f"Получатель: ПК владельца {host}, порт {self.settings['port']}")
        else:
            self.lbl_target.setText("Получатель: ПК владельца найдётся автоматически при отправке")
        p = Path(self.path.text().strip())
        self.btn_send.setEnabled(bool(secret) and p.suffix.lower() in (".xlsx", ".xlsm") and p.is_file())

    def open_settings(self):
        if AccountantSettingsDialog(self.settings, self).exec():
            self._update()

    def pick_file(self):
        start = str(Path(self.path.text()).parent) if self.path.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Кассовая книга", start, "Excel (*.xlsx *.xlsm)")
        if path:
            self.path.setText(path)

    def send(self):
        src = Path(self.path.text().strip())
        host, port, secret = self.settings["owner_host"], self.settings["port"], self.settings["secret"]
        self.settings["last_file"] = str(src)
        self.settings.save()
        self.btn_send.setEnabled(False)
        self.dot.set("blue")
        self.status.setText("Отправляю…")

        def work():
            tmp = temp_dir() / f"outgoing_{uuid.uuid4().hex}{src.suffix}"
            try:
                shutil.copy2(src, tmp)          # оригинал не трогаем
                data = tmp.read_bytes()
            except PermissionError:
                raise TransferError("Файл занят другой программой. Сохраните и закройте его в Excel.") from None
            finally:
                secure_delete(tmp)               # копия удаляется всегда
            target = (host, port) if host else None
            if target is None:
                target = _find_owner(secret)
            try:
                message = send_file(target[0], target[1], secret, src.name, data)
            except TransferError as e:
                # адрес мог смениться - ищем программу владельца заново и пробуем ещё раз
                if not e.unreachable:
                    raise
                new = _find_owner(secret, exclude=target, error=e)
                message = send_file(new[0], new[1], secret, src.name, data)
                target = new
            return message, target

        def done(result, err):
            self._update()
            if err:
                self.dot.set("red")
                self.status.setText(str(err))
                self.journal.write("Ошибка отправки", type(err).__name__)
                return
            message, (new_host, new_port) = result
            if (new_host, new_port) != (host, port):
                self.settings["owner_host"], self.settings["port"] = new_host, new_port
                self.settings.save()
                self._update()
            self.dot.set("green")
            self.status.setText(f"✓ {message}")
            self.journal.write("Кассовая книга отправлена владельцу", src.name)

        run_in_background(work, done)


def _find_owner(secret: str, exclude: tuple | None = None,
                error: TransferError | None = None) -> tuple[str, int]:
    found = [f for f in discover_owner(secret) if (f["ip"], f["port"]) != exclude]
    if not found:
        if error is not None:
            raise error
        raise TransferError("Программа владельца не найдена в сети. Проверьте, что она открыта и что "
                            "код доступа совпадает, или впишите адрес в настройках.")
    return found[0]["ip"], found[0]["port"]


def main():
    updated = updater.after_start()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = AccountantWindow()
    if updated:
        w.journal.write("Программа обновлена", f"версия {__version__}")
    w.show()
    sys.exit(app.exec())
