"""Пароль на вход в программу.

Хранится не сам пароль, а его «отпечаток» (PBKDF2-SHA256 с солью, 200 000 повторений) в настройках
программы этого пользователя Windows. Утренний отчёт по складу (--stock-report) пароль не спрашивает:
он запускается планировщиком без окна.
Забыли пароль - удалите раздел "app_lock" из файла настроек (%APPDATA%\\AvtoOtchet\\<программа>_settings.json);
прочие настройки останутся.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
                               QVBoxLayout, QWidget)

ITERATIONS = 200_000
MIN_LEN = 4


def make_lock(password: str) -> dict:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(),
            "iterations": ITERATIONS}


def check(lock: dict, password: str) -> bool:
    try:
        salt = base64.b64decode(lock["salt"])
        want = base64.b64decode(lock["hash"])
        n = int(lock.get("iterations") or ITERATIONS)
    except (KeyError, ValueError, TypeError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
    return hmac.compare_digest(got, want)


def is_set(settings) -> bool:
    lock = settings.data.get("app_lock") or {}
    return bool(lock.get("hash"))


def set_password(settings, password: str) -> None:
    settings["app_lock"] = make_lock(password)
    settings.save()


def clear_password(settings) -> None:
    settings["app_lock"] = {}
    settings.save()


class UnlockDialog(QDialog):
    def __init__(self, title: str, lock: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.lock, self.fails = lock, 0
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Введите пароль программы"))
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        lay.addWidget(self.edit)
        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        lay.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Войти")
        bb.button(QDialogButtonBox.Cancel).setText("Выход")
        bb.accepted.connect(self._try)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _try(self):
        if check(self.lock, self.edit.text()):
            self.accept()
            return
        self.fails += 1
        self.edit.clear()
        if self.fails >= 3:
            time.sleep(min(2 * (self.fails - 2), 10))   # перебор паролей - медленно
        self.msg.setText(f"Неверный пароль (попыток: {self.fails}).")


def unlock(settings, title: str) -> bool:
    """Спросить пароль при запуске. True - можно работать (или пароль не задан)."""
    if not is_set(settings):
        return True
    return UnlockDialog(title, settings["app_lock"]).exec() == QDialog.Accepted


class SetPasswordDialog(QDialog):
    """Задать или сменить пароль программы (пустой - снять пароль)."""

    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Пароль программы")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.cur = QLineEdit()
        self.cur.setEchoMode(QLineEdit.Password)
        if is_set(settings):
            f.addRow("Текущий пароль:", self.cur)
        self.new1 = QLineEdit()
        self.new1.setEchoMode(QLineEdit.Password)
        self.new2 = QLineEdit()
        self.new2.setEchoMode(QLineEdit.Password)
        f.addRow("Новый пароль:", self.new1)
        f.addRow("Ещё раз:", self.new2)
        lay.addLayout(f)
        hint = QLabel(f"Не меньше {MIN_LEN} символов. Оставьте поля нового пароля пустыми, чтобы снять пароль. "
                      "Пароль спрашивается при каждом запуске программы.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Сохранить")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        if is_set(self.s) and not check(self.s["app_lock"], self.cur.text()):
            QMessageBox.warning(self, "Пароль", "Текущий пароль неверный.")
            return
        a, b = self.new1.text(), self.new2.text()
        if a != b:
            QMessageBox.warning(self, "Пароль", "Новые пароли не совпадают.")
            return
        if not a:
            clear_password(self.s)
            QMessageBox.information(self, "Пароль", "Пароль снят: программа запускается без пароля.")
        elif len(a) < MIN_LEN:
            QMessageBox.warning(self, "Пароль", f"Пароль короче {MIN_LEN} символов.")
            return
        else:
            set_password(self.s, a)
            QMessageBox.information(self, "Пароль", "Пароль сохранён. Он будет спрашиваться при запуске.")
        self.accept()
