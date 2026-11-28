"""ACMineevo — Витрина: окно настроек. Сама витрина - сайт (server.py); окно нужно, чтобы
задать подключение к Далиону, пользователей сайта (логин + пароль) и названия линий, собрать данные вручную и
проверить сайт. На сервере работают задачи Windows (tools/setup_vitrina.ps1):
сбор в 6:00 (--collect) и сайт при включении компьютера (--serve). Кнопка «Перезапустить сайт»
останавливает все копии сайта и запускает его заново из текущего файла программы (restart.py).
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import __version__
from ..common import app_lock, updater
from ..common.journal import Journal
from ..common.ui import STYLE, form_layout, run_in_background, show_start
from ..common.update_ui import UpdateController
from . import restart, runner
from .db import read_db
from .server import make_lock, norm_login, serve

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


ROLE_TITLES = {"owner": "владелец (всё)", "marketer": "маркетолог (без денег)"}


class UsersDialog(QDialog):
    """Пользователи сайта: логин + роль + пароль. Старые пароли ролей (до 0.12) показываются как
    пользователи «владелец»/«маркетолог» и при сохранении переносятся в общий список."""

    def __init__(self, site: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Пользователи сайта витрины")
        self.resize(460, 360)
        self.users = [dict(u) for u in runner.site_users(site).values()]
        lay = QVBoxLayout(self)
        hint = QLabel("Вход на сайт — по логину и паролю. Владелец видит всё, маркетолог — без денег. "
                      "Логин без учёта регистра. Пароли хранятся только в виде отпечатков.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.list = QListWidget()
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in (("Добавить…", self._add), ("Сменить пароль…", self._password), ("Удалить", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()

    def _fill(self):
        self.list.clear()
        for u in sorted(self.users, key=lambda u: (u["role"] != "owner", u["login"].lower())):
            self.list.addItem(f"{u['login']}  —  {ROLE_TITLES.get(u['role'], u['role'])}")

    def _current(self) -> dict | None:
        i = self.list.currentRow()
        if i < 0:
            return None
        ordered = sorted(self.users, key=lambda u: (u["role"] != "owner", u["login"].lower()))
        return ordered[i]

    def _add(self):
        login, ok = QInputDialog.getText(self, TITLE, "Логин (например: Саша):")
        login = " ".join(login.split())
        if not ok or not login:
            return
        if any(norm_login(u["login"]) == norm_login(login) for u in self.users):
            QMessageBox.warning(self, TITLE, "Такой логин уже есть.")
            return
        names = list(ROLE_TITLES.values())
        role_title, ok = QInputDialog.getItem(self, TITLE, "Роль:", names, 0, False)
        if not ok:
            return
        dlg = SitePasswordDialog(login, self)
        if dlg.exec() != QDialog.Accepted or not (dlg.lock or {}).get("hash"):
            return
        role = next(k for k, v in ROLE_TITLES.items() if v == role_title)
        self.users.append({"login": login, "role": role, "lock": dlg.lock})
        self._fill()

    def _password(self):
        u = self._current()
        if not u:
            return
        dlg = SitePasswordDialog(u["login"], self)
        if dlg.exec() == QDialog.Accepted and (dlg.lock or {}).get("hash"):
            u["lock"] = dlg.lock

    def _delete(self):
        u = self._current()
        if u and QMessageBox.question(self, TITLE, f"Удалить пользователя «{u['login']}»?") == QMessageBox.Yes:
            self.users.remove(u)
            self._fill()


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
        self.btn_users = QPushButton()
        self.btn_users.clicked.connect(self._edit_users)
        pw.addWidget(self.btn_users)
        pw.addStretch()
        special = {**runner.DEFAULTS["site"]["special"], **(s.get("special") or {})}
        self.warranty = QLineEdit(special.get("warranty") or "")
        self.personal = QLineEdit(special.get("personal") or "")
        self.payer = QLineEdit(special.get("payer") or "")
        for w in (self.warranty, self.personal, self.payer):
            w.setPlaceholderText("слова через запятую")
        self.warranty.setToolTip("ЗН - гарантийный, если в его договоре (или виде, типе, статусе) есть одно из слов")
        self.personal.setToolTip("Запасной признак наших машин: слово в договоре, виде, типе или статусе ЗН")
        self.payer.setToolTip("Плательщик в ЗН, когда ремонтируем свои машины (часть названия контрагента).\n"
                              "ЗН с таким плательщиком без гарантийного договора - «Наши машины».\n"
                              "Как называется плательщик - видно в таблице под строками на сайте («Финансы»).")
        f.addRow("Порт:", self.port)
        f.addRow("Названия линий:", self.lines)
        f.addRow("", self.full)
        f.addRow("Вход на сайт:", pw)
        f.addRow("Гарантия (слова):", self.warranty)
        f.addRow("Наши машины - плательщик:", self.payer)
        f.addRow("Наши машины (слова):", self.personal)
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
        self.btn_demo.setToolTip("Сайт на выдуманных данных: логин «владелец», пароль demo; "
                                 "логин «маркетолог», пароль marketing")
        self.btn_demo.clicked.connect(self._demo)
        self.btn_restart = QPushButton("⟳  Перезапустить сайт")
        self.btn_restart.setToolTip("Остановить все запущенные копии сайта (задача Windows, старые процессы) "
                                    "и запустить сайт заново из этой версии программы")
        self.btn_restart.clicked.connect(self._restart_site)
        for b in (save, self.btn_collect, self.btn_restart, self.btn_site, self.btn_demo):
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
        n = len(runner.site_users(self.settings["site"]))
        self.btn_users.setText(f"Пользователи сайта ({n})…" if n else "Пользователи сайта — не заданы…")

    def _edit_users(self):
        dlg = UsersDialog(self.settings["site"], self)
        if dlg.exec() == QDialog.Accepted:
            site = self.settings["site"]
            site["users"] = dlg.users
            site["owner_lock"], site["marketer_lock"] = {}, {}   # старые пароли ролей уже в списке
            self.settings.save()
            self._password_labels()
            QMessageBox.information(self, TITLE, "Пользователи сохранены. Сайт, запущенный задачей Windows, "
                                                 "применит их после «Перезапустить сайт».")

    def _save(self, quiet: bool = False):
        self.settings["com"] = {"server": self.server.text().strip(), "base": "", "user": self.user.text().strip(),
                                "password": self.password.text(), "platform": self.platform.text().strip()}
        self.settings["data"] = {"file": self.file.text().strip(), "months": self.months.value(),
                                 "in_codes": self.in_codes.text().strip(), "out_codes": self.out_codes.text().strip()}
        site = self.settings["site"]
        site.update({"port": self.port.value(), "lines": self.lines.toPlainText().strip(),
                     "full_phones": self.full.isChecked(),
                     "special": {"warranty": self.warranty.text().strip(), "personal": self.personal.text().strip(),
                                 "payer": self.payer.text().strip()}})
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

    def _restart_site(self, ask: bool = True):
        if ask and QMessageBox.question(
                self, TITLE, "Перезапустить сайт витрины?\n\nВсе запущенные копии сайта будут остановлены, "
                             "сайт запустится заново из этой версии программы "
                             f"({__version__}). Пользователи сайта войдут заново.") != QMessageBox.Yes:
            return
        self._save(quiet=True)
        if self.httpd:                                     # сайт, запущенный из этого окна
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.btn_site.setText("Запустить сайт здесь")
        self.btn_restart.setEnabled(False)
        self.btn_restart.setText("Перезапускаю…")
        port = self.port.value()

        def done(report, err):
            self.btn_restart.setEnabled(True)
            self.btn_restart.setText("⟳  Перезапустить сайт")
            self._show_status()
            if err:
                self.journal.write("Перезапуск сайта: ошибка", str(err)[:300])
                QMessageBox.critical(self, TITLE, str(err))
                return
            ver = restart.site_version(port)
            if ver and ver != __version__:
                report += (f"\n\nВнимание: сайт работает в версии {ver}, а эта программа - {__version__}. "
                           "Задача Windows запускает другой файл - поставьте его на место или пересоздайте "
                           "задачу (tools\\setup_vitrina.ps1).")
                QMessageBox.warning(self, TITLE, report)
            else:
                QMessageBox.information(self, TITLE, report)

        run_in_background(lambda: restart.restart(port, updater.current_exe(), self.journal.write), done)

    def offer_restart_after_update(self):
        ver = restart.site_version(self.port.value())
        if ver is None or ver == __version__:
            return                                         # сайт не запущен или уже новый
        if QMessageBox.question(self, TITLE, f"Программа обновлена до {__version__}, а сайт работает в "
                                             f"{ver or 'старой версии'}.\n\nПерезапустить сайт сейчас?") == \
                QMessageBox.Yes:
            self._restart_site(ask=False)

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
        if not runner.has_owner(self.settings["site"]):
            QMessageBox.warning(self, TITLE, "Сначала заведите пользователя-владельца: «Пользователи сайта…».")
            return
        if not runner.data_file(self.settings).exists():
            QMessageBox.information(self, TITLE, "Данных витрины ещё нет: сайт откроется, но покажет пустую "
                                                 "страницу. Сначала нажмите «Собрать данные сейчас».")
        self._start(demo=False)

    def _demo(self):
        self._start(demo=True)
        QMessageBox.information(self, TITLE, "Демо-сайт открыт в браузере.\n\nЛогин «владелец», пароль demo\n"
                                             "Логин «маркетолог», пароль marketing")

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
    if updated:
        QTimer.singleShot(800, w.offer_restart_after_update)
    sys.exit(app.exec())
