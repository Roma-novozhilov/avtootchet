"""Кнопка «Обновления» для окна программы: проверка при запуске и установка новой версии."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QPushButton, QWidget

from .. import __version__
from . import updater
from .ui import run_in_background


class _Progress(QObject):
    changed = Signal(int, int)


class UpdateController(QObject):
    """window       - главное окно (закрывается после установки);
    before_install - вызывается перед установкой, False = отменить (например, есть несохранённое);
    log            - запись в журнал действий."""

    def __init__(self, window: QWidget, before_install: Callable[[], bool] | None = None,
                 log: Callable[[str, str], None] | None = None):
        super().__init__(window)
        self.window = window
        self.before_install = before_install or (lambda: True)
        self.log = log or (lambda *_: None)
        self.release: updater.Release | None = None
        self._busy = False
        self.button = QPushButton("⟳  Обновления")
        self.button.setToolTip(f"Текущая версия {__version__}. Проверить, есть ли новая.")
        self.button.clicked.connect(self._clicked)

    def start(self, delay_ms: int = 3000) -> None:
        if updater.available() is None:
            QTimer.singleShot(delay_ms, lambda: self.check(silent=True))

    # ------------------------------------------------------------ проверка
    def _clicked(self):
        if self.release:
            self.install()
        else:
            self.check(silent=False)

    def check(self, silent: bool) -> None:
        reason = updater.available()
        if reason:
            if not silent:
                QMessageBox.information(self.window, "Обновления", reason)
            return
        if self._busy:
            return
        self._busy = True
        self.button.setEnabled(False)
        self.button.setText("Проверяю…")

        def done(rel, err):
            self._busy = False
            self.button.setEnabled(True)
            if err:
                self._set_idle()
                if not silent:
                    QMessageBox.warning(self.window, "Обновления", str(err))
                return
            if rel is None:
                self._set_idle()
                if not silent:
                    QMessageBox.information(self.window, "Обновления",
                                            f"У вас последняя версия ({__version__}).")
                return
            self.release = rel
            self.button.setText(f"⬆  Обновить до {rel.version}")
            self.button.setObjectName("primary")
            self.button.style().unpolish(self.button)
            self.button.style().polish(self.button)
            self.button.setMinimumWidth(self.button.sizeHint().width() + 8)
            self.button.updateGeometry()
            if not silent:
                self.install()

        run_in_background(updater.check, done)

    def _set_idle(self):
        self.button.setText("⟳  Обновления")

    # ------------------------------------------------------------ установка
    def install(self) -> None:
        rel = self.release
        if not rel or self._busy:
            return
        notes = f"\n\nЧто нового:\n{rel.notes[:1500]}" if rel.notes else ""
        if QMessageBox.question(
                self.window, "Обновление",
                f"Установить версию {rel.version}? Сейчас у вас {__version__}.{notes}\n\n"
                "Программа закроется и сразу откроется заново.") != QMessageBox.Yes:
            return
        if not self.before_install():
            return
        self._busy = True
        self.button.setEnabled(False)
        dlg = QProgressDialog("Скачиваю обновление…", None, 0, 100, self.window)
        dlg.setWindowTitle("Обновление")
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)
        prog = _Progress()
        prog.changed.connect(lambda n, total: dlg.setValue(int(n * 100 / total)) if total else None)

        def work():
            new = updater.download(rel, progress=lambda n, t: prog.changed.emit(n, t))
            updater.install(new)

        def done(_, err):
            dlg.close()
            self._busy = False
            self.button.setEnabled(True)
            if err:
                self.log("Обновление не установлено", str(err)[:200])
                QMessageBox.critical(self.window, "Обновление", str(err))
                return
            self.log("Установлено обновление", f"{__version__} → {rel.version}")
            self.window.close()

        run_in_background(work, done)
