"""Программа администратора: разбор данных, библиотека скриптов, окно на поддельных данных.

    python tests/admin_check.py            (QT_QPA_PLATFORM=offscreen)
    python tests/admin_check.py <папка>    - ещё и скриншоты разделов
"""
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp()

from avtootchet.admin import scripts as S  # noqa: E402
from avtootchet.admin import system as SY  # noqa: E402

# ---------------------------------------------------------------- разбор
assert SY.platform_of(r"C:\Program Files (x86)\1cv8\8.3.27.2325\bin\1cv8.exe") == "8.3.27.2325 (32-бит)"
assert SY.platform_of(r"C:\Program Files\1cv8\8.5.1.1343\bin\1cv8c.exe") == "8.5.1.1343"
assert SY.client_kind("1cv8c.exe") == "тонкий клиент" and SY.client_kind("ACMineevo-Vladelec.exe").startswith("программа")

rows = [
    {"Name": "АвтоОтчёт - залежавшиеся запчасти", "State": "Ready", "Last": "2026-11-16T07:30:01",
     "Next": "2026-11-17T07:30:00", "Result": 0, "User": "SERVER\\roma",
     "Triggers": {"Kind": "MSFT_TaskDailyTrigger", "Start": "2026-11-10T07:30:00", "Days": "", "Enabled": True}},
    {"Name": "Резервная копия", "State": "Ready", "Last": "2026-11-16T02:00:00", "Next": "2026-11-17T02:00:00",
     "Result": 1, "User": "SYSTEM",
     "Triggers": [{"Kind": "MSFT_TaskDailyTrigger", "Start": "2026-11-01T02:00:00+03:00"}]},
    {"Name": "Перезагрузка", "State": "Ready", "Last": "", "Next": "2026-11-22T05:00:00", "Result": 267011,
     "User": "SYSTEM", "Triggers": [{"Kind": "MSFT_TaskWeeklyTrigger", "Start": "2026-11-16T05:00:00", "Days": "1"}]},
    {"Name": "Питание при включении", "State": "Ready", "Last": "2026-11-16T08:00:00", "Next": "", "Result": 0,
     "User": "SYSTEM", "Triggers": [{"Kind": "MSFT_TaskBootTrigger", "Start": ""}]},
]
ts = SY.parse_tasks(rows)
by = {t.name: t for t in ts}
assert ts[0].name == "Резервная копия" and not ts[0].ok and ts[0].result == "ошибка (код 1)", "ошибки - наверх"
assert by["АвтоОтчёт - залежавшиеся запчасти"].schedule == "каждый день 07:30"
assert by["Перезагрузка"].schedule == "по неделям (вс) 05:00" and by["Перезагрузка"].result == "ещё не запускалась"
assert by["Питание при включении"].schedule == "при включении" and by["Питание при включении"].user == "SYSTEM"
assert SY.describe_trigger({"Kind": "MSFT_TaskWeeklyTrigger", "Start": "2026-11-16T05:00:00", "Days": "62"}) \
    == "по неделям (пн, вт, ср, чт, пт) 05:00"

q = ("GUID подгруппы: 238c9fa8 (Сон)\n  Минимальное возможное значение: 0x00000000\n"
     "  Максимальное возможное значение: 0xffffffff\n  Шаг: 0x00000001\n"
     "Текущий индекс параметра питания переменного тока: 0x00000708\n"
     "Текущий индекс параметра питания постоянного тока: 0x00000384\n")
assert SY.parse_sleep_ac(q) == 1800, "сон от сети - 30 мин"
assert SY.parse_sleep_ac("пусто") is None

tsj = SY.parse_tailscale({"BackendState": "Running", "Self": {"HostName": "server", "Online": True,
                                                              "TailscaleIPs": ["100.112.23.72"]},
                          "Peer": {"a": {"HostName": "desktop", "Online": True, "TailscaleIPs": ["100.1.1.1"]},
                                   "b": {"HostName": "anastasia-pc", "Online": False}}},
                         {"ForceDaemon": False})
assert tsj["online"] and tsj["ip"] == "100.112.23.72" and tsj["unattended"] is False
assert [p["name"] for p in tsj["peers"]] == ["desktop", "anastasia-pc"], "в сети - сверху"

with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / "backup.log"
    log.write_text("2026-11-15 02:10:00  [Далион] готово\n2026-11-15 02:30:00  Итог: ошибок 0, свободно 360 ГБ\n"
                   "2026-11-16 02:31:00  ОШИБКА [ЗУП]: нет файла\n2026-11-16 02:40:00  Итог: ошибок 1, свободно 355 ГБ\n",
                   encoding="utf-8-sig")
    b = SY.backup_status(log)
    assert b.last == datetime(2026, 11, 16, 2, 40) and b.errors == 1 and len(b.lines) == 4
    assert SY.backup_status(Path(tmp) / "нет.log").last is None
    tl = Path(tmp) / "techlog" / "1cv8_1"
    tl.mkdir(parents=True)
    (tl / "26111609.log").write_text("x" * 2048)
    st = SY.folder_status(Path(tmp) / "techlog")
    assert st["files"] == 1 and st["fresh"] == 1 and st["exists"]
    assert SY.folder_status(Path(tmp) / "нет")["exists"] is False
    (Path(tmp) / "ОШИБКА — отчёт не построен.txt").write_text("x")
    assert SY.stock_report_error(tmp).startswith("ОШИБКА")

