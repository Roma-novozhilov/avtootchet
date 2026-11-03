"""Сквозная проверка без экрана: бухгалтер -> сеть -> владелец -> проверка -> флешка -> удаление.

    QT_QPA_PLATFORM=offscreen python tests/e2e_check.py <папка для скриншотов>
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
work = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(work / "appdata")
usb = work / "usb"
usb.mkdir()
os.environ["AVTO_FAKE_USB"] = str(usb)
shots = Path(sys.argv[1]) if len(sys.argv) > 1 else work

from PySide6.QtCore import QDate  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from avtootchet.accountant.app import AccountantWindow  # noqa: E402
from avtootchet.common.paths import temp_dir  # noqa: E402
from avtootchet.common.ui import STYLE  # noqa: E402
from avtootchet.owner.app import OwnerWindow  # noqa: E402

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)

app = QApplication(sys.argv)
app.setStyleSheet(STYLE)


def pump(seconds=0.5, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.02)
    return until() if until else True


owner = OwnerWindow()
owner.show()
owner.d_from.setDate(QDate(2026, 10, 1))
owner.d_to.setDate(QDate(2026, 10, 31))
pump(0.3)
owner.grab().save(str(shots / "1_owner_start.png"))

# --- бухгалтер: исходный файл копируем в "Документы" бухгалтера
src = work / "Документы" / "Касса октябрь.xlsx"
src.parent.mkdir()
src.write_bytes((ROOT / "demo" / "Кассовая книга (демо, с ошибками).xlsx").read_bytes())
acc = AccountantWindow()
acc.settings["owner_host"] = "127.0.0.1"
acc.settings["port"] = owner.settings["port"]
acc.settings["secret"] = owner.settings["secret"]
acc.settings.save()
acc.path.setText(str(src))
acc.show()
pump(0.2)
assert acc.btn_send.isEnabled(), "кнопка отправки должна быть активна"

# неверный код доступа
acc.settings["secret"] = "WRONG-CODE-0000-0000"
acc.send()
assert pump(10, lambda: "код" in acc.status.text().lower()), acc.status.text()
print("неверный код ->", acc.status.text())
acc.settings["secret"] = owner.settings["secret"]

# адрес не указан - бухгалтер должен найти владельца сам
acc.settings["owner_host"] = ""
acc.settings.save()
acc._update()
assert acc.btn_send.isEnabled(), "без адреса отправка тоже должна быть доступна"
acc.send()
assert pump(15, lambda: acc.status.text().startswith("✓")), acc.status.text()
print("автопоиск ->", acc.settings["owner_host"], acc.settings["port"], "|", acc.status.text())
assert acc.settings["owner_host"], "найденный адрес должен сохраниться"

# старый неверный адрес - должен переискать и отправить
acc.settings["owner_host"], acc.settings["port"] = "127.0.0.1", 1
acc.settings.save()
acc.status.setText("")
acc.send()
assert pump(20, lambda: acc.status.text().startswith("✓")), acc.status.text()
print("неверный адрес -> переискал:", acc.settings["owner_host"], acc.settings["port"])
assert acc.settings["port"] == owner.settings["port"]
acc.grab().save(str(shots / "2_accountant_sent.png"))
assert src.exists(), "оригинал бухгалтера должен остаться"
assert not list(temp_dir().glob("outgoing_*")), "временная копия должна быть удалена"
assert pump(5, lambda: owner.session.book is not None), "владелец не получил книгу"

# --- владелец: данные 1С, проверка
owner.load_1c()
assert pump(10, lambda: owner.session.onec is not None), owner.lbl_1c.text()
assert owner.btn_check.isEnabled()
owner.run_check()
pump(0.3)
res = owner.session.result
print(f"проверка -> ошибок {res.errors}, предупреждений {res.warnings}")
assert res.errors > 0
owner.grab().save(str(shots / "3_owner_checked.png"))

# --- флешка
owner.refresh_drives()
pump(0.2)
assert owner.btn_usb.isEnabled(), "кнопка флешки должна быть активна"
owner.export_usb()
files = list(usb.glob("*.xlsx"))
print("на флешке ->", [f.name for f in files])
assert len(files) == 1

# --- удаление
owner.delete_all(ask=False)
pump(0.2)
assert not owner.session.has_data
assert not owner.btn_check.isEnabled() and not owner.btn_usb.isEnabled()
owner.tabs.setCurrentIndex(1)
pump(0.2)
owner.grab().save(str(shots / "4_owner_deleted_journal.png"))
journal = (Path(os.environ["APPDATA"]) / "AvtoOtchet" / "journal_owner.log").read_text(encoding="utf-8")
print("--- журнал владельца ---\n" + journal)
for bad in ("Касса октябрь", "₽"):
    assert bad not in journal or bad == "Касса октябрь", bad
owner.close()
acc.close()
print("OK: все шаги пройдены")
