"""ACMineevo — администратор. Всё нужное администратору сервера в одном окне:
состояние сервера, сеансы и процессы 1С, задачи Планировщика, библиотека скриптов, журналы.

Нагрузка на сервер минимальная: обновляется только открытый раздел, данные собираются в фоне,
тяжёлые запросы (журнал событий, Tailscale, Планировщик) - редко или по кнопке, а когда окно
свёрнуто, обновление стоит на паузе.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from ..common import app_lock, updater
from ..common.journal import Journal
from ..common.settings import Settings
from ..common.ui import STYLE, form_layout, run_in_background, show_start, tool_button
from ..common.update_ui import UpdateController
from . import scripts as S
from . import system as SY

TITLE = "ACMineevo — администратор"
DEFAULTS = {
    "app_lock": {},
    "auto_refresh": True,
    "paths": {
        "scripts": r"C:\avtootchet\admin\scripts",
        "logs": r"C:\avtootchet\admin\logs",
        "backup_log": r"F:\Backup_1C\backup.log",
        "reboot_log": r"F:\Diag\reboot.log",
        "techlog": r"F:\Diag\techlog",
        "stock_dir": r"F:\Отчёты\Склад",
    },
}
# как часто обновлять открытый раздел, секунды (0 - только по кнопке)
INTERVALS = {"overview": 5, "sessions": 15, "tasks": 60, "scripts": 0, "logs": 0, "settings": 0}
SLOW_EVERY = 60          # питание, Tailscale, резервная копия на «Обзоре» - раз в минуту

WARN_BG = QColor("#fff4e5")
BAD_BG = QColor("#fdecea")


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes  # noqa: PLC0415
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def mono() -> object:
    return QFontDatabase.systemFont(QFontDatabase.FixedFont)


def fmt_dt(d: datetime | None, today_short: bool = True) -> str:
    if not d:
        return "—"
    if today_short and d.date() == datetime.now().date():
        return d.strftime("сегодня %H:%M")
    return d.strftime("%d.%m %H:%M")


def fmt_span(td: timedelta) -> str:
    h = int(td.total_seconds() // 3600)
    return f"{h // 24} дн. {h % 24} ч" if h >= 24 else f"{h} ч {int(td.total_seconds() // 60) % 60} мин"


def fmt_idle(m: int | None) -> str:
    if m is None:
        return "—"
    return f"{m // 60} ч {m % 60:02d} мин" if m >= 60 else f"{m} мин"


def make_table(headers: list[str], stretch: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


def fill(t: QTableWidget, rows: list[list], colors: list[QColor | None] | None = None,
         keys: list | None = None) -> None:
    """Заполнить таблицу, сохранив выделенную строку (по ключу)."""
    sel_key = None
    r = t.currentRow()
    if r >= 0 and t.item(r, 0) is not None:
        sel_key = t.item(r, 0).data(Qt.UserRole)
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = QTableWidgetItem("" if v is None else str(v))
            if isinstance(v, (int, float)):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if colors and colors[i] is not None:
                it.setBackground(QBrush(colors[i]))
            if j == 0 and keys is not None:
                it.setData(Qt.UserRole, keys[i])
            t.setItem(i, j, it)
    if sel_key is not None and keys is not None and sel_key in keys:
        t.selectRow(keys.index(sel_key))


def group(title: str, *widgets) -> QGroupBox:
    g = QGroupBox(title)
    v = QVBoxLayout(g)
    for w in widgets:
        if isinstance(w, QHBoxLayout):
            v.addLayout(w)
        else:
            v.addWidget(w)
    return g


def hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("hint")
    lb.setWordWrap(True)
    return lb


class Card(QWidget):
    """Плитка с крупным числом: «Память 23,1 / 31,5 ГБ»."""

    def __init__(self, caption: str):
        super().__init__()
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        self.cap = QLabel(caption)
        self.cap.setObjectName("hint")
        self.val = QLabel("—")
        self.val.setStyleSheet("font-size: 15pt; font-weight: 700; color: #1f3864;")
        self.sub = QLabel("")
        self.sub.setObjectName("hint")
        for w in (self.cap, self.val, self.sub):
            v.addWidget(w)

    def set(self, value: str, sub: str = "", level: str = ""):
        self.val.setText(value)
        self.sub.setText(sub)
        color = {"bad": "#b42318", "warn": "#b54708"}.get(level, "#1f3864")
        self.val.setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {color};")


# ====================================================================== окно
class AdminWindow(QMainWindow):
    def __init__(self, collect=SY):
        super().__init__()
        self.sy = collect                       # модуль сбора данных (в тестах - подделка)
        self.settings = Settings("admin", DEFAULTS)
        self.journal = Journal("admin")
        self.admin = is_admin()
        self.sampler = self.sy.ProcSampler()
        self.state: dict = {"procs": [], "sess": [], "power": None, "ts": None, "backup": None,
                            "tasks": None, "ov": None}
        self.busy: set[str] = set()
        self.ticks: dict[str, int] = {}
        self.slow_at: datetime | None = None
        self.proc: QProcess | None = None
        self.setWindowTitle(f"{TITLE}  v{__version__}")
        self.resize(1180, 760)

        root = QWidget()
        outer = QVBoxLayout(root)
        top = QHBoxLayout()
        title = QLabel("Администратор сервера")
        title.setObjectName("title")
        top.addWidget(title)
        self.badge = QLabel("● права администратора" if self.admin else
                            "● без прав администратора — завершать сеансы и запускать скрипты нельзя")
        self.badge.setStyleSheet(f"color: {'#067647' if self.admin else '#b42318'}; font-weight: 600;")
        top.addWidget(self.badge)
        top.addStretch()
        self.btn_refresh = QPushButton("↻  Обновить")
        self.btn_refresh.clicked.connect(lambda: self.refresh(force=True))
        top.addWidget(self.btn_refresh)
        self.updates = UpdateController(self, before_install=self._can_update, log=self._log)
        top.addWidget(self.updates.button)
        outer.addLayout(top)

        body = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(200)
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(root)

        self.pages: list[str] = []
        self._add_page("overview", "Обзор", self._page_overview())
        self._add_page("sessions", "Сеансы и 1С", self._page_sessions())
        self._add_page("tasks", "Задачи по расписанию", self._page_tasks())
        self._add_page("scripts", "Скрипты", self._page_scripts())
        self._add_page("logs", "Журналы и события", self._page_logs())
        self._add_page("settings", "Настройки", self._page_settings())
        self.nav.currentRowChanged.connect(self._switch)

        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.statusBar().addWidget(self.status, 1)

        self.updates.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.nav.setCurrentRow(0)

    # ------------------------------------------------------------ каркас
    def _add_page(self, key: str, title: str, widget: QWidget):
        self.pages.append(key)
        self.nav.addItem(QListWidgetItem(title))
        self.stack.addWidget(widget)

    def _switch(self, row: int):
        self.stack.setCurrentIndex(row)
        self.refresh(force=True)

    def current(self) -> str:
        return self.pages[self.stack.currentIndex()]

    def _can_update(self) -> bool:
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Обновление", "Дождитесь окончания скрипта.")
            return False
        return True

    def _tick(self):
        if not self.settings["auto_refresh"] or self.isMinimized() or not self.isVisible():
            return
        key = self.current()
        every = INTERVALS.get(key, 0)
        if not every:
            return
        self.ticks[key] = self.ticks.get(key, 0) + 1
        if self.ticks[key] >= every:
            self.ticks[key] = 0
            self.refresh()

    def refresh(self, force: bool = False):
        key = self.current()
        fn = getattr(self, f"_refresh_{key}", None)
        if fn is None or key in self.busy:
            return
        self.ticks[key] = 0
        fn(force)

    def _bg(self, key: str, work, done):
        """Сбор данных в фоне; одновременно - не больше одного сбора на раздел."""
        self.busy.add(key)

        def finish(res, err):
            self.busy.discard(key)
            if err is not None:
                self.status.setText(f"Ошибка ({key}): {err}")
                return
            done(res)
            ov = self.state.get("ov")
            own = f" · программа: {ov.self_mb:.0f} МБ, {ov.self_cpu:.1f}% ЦП" if ov else ""
            auto = INTERVALS.get(key, 0)
            mode = (f"автообновление каждые {auto} с" if auto and self.settings["auto_refresh"]
                    else "обновление по кнопке")
            self.status.setText(f"Обновлено {datetime.now():%H:%M:%S} · {mode}{own}")
        run_in_background(work, finish)

    def _log(self, action: str, details: str = ""):
        self.journal.write(action, details)

    def _need_admin(self) -> bool:
        if not self.admin:
            QMessageBox.warning(self, TITLE, "Для этого действия программу нужно запустить от имени "
                                             "администратора (правой кнопкой → «Запуск от имени администратора»).")
            return False
        return True

    # ============================================================ Обзор
    def _page_overview(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        cards = QHBoxLayout()
        self.c_cpu, self.c_mem, self.c_comp, self.c_up = (Card("Процессор"), Card("Память"),
                                                          Card("Сжатая память"), Card("Работает без перезагрузки"))
        self.c_disks = Card("Диски — свободно")
        for c in (self.c_cpu, self.c_mem, self.c_comp, self.c_up, self.c_disks):
            c.setStyleSheet("QWidget#card { background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px; }")
            cards.addWidget(c)
        v.addLayout(cards)

        self.warn_box = QLabel("")
        self.warn_box.setWordWrap(True)
        self.warn_box.setTextFormat(Qt.RichText)
        v.addWidget(group("Требует внимания", self.warn_box))

        row = QHBoxLayout()
        self.l_power = QLabel("—")
        self.l_power.setWordWrap(True)
        self.l_ts = QLabel("—")
        self.l_ts.setWordWrap(True)
        self.l_ts.setTextFormat(Qt.RichText)
        row.addWidget(group("Питание", self.l_power), 1)
        row.addWidget(group("Tailscale (удалённый доступ)", self.l_ts), 2)
        v.addLayout(row)

        self.t_top = make_table(["Процесс", "Пользователь", "Память, МБ", "PID"])
        v.addWidget(group("Больше всего памяти", self.t_top), 1)
        return w

    def _refresh_overview(self, force: bool):
        slow = force or self.slow_at is None or datetime.now() - self.slow_at > timedelta(seconds=SLOW_EVERY)
        sy, sampler, paths = self.sy, self.sampler, self.settings["paths"]

        def work():
            res = {"ov": sy.overview(), "top": sy.top_memory(8), "procs": sampler.sample()}
            if slow:
                for key, fn in (("power", sy.power_state), ("ts", sy.tailscale),
                                ("backup", lambda: sy.backup_status(paths["backup_log"]))):
                    try:
                        res[key] = fn()
                    except Exception:  # noqa: BLE001 - один недоступный источник не мешает остальным
                        res[key] = None
                try:
                    res["sess"] = sy.sessions()
                except OSError:
                    res["sess"] = []
            return res

        def done(res):
            if slow:
                self.slow_at = datetime.now()
            self.state.update(res)
            self._render_overview()
        self._bg("overview", work, done)

    def _render_overview(self):
        st = self.state
        ov = st["ov"]
        if ov:
            self.c_cpu.set(f"{ov.cpu:.0f}%", "", "warn" if ov.cpu >= 80 else "")
            self.c_mem.set(f"{ov.mem_used_gb:.1f} / {ov.mem_total_gb:.0f} ГБ", f"{ov.mem_pct:.0f}% занято",
                           "bad" if ov.mem_pct >= 90 else "warn" if ov.mem_pct >= 80 else "")
            self.c_comp.set(f"{ov.compressed_gb:.1f} ГБ", "Windows сжимает, когда памяти мало",
                            "warn" if ov.compressed_gb >= 3 else "")
            self.c_up.set(fmt_span(ov.uptime), f"с {ov.boot:%d.%m %H:%M}",
                          "warn" if ov.uptime > timedelta(days=8) else "")
            self.c_disks.set("  ".join(f"{d.mount} {d.free_gb:.0f} ГБ" for d in ov.disks),
                             "  ".join(f"{d.mount} {d.free_pct:.0f}%" for d in ov.disks),
                             "bad" if any(d.free_pct < 10 for d in ov.disks) else "")
        warns = self.sy.warnings(ov, st["procs"], st["sess"], st["power"], st["ts"], st["backup"], st["tasks"])
        if warns:
            self.warn_box.setText("<br>".join(f"<span style='color:#b54708'>▲</span> {w}" for w in warns))
        else:
            self.warn_box.setText("<span style='color:#067647'>✓ Всё в порядке</span>")
        p = st["power"]
        if p:
            sleep = "выключен" if not p["sleep_s"] else f"через {p['sleep_s'] // 60} мин"
            self.l_power.setText(f"Схема: {p['scheme']}\nСон от сети: {sleep}")
        ts = st["ts"]
        if ts is None:
            self.l_ts.setText("Tailscale не установлен или не отвечает")
        else:
            mode = {True: "да", False: "<b style='color:#b42318'>нет</b>", None: "неизвестно"}[ts["unattended"]]
            peers = ", ".join(f"{'●' if pr['online'] else '○'} {pr['name']}" for pr in ts["peers"][:8])
            self.l_ts.setText(f"{'● в сети' if ts['online'] else '○ НЕ в сети'} · {ts['name']} {ts['ip']}<br>"
                              f"Работает без входа пользователя (Run unattended): {mode}<br>"
                              f"<span style='color:#6b7280'>{peers or 'других устройств нет'}</span>")
        top = st.get("top") or []
        fill(self.t_top, [[r["name"], r["user"], round(r["mb"]), r["pid"]] for r in top])

    # ============================================================ Сеансы и 1С
    def _page_sessions(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_sess = make_table(["Пользователь", "Состояние", "Вход", "Бездействует", "Компьютер", "Сеанс"])
        b1 = QPushButton("Отправить сообщение…")
        b1.clicked.connect(self._msg_session)
        b2 = QPushButton("Завершить сеанс…")
        b2.setObjectName("danger")
        b2.clicked.connect(self._logoff_session)
        r1 = QHBoxLayout()
        r1.addWidget(b1)
        r1.addWidget(b2)
        r1.addStretch()
        v.addWidget(group("Сеансы Windows (вход на сервер)", self.t_sess, r1,
                          hint("«Отключён» — человек закрыл окно крестиком, а не вышел: его программы и 1С "
                               "продолжают работать и занимать память.")), 1)
        self.t_procs = make_table(["Пользователь", "Программа", "Платформа", "Запущен", "Память, МБ",
                                   "ЦП, %", "PID"], stretch=1)
        b3 = QPushButton("Завершить процесс…")
        b3.setObjectName("danger")
        b3.clicked.connect(self._kill_proc)
        r2 = QHBoxLayout()
        r2.addWidget(b3)
        r2.addStretch()
        v.addWidget(group("1С и программы ACMineevo", self.t_procs, r2,
                          hint("Жёлтые строки — запущены вчера или раньше: скорее всего, забытые сеансы. "
                               "Перед завершением спросите сотрудника, нет ли несохранённого.")), 1)
        return w

    def _refresh_sessions(self, force: bool):
        sy, sampler = self.sy, self.sampler

        def work():
            try:
                sess = sy.sessions()
            except OSError:
                sess = []
            return {"sess": sess, "procs": sampler.sample()}

        def done(res):
            self.state.update(res)
            ss = res["sess"]
            fill(self.t_sess, [[s.user, s.state, fmt_dt(s.logon, False), fmt_idle(s.idle_min), s.client, s.id]
                               for s in ss],
                 [WARN_BG if s.state == "отключён" else None for s in ss], [s.id for s in ss])
            ps = res["procs"]
            fill(self.t_procs, [[p["user"], p["kind"], p["platform"], fmt_dt(p["started"]), round(p["mb"]),
                                 round(p["cpu"], 1), p["pid"]] for p in ps],
                 [WARN_BG if p["stale"] else None for p in ps], [p["pid"] for p in ps])
        self._bg("sessions", work, done)

    def _selected(self, t: QTableWidget):
        r = t.currentRow()
        if r < 0 or t.item(r, 0) is None:
            QMessageBox.information(self, TITLE, "Сначала выберите строку в таблице.")
            return None, None
        return t.item(r, 0).data(Qt.UserRole), t.item(r, 0).text()

    def _msg_session(self):
        sid, user = self._selected(self.t_sess)
        if sid is None or not self._need_admin():
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Сообщение", f"Текст для {user}:",
            "Пожалуйста, сохраните работу, выйдите из 1С и выйдите с сервера: «Пуск → Выйти».")
        if ok and text.strip():
            try:
                self.sy.send_message(int(sid), "Администратор сервера", text.strip())
                self._log("Сообщение пользователю", user)
            except OSError as e:
                QMessageBox.warning(self, TITLE, str(e))

    def _logoff_session(self):
        sid, user = self._selected(self.t_sess)
        if sid is None or not self._need_admin():
            return
        if QMessageBox.question(self, "Завершить сеанс",
                                f"Завершить сеанс пользователя {user}?\n\nВсе его программы, включая 1С, "
                                "закроются. Несохранённое пропадёт.") != QMessageBox.Yes:
            return
        try:
            self.sy.logoff(int(sid))
            self._log("Сеанс завершён", user)
        except OSError as e:
            QMessageBox.warning(self, TITLE, str(e))
        QTimer.singleShot(2000, lambda: self.refresh(force=True))

    def _kill_proc(self):
        pid, user = self._selected(self.t_procs)
        if pid is None or not self._need_admin():
            return
        r = self.t_procs.currentRow()
        what = f"{self.t_procs.item(r, 1).text()} ({user}, запущен {self.t_procs.item(r, 3).text()}, PID {pid})"
        if QMessageBox.question(self, "Завершить процесс",
                                f"Завершить {what}?\n\nНесохранённые изменения в этом окне 1С пропадут.") \
                != QMessageBox.Yes:
            return
        try:
            self.sy.kill_process(int(pid))
            self._log("Процесс завершён", what)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, TITLE, f"Не удалось: {e}")
        QTimer.singleShot(1500, lambda: self.refresh(force=True))

    # ============================================================ Задачи
    def _page_tasks(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_tasks = make_table(["Задача", "Расписание", "Следующий запуск", "Прошлый запуск",
                                   "Результат", "Состояние", "От имени"], stretch=1)
        b = QPushButton("Запустить сейчас…")
        b.clicked.connect(self._run_task)
        r = QHBoxLayout()
        r.addWidget(b)
        r.addStretch()
        v.addWidget(group("Задачи Планировщика Windows (созданные вручную, без системных)", self.t_tasks, r,
                          hint("Красные строки — последний запуск закончился ошибкой.")), 1)
        return w

    def _refresh_tasks(self, force: bool):
        sy = self.sy

        def done(ts):
            self.state["tasks"] = ts
            fill(self.t_tasks, [[t.name, t.schedule, fmt_dt(t.next), fmt_dt(t.last), t.result, t.state, t.user]
                                for t in ts],
                 [None if t.ok else BAD_BG for t in ts], [t.name for t in ts])
        self._bg("tasks", sy.tasks, done)

    def _run_task(self):
        name, _ = self._selected(self.t_tasks)
        if name is None or not self._need_admin():
            return
        if QMessageBox.question(self, "Запустить задачу", f"Запустить «{name}» сейчас, вне расписания?") \
                != QMessageBox.Yes:
            return
        run_in_background(lambda: self.sy.run_task(name),
                          lambda _r, e: (self._log("Задача запущена вручную", name),
                                         QTimer.singleShot(3000, lambda: self.refresh(force=True))) if e is None
                          else QMessageBox.warning(self, TITLE, str(e)))

    # ============================================================ Скрипты
    def _library(self) -> S.Library:
        p = self.settings["paths"]
        return S.Library(p["scripts"], p["logs"])

    def _page_scripts(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.l_scripts = QListWidget()
        self.l_scripts.currentRowChanged.connect(self._show_script)
        lv.addWidget(self.l_scripts, 1)
        row = QHBoxLayout()
        for text, fn in (("Добавить файл…", self._add_script_file), ("Новый из текста…", self._new_script),
                         ("Папка", self._open_scripts_folder)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lv.addLayout(row)
        self.btn_del_script = QPushButton("Удалить из библиотеки…")
        self.btn_del_script.setObjectName("danger")
        self.btn_del_script.clicked.connect(self._del_script)
        lv.addWidget(self.btn_del_script)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.l_script_info = QLabel("Выберите скрипт слева")
        self.l_script_info.setWordWrap(True)
        rv.addWidget(self.l_script_info)
        self.e_script = QPlainTextEdit()
        self.e_script.setReadOnly(True)
        self.e_script.setFont(mono())
        rv.addWidget(self.e_script, 3)
        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Параметры:"))
        self.e_args = QLineEdit()
        self.e_args.setPlaceholderText("например -Test или -Remove (необязательно)")
        run_row.addWidget(self.e_args, 1)
        self.btn_run = QPushButton("▶  Выполнить")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._run_script)
        self.btn_stop = QPushButton("■  Остановить")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_script)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_stop)
        rv.addLayout(run_row)
        self.e_out = QPlainTextEdit()
        self.e_out.setReadOnly(True)
        self.e_out.setFont(mono())
        self.e_out.setPlaceholderText("Здесь появится вывод скрипта")
        rv.addWidget(self.e_out, 2)
        self.l_run = QLabel("")
        self.l_run.setObjectName("hint")
        rv.addWidget(self.l_run)
        split.addWidget(right)
        split.setSizes([330, 820])
        v.addWidget(split, 1)
        return w

    def _refresh_scripts(self, force: bool):
        lib = self._library()
        cur = self._script()
        self.l_scripts.blockSignals(True)
        self.l_scripts.clear()
        self.scripts = lib.scripts()
        section = None
        for s in self.scripts:
            sec = "Встроенные (из комплекта программы)" if s.builtin else "Мои скрипты"
            if sec != section:
                head = QListWidgetItem(sec)
                head.setFlags(Qt.NoItemFlags)
                head.setForeground(QBrush(QColor("#6b7280")))
                self.l_scripts.addItem(head)
                section = sec
            it = QListWidgetItem(("    " + s.name))
            it.setData(Qt.UserRole, str(s.path))
            it.setToolTip(s.hint)
            self.l_scripts.addItem(it)
        self.l_scripts.blockSignals(False)
        if not any(not s.builtin for s in self.scripts):
            self.l_scripts.insertItem(1, QListWidgetItem("    (пусто — «Добавить файл…» или «Новый из текста…»)"))
            self.l_scripts.item(1).setFlags(Qt.NoItemFlags)
        target = str(cur.path) if cur else None
        for i in range(self.l_scripts.count()):
            if target and self.l_scripts.item(i).data(Qt.UserRole) == target:
                self.l_scripts.setCurrentRow(i)
                break
        self.status.setText(f"Скриптов: {len(self.scripts)} · журналы запусков: {lib.logs}")

    def _script(self) -> S.Script | None:
        it = self.l_scripts.currentItem() if hasattr(self, "l_scripts") else None
        path = it.data(Qt.UserRole) if it else None
        for s in getattr(self, "scripts", []):
            if str(s.path) == path:
                return s
        return None

    def _show_script(self, _row: int):
        s = self._script()
        if not s:
            return
        where = "встроенный, только чтение" if s.builtin else str(s.path)
        self.l_script_info.setText(f"<b>{s.name}</b> — {s.kind}. {s.hint}<br>"
                                   f"<span style='color:#6b7280'>{where}</span>")
        try:
            self.e_script.setPlainText(S.read_text(s.path))
        except OSError as e:
            self.e_script.setPlainText(f"Не удалось прочитать: {e}")
        self.btn_del_script.setEnabled(not s.builtin)

    def _add_script_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Скрипт", "", "Скрипты (*.ps1 *.cmd *.bat)")
        if not path:
            return
        lib = self._library()
        try:
            dst = lib.add_file(path)
        except FileExistsError as e:
            if QMessageBox.question(self, TITLE, f"«{e}» уже есть в библиотеке. Заменить?") != QMessageBox.Yes:
                return
            dst = lib.add_file(path, overwrite=True)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, TITLE, str(e))
            return
        self._log("Скрипт добавлен", dst.name)
        self._select_after_refresh(dst)

    def _new_script(self):
        d = NewScriptDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        lib = self._library()
        try:
            dst = lib.save_text(d.name.text(), d.text.toPlainText(), d.kind.currentData())
        except FileExistsError as e:
            if QMessageBox.question(self, TITLE, f"«{e}» уже есть. Заменить?") != QMessageBox.Yes:
                return
            dst = lib.save_text(d.name.text(), d.text.toPlainText(), d.kind.currentData(), overwrite=True)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, TITLE, str(e))
            return
        self._log("Скрипт сохранён", dst.name)
        self._select_after_refresh(dst)

    def _select_after_refresh(self, path: Path):
        self._refresh_scripts(True)
        for i in range(self.l_scripts.count()):
            if self.l_scripts.item(i).data(Qt.UserRole) == str(path):
                self.l_scripts.setCurrentRow(i)

    def _open_scripts_folder(self):
        lib = self._library()
        try:
            lib.ensure()
        except OSError as e:
            QMessageBox.warning(self, TITLE, str(e))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(lib.folder)))

    def _del_script(self):
        s = self._script()
        if not s or s.builtin:
            return
        if QMessageBox.question(self, TITLE, f"Удалить «{s.name}» из библиотеки?\n{s.path}") != QMessageBox.Yes:
            return
        try:
            self._library().remove(s)
            self._log("Скрипт удалён", s.name)
        except (PermissionError, OSError) as e:
            QMessageBox.warning(self, TITLE, str(e))
        self._refresh_scripts(True)

    def _run_script(self):
        s = self._script()
        if not s:
            QMessageBox.information(self, TITLE, "Выберите скрипт слева.")
            return
        if not self.admin and QMessageBox.question(
                self, TITLE, "Программа запущена без прав администратора — многие скрипты не сработают. "
                             "Всё равно выполнить?") != QMessageBox.Yes:
            return
        args = self.e_args.text().strip()
        if QMessageBox.question(self, "Выполнить скрипт",
                                f"Выполнить «{s.name}» {args}\nна этом сервере"
                                f"{' с правами администратора' if self.admin else ''}?\n\n"
                                "Проверьте текст скрипта справа — он выполнится как есть.") != QMessageBox.Yes:
            return
        program, argv = S.command(s, args)
        self.run_log = self._library().log_path(s)
        self.run_started = datetime.now()
        self.run_enc = S.output_encoding(s)
        self.e_out.clear()
        self._out(f"> {s.name} {args}\n")
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.setWorkingDirectory(str(s.path.parent))
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.finished.connect(lambda code, _st: self._script_done(s, code))
        self.proc.errorOccurred.connect(lambda e: self._out(f"\n[не удалось запустить: {e}]\n"))
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.l_run.setText(f"Выполняется с {self.run_started:%H:%M:%S}…")
        self._log("Скрипт запущен", f"{s.name} {args}".strip())
        self.proc.start(program, argv)

    def _out(self, text: str):
        self.e_out.moveCursor(QTextCursor.MoveOperation.End)
        self.e_out.insertPlainText(text)
        self.e_out.ensureCursorVisible()
        try:
            with open(self.run_log, "a", encoding="utf-8") as f:
                f.write(text)
        except (OSError, AttributeError):
            pass

    def _read_out(self):
        data = bytes(self.proc.readAllStandardOutput())
        self._out(data.decode(self.run_enc, "replace").replace("\r\n", "\n"))

    def _script_done(self, s: S.Script, code: int):
        secs = (datetime.now() - self.run_started).total_seconds()
        verdict = "успешно" if code == 0 else f"код выхода {code}"
        self._out(f"\n[завершён: {verdict}, {secs:.0f} с]\n")
        self.l_run.setText(f"«{s.name}»: {verdict} за {secs:.0f} с · журнал: {self.run_log}")
        self._log("Скрипт завершён", f"{s.name}: {verdict}")
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _stop_script(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            if QMessageBox.question(self, TITLE, "Остановить скрипт? Он может не успеть закончить "
                                                 "начатые изменения.") == QMessageBox.Yes:
                self.proc.kill()

    # ============================================================ Журналы
    def _page_logs(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.l_backup = QLabel("—")
        self.l_backup.setWordWrap(True)
        self.l_backup.setTextFormat(Qt.RichText)
        self.l_diag = QLabel("—")
        self.l_diag.setWordWrap(True)
        self.l_diag.setTextFormat(Qt.RichText)
        row.addWidget(group("Резервная копия и отчёт по складу", self.l_backup), 1)
        row.addWidget(group("Диагностика", self.l_diag), 1)
        v.addLayout(row)
        self.e_logtail = QPlainTextEdit()
        self.e_logtail.setReadOnly(True)
        self.e_logtail.setFont(mono())
        self.e_logtail.setMaximumHeight(170)
        v.addWidget(group("Последние строки журналов (резервная копия, перезагрузка)", self.e_logtail))
        self.t_events = make_table(["Когда", "Событие", "Подробно"], stretch=2)
        b = QPushButton("Показать включения, выключения и сон за 7 дней")
        b.clicked.connect(self._load_events)
        r = QHBoxLayout()
        r.addWidget(b)
        r.addStretch()
        v.addWidget(group("Журнал Windows", r, self.t_events), 1)
        return w

    def _refresh_logs(self, force: bool):
        sy, p = self.sy, self.settings["paths"]

        def work():
            res = {"backup": sy.backup_status(p["backup_log"]), "reboot": sy.tail(p["reboot_log"], 8),
                   "stock_err": sy.stock_report_error(p["stock_dir"]), "techlog": sy.folder_status(p["techlog"])}
            try:
                res["perf"] = sy.perf_collectors()
            except Exception:  # noqa: BLE001
                res["perf"] = []
            return res

        def done(res):
            b = res["backup"]
            self.state["backup"] = b
            if b.last:
                age = datetime.now() - b.last
                color = "#b42318" if age > timedelta(hours=26) or b.errors else "#067647"
                txt = (f"<b style='color:{color}'>Последняя копия: {b.last:%d.%m.%Y %H:%M}, "
                       f"ошибок {b.errors}</b>")
            else:
                txt = "<b style='color:#b42318'>Записей об успешной копии нет</b>"
            txt += f"<br>Журнал: {p['backup_log']}"
            txt += ("<br><b style='color:#b42318'>Отчёт по складу: " + res["stock_err"] + "</b>"
                    if res["stock_err"] else "<br>Отчёт по складу: ошибок нет")
            self.l_backup.setText(txt)
            t = res["techlog"]
            if t.get("exists"):
                tl = (f"Техжурнал 1С: {t['mb']:.0f} МБ, файлов {t['files']}, "
                      f"за 10 мин обновлено {t['fresh']} — {'пишется' if t['fresh'] else 'не пишется'}")
            else:
                tl = "Техжурнал 1С: выключен (папки нет)"
            perf = "; ".join(f"{c['name']}: {c['state']}" for c in res["perf"]) or "нет"
            self.l_diag.setText(f"{tl}<br>Счётчики производительности: {perf}")
            self.e_logtail.setPlainText("\n".join(["— резервная копия —", *b.lines[-8:], "",
                                                   "— плановая перезагрузка —", *(res["reboot"] or ["(нет записей)"])]))
        self._bg("logs", work, done)

    def _load_events(self):
        def done(rows):
            fill(self.t_events, [[fmt_dt(r["time"], False), r["what"], r["text"]] for r in rows],
                 [BAD_BG if r["id"] in (41, 6008) else WARN_BG if r["id"] == 42 else None for r in rows])
        self._bg("events", lambda: self.sy.power_events(7), done)

    # ============================================================ Настройки
    def _page_settings(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("Папки и журналы")
        f = form_layout(g)
        self.path_edits = {}
        labels = {"scripts": "Мои скрипты:", "logs": "Журналы запусков:", "backup_log": "Журнал резервной копии:",
                  "reboot_log": "Журнал перезагрузки:", "techlog": "Техжурнал 1С:", "stock_dir": "Отчёты по складу:"}
        for key, label in labels.items():
            e = QLineEdit(self.settings["paths"][key])
            show_start(e)
            row = QHBoxLayout()
            row.addWidget(e, 1)
            if key in ("scripts", "logs", "techlog", "stock_dir"):
                b = tool_button("…", "Выбрать папку")
                b.clicked.connect(lambda _=False, ed=e: self._pick_dir(ed))
                row.addWidget(b)
            self.path_edits[key] = e
            f.addRow(label, row)
        v.addWidget(g)
        self.chk_auto = QCheckBox("Обновлять открытый раздел автоматически (Обзор — 5 с, Сеансы — 15 с, "
                                  "Задачи — 60 с; на паузе, когда окно свёрнуто)")
        self.chk_auto.setChecked(self.settings["auto_refresh"])
        v.addWidget(self.chk_auto)
        row = QHBoxLayout()
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.clicked.connect(self._save_settings)
        pw = QPushButton("🔒  Пароль программы…")
        pw.clicked.connect(lambda: app_lock.SetPasswordDialog(self.settings, self).exec())
        journal = QPushButton("Журнал действий")
        journal.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.journal.path))))
        row.addWidget(save)
        row.addWidget(pw)
        row.addWidget(journal)
        row.addStretch()
        v.addLayout(row)
        v.addWidget(hint("Программа сама ничего не меняет на сервере: завершение сеансов и процессов, запуск "
                         "задач и скриптов — только по вашей кнопке и после подтверждения. Каждое такое "
                         "действие записывается в журнал действий."))
        v.addStretch()
        return w

    def _pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Папка", edit.text())
        if d:
            edit.setText(d.replace("/", "\\") if os.name == "nt" else d)

    def _save_settings(self):
        self.settings["paths"] = {k: e.text().strip() for k, e in self.path_edits.items()}
        self.settings["auto_refresh"] = self.chk_auto.isChecked()
        self.settings.save()
        self.status.setText("Настройки сохранены")


class NewScriptDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый скрипт")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        f = form_layout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("например, Закрыть брошенные сеансы")
        self.kind = QComboBox()
        self.kind.addItem("PowerShell (.ps1)", ".ps1")
        self.kind.addItem("Командный файл (.cmd)", ".cmd")
        f.addRow("Название:", self.name)
        f.addRow("Тип:", self.kind)
        v.addLayout(f)
        v.addWidget(QLabel("Текст скрипта (вставьте Ctrl+V):"))
        self.text = QPlainTextEdit()
        self.text.setFont(mono())
        v.addWidget(self.text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Сохранить в библиотеку")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


def main():
    updated = updater.after_start()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    if not app_lock.unlock(Settings("admin", DEFAULTS), TITLE):
        sys.exit(0)
    w = AdminWindow()
    if updated:
        w._log("Программа обновлена", f"версия {__version__}")
    w.show()
    sys.exit(app.exec())


__all__ = ["main", "AdminWindow"]