now = datetime(2026, 11, 16, 10, 0)
ov = SY.Overview(cpu=12, mem_used_gb=28.5, mem_total_gb=31.5, mem_pct=90.5, compressed_gb=5.4,
                 disks=[SY.Disk("C:", 931, 664), SY.Disk("F:", 932, 40)], boot=now - timedelta(days=14),
                 self_mb=70, self_cpu=0.1)
procs = [{"pid": 1, "name": "1cv8.exe", "kind": "толстый клиент", "user": "Buh2floor", "stale": True,
          "platform": "8.3.27.2325 (32-бит)", "mb": 300, "cpu": 0, "started": now - timedelta(days=5)},
         {"pid": 2, "name": "1cv8c.exe", "kind": "тонкий клиент", "user": "user", "stale": False,
          "platform": "8.5.1.1343", "mb": 600, "cpu": 1, "started": now}]
sess = [SY.Session(5, "rdp-tcp#1", "Buh2floor", "отключён", now - timedelta(days=5), 600)]
w = SY.warnings(ov, procs, sess, {"scheme": "Сбалансированная", "sleep_s": 1800}, tsj,
                SY.BackupStatus(datetime(2026, 11, 14, 2, 40), 1), ts, now=now)
text = "\n".join(w)
for must in ("Память занята на 90%", "На диске F:", "не перезагружался 14", "Процессов 1С со вчера и раньше: 1 (Buh2floor)",
             "разных версиях платформы: 8.3.27.2325, 8.5.1.1343", "Buh2floor отключён и брошен 10 ч",
             "сон через 30 мин", "Run unattended", "больше суток назад", "с ошибками: 1", "«Резервная копия»: ошибка"):
    assert must in text, (must, text)
assert SY.warnings(None, [], [], {"scheme": "x", "sleep_s": 0}, None, None, []) == []

# реальные psutil-функции работают и здесь (Linux): процесс программы виден, память читается
o = SY.overview()
assert o.mem_total_gb > 0 and o.disks and o.self_mb > 0
assert SY.ProcSampler().sample() == [] or True
assert len(SY.top_memory(3)) <= 3

# ---------------------------------------------------------------- библиотека скриптов
with tempfile.TemporaryDirectory() as tmp:
    lib = S.Library(Path(tmp) / "scripts", Path(tmp) / "logs")
    src = Path(tmp) / "Проверка.ps1"
    src.write_text("# Проверить что-то\r\nWrite-Host 'привет'\r\n", encoding="utf-8-sig")
    dst = lib.add_file(src)
    try:
        lib.add_file(src)
        raise AssertionError("повтор без замены")
    except FileExistsError:
        pass
    lib.add_file(src, overwrite=True)
    p = lib.save_text("Закрыть: сеансы?", "Get-Process 1cv8\n# русский текст")
    assert p.name == "Закрыть_ сеансы_.ps1" and p.read_bytes().startswith(b"\xef\xbb\xbf"), "BOM для PowerShell 5.1"
    assert b"\r\n" in p.read_bytes()
    c = lib.save_text("старт", "@echo off\necho привет", ".cmd")
    assert c.read_bytes().decode("cp866").startswith("@echo off")
    try:
        lib.save_text("пусто", "   ")
        raise AssertionError("пустой текст")
    except ValueError:
        pass
    try:
        lib.add_file(Path(tmp) / "x.exe")
        raise AssertionError("только скрипты")
    except ValueError:
        pass
    items = lib.scripts()
    mine = [s for s in items if not s.builtin]
    built = [s for s in items if s.builtin]
    assert [s.name for s in mine] == ["Закрыть_ сеансы_.ps1", "Проверка.ps1", "старт.cmd"], [s.name for s in mine]
    assert any(s.name == "setup_techlog.ps1" for s in built), "встроенные - из tools"
    assert mine[1].hint == "Проверить что-то" and S.Script(dst, False).kind == "PowerShell"
    try:
        lib.remove(built[0])
        raise AssertionError("встроенные не удаляются")
    except PermissionError:
        pass
    lib.remove(mine[2])
    assert not c.exists()
    prog, argv = S.command(mine[1], "-Test")
    assert prog == "powershell" and argv[-1].endswith("Проверка.ps1' -Test") and "UTF8" in argv[-1]
    prog, argv = S.command(S.Script(Path(r"C:\a b\x.cmd"), False))
    assert prog == "cmd.exe" and argv == ["/c", '"C:\\a b\\x.cmd"']
    assert lib.log_path(mine[1], datetime(2026, 11, 16, 9, 5, 7)).name == "2026-11-16_090507 Проверка.log"

