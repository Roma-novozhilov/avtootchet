"""ACMineevo — Витрина: окно настроек. Сама витрина - сайт (server.py); окно нужно, чтобы
задать подключение к Далиону, пароли сайта и названия линий, собрать данные вручную и
проверить сайт. На сервере работают задачи Windows (tools/setup_vitrina.ps1):
сбор в 6:00 (--collect) и сайт при включении компьютера (--serve).
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser

from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..common import app_lock, updater
from ..common.journal import Journal
from ..common.ui import STYLE, form_layout, run_in_background, show_start
from ..common.update_ui import UpdateController
from . import runner
from .db import read_db
from .server import make_lock, serve

TITLE = "ACMineevo — Витрина"
MIN_SITE_PASSWORD = 8


def local_addresses() -> list[str]:
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


class SitePasswordDialog(QDialog):
    def __init__(self, who: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Пароль сайта: {who}")
        lay = QVBoxLayout(self)
        f = form_layout()
        self.a, self.b = QLineEdit(), QLineEdit()
        for w in (self.a, self.b):
            w.setEchoMode(QLineEdit.Password)
        f.addRow("Новый пароль:", self.a)
        f.addRow("Ещё раз:", self.b)
        lay.addLayout(f)
        hint = QLabel(f"Не меньше {MIN_SITE_PASSWORD} символов. Пустой пароль — вход с этой ролью закрыт.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.lock = None

    def _ok(self):
        if self.a.text() != self.b.text():
            QMessageBox.warning(self, TITLE, "Пароли не совпадают.")
            return
        if self.a.text() and len(self.a.text()) < MIN_SITE_PASSWORD:
            QMessageBox.warning(self, TITLE, f"Пароль короче {MIN_SITE_PASSWORD} символов.")
            return
        self.lock = make_lock(self.a.text()) if self.a.text() else {}
        self.accept()


class VitrinaWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = runner.settings()
        self.journal = Journal("vitrina")
        self.httpd = None
        self.setWindowTitle(f"{TITLE}  v{__version__}")
        self.resize(760, 820)
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(16, 12, 16, 12)

        top = QHBoxLayout()
        title = QLabel("Витрина: звонки, заказ-наряды, деньги")
        title.setObjectName("title")
        top.addWidget(title)
        top.addStretch()
        self.updates = UpdateController(self, log=lambda a, b="": self.journal.write(a, b))
        top.addWidget(self.updates.button)
        btn_pw = QPushButton("🔒  Пароль программы…")
        btn_pw.clicked.connect(lambda: app_lock.SetPasswordDialog(self.settings, self).exec())
        top.addWidget(btn_pw)
        lay.addLayout(top)

        c, d, s = self.settings["com"], self.settings["data"], self.settings["site"]
        g = QGroupBox("Подключение к 1С:Далион (пользователь только для чтения, видит деньги)")
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
        lay.addWidget(g)

        g = QGroupBox("Данные")
        f = form_layout(g)
        self.file = QLineEdit(str(runner.data_file(self.settings)))
        show_start(self.file)
        self.months = QSpinBox()
        self.months.setRange(1, 24)
        self.months.setValue(int(d.get("months") or 6))
        self.in_codes, self.out_codes = QLineEdit(d.get("in_codes") or "0"), QLineEdit(d.get("out_codes") or "1")
        codes = QHBoxLayout()
        for w, label in ((self.in_codes, "входящие:"), (self.out_codes, "исходящие:")):
            w.setMaximumWidth(70)
            codes.addWidget(QLabel(label))
            codes.addWidget(w)
        codes.addStretch()
        f.addRow("Файл витрины:", self.file)
        f.addRow("Сколько месяцев:", self.months)
        f.addRow("Коды звонков:", codes)
        lay.addWidget(g)

        g = QGroupBox("Сайт")
        f = form_layout(g)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(s.get("port") or 8765))
        self.lines = QPlainTextEdit(s.get("lines") or "")
        self.lines.setPlaceholderText("по строке на линию: 21=Городской номер\n45=Авито")
        self.lines.setMaximumHeight(70)
        self.full = QCheckBox("показывать номера телефонов полностью (иначе — последние 4 цифры)")
        self.full.setChecked(bool(s.get("full_phones")))
        pw = QHBoxLayout()
        self.btn_owner = QPushButton()
        self.btn_owner.clicked.connect(lambda: self._site_password("owner_lock", "владелец (всё)"))
        self.btn_mark = QPushButton()
        self.btn_mark.clicked.connect(lambda: self._site_password("marketer_lock", "маркетолог (без денег)"))
        pw.addWidget(self.btn_owner)
        pw.addWidget(self.btn_mark)
        pw.addStretch()
        f.addRow("Порт:", self.port)
        f.addRow("Названия линий:", self.lines)
        f.addRow("", self.full)
        f.addRow("Пароли входа:", pw)
        self._password_labels()
        lay.addWidget(g)

        row = QHBoxLayout()
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save)
        self.btn_collect = QPushButton("Собрать данные сейчас")
        self.btn_collect.setObjectName("primary")
        self.btn_collect.clicked.connect(self._collect)
        self.btn_site = QPushButton("Запустить сайт здесь")
        self.btn_site.clicked.connect(self._toggle_site)
        self.btn_demo = QPushButton("Демо-сайт")
        self.btn_demo.setToolTip("Сайт на выдуманных данных: пароль demo (владелец) или marketing (маркетолог)")
        self.btn_demo.clicked.connect(self._demo)
        for b in (save, self.btn_collect, self.btn_site, self.btn_demo):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        hint = QLabel("На сервере витрина работает без этого окна: задачи Windows «сбор в 6:00» и «сайт при "
                      "включении» ставит tools\\setup_vitrina.ps1. Сбор только читает Далион. Сайт открывается "
                      "по адресу http://<адрес сервера>:<порт> — снаружи офиса только через Tailscale.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        self.setCentralWidget(root)
        self._show_status()

    # ------------------------------------------------------------ настройки
    def _password_labels(self):
        s = self.settings["site"]
        on = lambda k: bool((s.get(k) or {}).get("hash"))  # noqa: E731
        self.btn_owner.setText(("✓ " if on("owner_lock") else "") + "Пароль владельца…")
        self.btn_mark.setText(("✓ " if on("marketer_lock") else "") + "Пароль маркетолога…")

    def _site_password(self, key: str, who: str):
        dlg = SitePasswordDialog(who, self)
        if dlg.exec() == QDialog.Accepted and dlg.lock is not None:
            self.settings["site"][key] = dlg.lock
            self.settings.save()
            self._password_labels()

    def _save(self, quiet: bool = False):
        self.settings["com"] = {"server": self.server.text().strip(), "base": "", "user": self.user.text().strip(),
                                "password": self.password.text(), "platform": self.platform.text().strip()}
        self.settings["data"] = {"file": self.file.text().strip(), "months": self.months.value(),
                                 "in_codes": self.in_codes.text().strip(), "out_codes": self.out_codes.text().strip()}
        site = self.settings["site"]
        site.update({"port": self.port.value(), "lines": self.lines.toPlainText().strip(),
                     "full_phones": self.full.isChecked()})
        self.settings.save()
        if not quiet:
            QMessageBox.information(self, TITLE, "Настройки сохранены. Сайт, запущенный задачей Windows, "
                                                 "применит их после перезапуска.")

    def _show_status(self):
        path = runner.data_file(self.settings)
        try:
            _, meta = read_db(path)
            text = f"Данные собраны {meta.get('collected_at', '?')} за период с {meta.get('from', '?')}: " + \
                ", ".join(f"{k} — {v}" for k, v in (meta.get("counts") or {}).items())
            if meta.get("errors"):
                text += "\nНе собрались: " + "; ".join(f"{k}: {v}" for k, v in meta["errors"].items())
        except Exception:  # noqa: BLE001
            text = "Данных витрины ещё нет — нажмите «Собрать данные сейчас»."
        if self.httpd:
            port = self.httpd.server_address[1]
            text += "\n\nСайт работает: " + ", ".join(f"http://{ip}:{port}" for ip in (local_addresses() or ["localhost"]))
        self.status.setText(text)

    # ------------------------------------------------------------ действия
    def _collect(self):
        self._save(quiet=True)
        self.btn_collect.setEnabled(False)
        self.btn_collect.setText("Собираю… (несколько минут)")

        def done(meta, err):
            self.btn_collect.setEnabled(True)
            self.btn_collect.setText("Собрать данные сейчас")
            if err:
                QMessageBox.critical(self, TITLE, str(err))
            elif meta.get("errors"):
                QMessageBox.warning(self, TITLE, "Собрано, но не все разделы:\n\n" +
                                    "\n".join(f"{k}: {v}" for k, v in meta["errors"].items()))
            self._show_status()

        run_in_background(lambda: runner.do_collect(self.settings, self.journal), done)

    def _start(self, demo: bool):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        path = None
        if demo:
            from ..common.paths import temp_dir  # noqa: PLC0415
            from .demo import write_demo  # noqa: PLC0415
            path = temp_dir() / "vitrina_demo.db"
            write_demo(path)
        app = runner.make_app(self.settings, path, demo)
        try:
            self.httpd = serve(app, self.settings["site"].get("host") or "0.0.0.0", self.port.value())
        except OSError as e:
            QMessageBox.critical(self, TITLE, f"Порт {self.port.value()} занят (сайт уже запущен задачей?).\n\n{e}")
            return
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.btn_site.setText("Остановить сайт")
        self._show_status()
        webbrowser.open(f"http://localhost:{self.port.value()}/")

    def _toggle_site(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.btn_site.setText("Запустить сайт здесь")
            self._show_status()
            return
        self._save(quiet=True)
        if not (self.settings["site"].get("owner_lock") or {}).get("hash"):
            QMessageBox.warning(self, TITLE, "Сначала задайте пароль владельца.")
            return
        if not runner.data_file(self.settings).exists():
            QMessageBox.information(self, TITLE, "Данных витрины ещё нет: сайт откроется, но покажет пустую "
                                                 "страницу. Сначала нажмите «Собрать данные сейчас».")
        self._start(demo=False)

    def _demo(self):
        self._start(demo=True)
        QMessageBox.information(self, TITLE, "Демо-сайт открыт в браузере.\n\nПароль владельца: demo\n"
                                             "Пароль маркетолога: marketing")

    def closeEvent(self, e):  # noqa: N802
        if self.httpd:
            self.httpd.shutdown()
        super().closeEvent(e)


def main():
    code = runner.main(sys.argv)
    if code != -1:
        sys.exit(code)
    updated = updater.after_start()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    if not app_lock.unlock(runner.settings(), TITLE):
        sys.exit(0)
    w = VitrinaWindow()
    if updated:
        w.journal.write("Программа обновлена", f"версия {__version__}")
    w.show()
    sys.exit(app.exec())
