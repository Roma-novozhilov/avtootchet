"""Программа маркетолога и пароль на вход: окна открываются, пароль проверяется, финансов нет.

    QT_QPA_PLATFORM=offscreen python tests/marketing_check.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from avtootchet.common import app_lock  # noqa: E402
from avtootchet.common.settings import Settings  # noqa: E402

app = QApplication([])

# --- пароль: хранится отпечаток, не сам пароль
lock = app_lock.make_lock("Секрет123")
assert app_lock.check(lock, "Секрет123") and not app_lock.check(lock, "секрет123")
assert "Секрет123" not in str(lock)
s = Settings("marketing", {"app_lock": {}})
assert not app_lock.is_set(s) and app_lock.unlock(s, "x"), "без пароля - сразу пускает"
app_lock.set_password(s, "Секрет123")
s2 = Settings("marketing", {"app_lock": {}})
assert app_lock.is_set(s2) and app_lock.check(s2["app_lock"], "Секрет123"), "пароль сохранился в файле"
d = app_lock.UnlockDialog("t", s2["app_lock"])
d.edit.setText("неверно")
d._try()
assert d.result() != QDialog.Accepted and "Неверный" in d.msg.text()
d.edit.setText("Секрет123")
d._try()
assert d.result() == QDialog.Accepted
app_lock.clear_password(s2)
assert not app_lock.is_set(Settings("marketing", {"app_lock": {}}))

# --- программа маркетолога: только CRM, без кассы/выручки/склада
from avtootchet.marketing import app as M  # noqa: E402

w = M.MarketingWindow()
w.server.setText(r"C:\1C\Dalion")
w.user.setText("Маркетолог")
w.password.setText("p")
w._save_quiet = True
src = w._src()
assert src.server == r"C:\1C\Dalion" and src.user == "Маркетолог"
loaded = [m for m in sys.modules if m.startswith("avtootchet.owner.")]
for banned in ("revenue", "stock_report", "stock_job", "report", "reconcile", "cashbook_parser", "process_pdf",
               "analytics", "app", "settings_dialog"):
    assert f"avtootchet.owner.{banned}" not in loaded, f"в программу маркетолога попал модуль {banned}"


class Conn:
    def NewObject(self, name):
        q = type("Q", (), {})()
        q.Text = ""
        q.Execute = lambda: type("R", (), {"Choose": lambda _s: type("S", (), {"Next": lambda _s: True})()})()
        return q


src._connect = lambda: Conn()
assert M.check_connection(src) == "Подключение к 1С работает."
print("OK: программа маркетолога и пароль на вход")