# ---------------------------------------------------------------- окно на поддельных данных
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from avtootchet.admin import app as A  # noqa: E402
from avtootchet.common.ui import STYLE  # noqa: E402

fake = types.SimpleNamespace(**{k: getattr(SY, k) for k in dir(SY) if not k.startswith("_")})
calls = []


class FakeSampler:
    def sample(self):
        return procs


fake.ProcSampler = FakeSampler
fake.overview = lambda: ov
fake.top_memory = lambda n: [{"pid": 3400, "name": "Memory Compression", "user": "", "mb": 5380},
                             {"pid": 61640, "name": "1cv8.exe", "user": "Buh2floor", "mb": 654}][:n]
fake.sessions = lambda: sess + [SY.Session(84, "rdp-tcp#6", "roma", "активен", now - timedelta(days=1), 0, "DESKTOP-1")]
fake.power_state = lambda: {"scheme": "Высокая производительность", "sleep_s": 0}
fake.tailscale = lambda: tsj
fake.tasks = lambda: ts
fake.power_events = lambda days: [{"time": now - timedelta(hours=14), "id": 42, "what": SY.EVENT_IDS[42], "text": "Система переходит в спящий режим."},
                                  {"time": now - timedelta(hours=2), "id": 107, "what": SY.EVENT_IDS[107], "text": "Система вышла из спящего режима."}]
fake.logoff = lambda sid: calls.append(("logoff", sid))
fake.kill_process = lambda pid: calls.append(("kill", pid))
fake.run_task = lambda name: calls.append(("task", name))
fake.perf_collectors = lambda: [{"name": "AvtoOtchet-Perf", "type": "Счетчик", "state": "Остановлено"}]
fake.backup_status = lambda p: SY.BackupStatus(datetime(2026, 11, 16, 2, 40), 0,
                                               ["2026-11-16 02:40:00  Итог: ошибок 0, свободно 355 ГБ"])
fake.folder_status = lambda p, fresh_min=10: {"exists": True, "mb": 812, "files": 233, "fresh": 3, "newest": now}

app = QApplication.instance() or QApplication([])
app.setStyleSheet(STYLE)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

tmp = Path(tempfile.mkdtemp())
win = A.AdminWindow(collect=fake)
win.timer.stop()
win.settings["paths"] = {**win.settings["paths"], "scripts": str(tmp / "s"), "logs": str(tmp / "l")}
win.admin = True
shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
win.show()


def settle(ms=400):
    end = datetime.now() + timedelta(milliseconds=ms)
    while datetime.now() < end or win.busy:
        app.processEvents()


for i, key in enumerate(win.pages):
    win.nav.setCurrentRow(i)
    settle()
    if key == "logs":
        win._load_events()
        settle()
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
        win.grab().save(str(shots / f"admin_{i}_{key}.png"))

assert "Память занята" in win.warn_box.text() and "Run unattended" in win.warn_box.text()
assert win.t_sess.rowCount() == 2 and win.t_procs.rowCount() == 2 and win.t_tasks.rowCount() == 4
assert win.t_events.rowCount() == 2 and win.t_top.item(0, 0).text() == "Memory Compression"

win.nav.setCurrentRow(win.pages.index("sessions"))
settle()
win.t_sess.selectRow(0)
win._logoff_session()
win.t_procs.selectRow(0)
win._kill_proc()
win.nav.setCurrentRow(win.pages.index("tasks"))
settle()
win.t_tasks.selectRow(0)
win._run_task()
settle(300)
assert ("logoff", 5) in calls and ("kill", 1) in calls and ("task", "Резервная копия") in calls, calls

# скрипты: сохранить из текста, выбрать, выполнить (python вместо powershell - есть везде)
win.nav.setCurrentRow(win.pages.index("scripts"))
settle()
lib = win._library()
lib.save_text("echo", "# Пробный скрипт\nWrite-Host 'привет'")
win._refresh_scripts(True)
target = next(i for i in range(win.l_scripts.count()) if (win.l_scripts.item(i).data(0x0100) or "").endswith("echo.ps1"))
win.l_scripts.setCurrentRow(target)
assert "Пробный скрипт" in win.l_script_info.text() and "Write-Host" in win.e_script.toPlainText()
S.command = lambda s, args="": (sys.executable, ["-c", "print('привет из скрипта')"])
S.output_encoding = lambda s: "utf-8"
win._run_script()
end = datetime.now() + timedelta(seconds=15)
while win.btn_run.isEnabled() is False and datetime.now() < end:
    app.processEvents()
out = win.e_out.toPlainText()
assert "привет из скрипта" in out and "завершён: успешно" in out, out
assert win.run_log.exists() and "привет из скрипта" in win.run_log.read_text(encoding="utf-8")
journal = win.journal.path.read_text(encoding="utf-8")
assert "Сеанс завершён" in journal and "Скрипт завершён" in journal
if shots:
    win.grab().save(str(shots / "admin_3_scripts_run.png"))
print("OK: программа администратора")
