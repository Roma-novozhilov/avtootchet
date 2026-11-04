"""Проверка отчёта владельцем перед отправкой.

Отчёт открывается в Excel из временной копии. Владелец смотрит его и нажимает
«Отправить» или «Не отправлять». Отправляется всегда тот отчёт, что лежит в памяти
программы: правки, сделанные в Excel, в отправку не попадают (копия открывается
только для чтения). Временная копия стирается сразу после решения, а если Excel
её ещё держит - при закрытии программы или при следующем запуске.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from .crypto import secure_delete
from .paths import temp_dir

PREFIX = "preview_"
_pending: set[Path] = set()      # копии, которые Excel ещё не отпустил


class PreviewFile:
    def __init__(self, data: bytes, filename: str):
        self.dir = temp_dir() / f"{PREFIX}{uuid.uuid4().hex[:12]}"
        self.dir.mkdir(parents=True)
        self.path = self.dir / filename
        self.path.write_bytes(data)
        os.chmod(self.path, stat.S_IREAD)      # «только чтение»: Excel так и откроет

    def open(self) -> str | None:
        """Открыть в программе для .xlsx. None = открыто, иначе текст ошибки."""
        if os.environ.get("AVTO_NO_OPEN"):
            return None
        try:
            if sys.platform == "win32":
                os.startfile(str(self.path))  # noqa: S606 - наш собственный файл
            else:
                subprocess.Popen(["xdg-open", str(self.path)])
            return None
        except OSError as e:
            return (f"Не удалось открыть отчёт ({e}). Проверьте, что на этом компьютере "
                    "установлен Excel или другая программа для файлов .xlsx.")

    def remove(self) -> bool:
        """Стереть копию. False - файл ещё открыт в Excel."""
        return _remove_dir(self.dir)


def _remove_dir(d: Path) -> bool:
    ok = True
    for f in list(d.glob("*")):
        try:
            os.chmod(f, stat.S_IREAD | stat.S_IWRITE)
            secure_delete(f)
        except OSError:
            ok = False
    if ok:
        shutil.rmtree(d, ignore_errors=True)
        _pending.discard(d)
    else:
        _pending.add(d)
    return ok


def cleanup() -> int:
    """Стереть оставшиеся копии (при запуске и закрытии программы). Возвращает, сколько
    папок удалить не удалось."""
    left = 0
    for d in set(temp_dir().glob(PREFIX + "*")) | set(_pending):
        if d.is_dir() and not _remove_dir(d):
            left += 1
    return left


class PreviewDialog(QDialog):
    def __init__(self, preview: PreviewFile, action: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.preview = preview
        self.setWindowTitle("Проверка перед отправкой")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        lay = QVBoxLayout(self)
        text = QLabel(
            f"<b>Отчёт открыт в Excel.</b><br><br>Проверьте, что всё построено верно, и выберите, "
            f"что сделать: <b>{action}</b> или не отправлять.<br><br>"
            "Будет отправлен отчёт в том виде, как его построила программа: правки в Excel "
            "в отправку не попадут.")
        text.setWordWrap(True)
        lay.addWidget(text)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        again = QPushButton("Открыть ещё раз")
        again.clicked.connect(self._open)
        row.addWidget(again)
        row.addStretch()
        self.btn_no = QPushButton("Не отправлять")
        self.btn_no.clicked.connect(self.reject)
        row.addWidget(self.btn_no)
        self.btn_yes = QPushButton("Отправить")
        self.btn_yes.setObjectName("primary")
        self.btn_yes.clicked.connect(self.accept)
        row.addWidget(self.btn_yes)
        lay.addLayout(row)
        self._open()

    def _open(self):
        err = self.preview.open()
        self.status.setText(err or "")


def _ask(parent: QWidget | None, preview: PreviewFile, action: str) -> bool:
    return PreviewDialog(preview, action, parent).exec() == QDialog.Accepted


def confirm_with_preview(parent: QWidget | None, data: bytes, filename: str,
                         action: str = "отправить") -> bool:
    """Показать отчёт в Excel и спросить владельца. True = «Отправить»."""
    pf = PreviewFile(data, filename)
    try:
        decision = _ask(parent, pf, action)
    finally:
        while not pf.remove():
            ans = QMessageBox.question(
                parent, "Закройте Excel",
                "Закройте окно Excel с отчётом, чтобы программа стёрла его временную копию.\n\n"
                "«Да» - проверить снова, «Нет» - стереть позже (при закрытии программы).")
            if ans != QMessageBox.Yes:
                break
    return decision
