"""Перезапуск сайта витрины одной кнопкой - вместо команд в PowerShell.

1. Находит ВСЕ запущенные копии сайта: процессы ACMineevo-Vitrina.exe с ключом --serve
   (от задачи Windows, старые, от другого пользователя) и любой процесс, который держит порт сайта.
   Окна программы и сборы (--collect, --collect-today) не трогаются.
2. Снимает задачу «ACMineevo - витрина, сайт» и завершает найденные процессы.
3. Ждёт, пока порт освободится, и запускает сайт заново: задачей Windows, если она есть
   (сайт работает от её пользователя и без окна), иначе - этим же .exe с ключом --serve.
4. Проверяет, что сайт ответил и какая у него версия (заголовок X-Vitrina-Version).

Работает с текущим файлом программы - после обновления кнопка «Обновления» ставит новый .exe,
а эта кнопка переводит на него сайт.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SITE_TASK = "ACMineevo - витрина, сайт"
EXE_NAME = "acmineevo-vitrina.exe"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class Proc:
    pid: int
    name: str
    cmdline: list[str]
    ppid: int = 0
    listens: bool = False          # держит порт сайта

    @property
    def text(self) -> str:
        return " ".join(self.cmdline[1:]) or "(без ключей)"


def choose_targets(procs: list[Proc], own: set[int]) -> list[Proc]:
    """Что останавливать: сайт (--serve или держит порт), кроме себя и своих родителя/детей."""
    out = []
    for p in procs:
        if p.pid in own:
            continue
        ours = p.name.lower() == EXE_NAME
        if p.listens or (ours and "--serve" in p.cmdline):
            out.append(p)
    return out


def own_pids() -> set[int]:
    """Сам процесс и его загрузчик (родитель с тем же .exe распаковывает программу и ждёт её).
    Дети не исключаются: сайт, запущенный этой же программой раньше, тоже нужно перезапустить."""
    pids = {os.getpid()}
    try:
        import psutil  # noqa: PLC0415
        parent = psutil.Process().parent()
        if parent and parent.name().lower() == EXE_NAME:
            pids.add(parent.pid)
    except Exception:  # noqa: BLE001
        pass
    return pids


def list_processes(port: int) -> list[Proc]:
    import psutil  # noqa: PLC0415
    listening = set()
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                listening.add(c.pid)
    except Exception:  # noqa: BLE001 - без прав видно не всё; останутся процессы по имени
        pass
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
        info = p.info
        name = info.get("name") or ""
        if name.lower() != EXE_NAME and info["pid"] not in listening:
            continue
        out.append(Proc(info["pid"], name, info.get("cmdline") or [name], info.get("ppid") or 0,
                        info["pid"] in listening))
    return out


def _run(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, timeout=60)
    out = (r.stdout or b"") + (r.stderr or b"")
    for enc in ("cp866", "cp1251", "utf-8"):
        try:
            return r.returncode, out.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return r.returncode, out.decode("utf-8", "replace").strip()


def task_exists(name: str = SITE_TASK) -> bool:
    return _run(["schtasks", "/Query", "/TN", name])[0] == 0


def kill(pid: int) -> str | None:
    """None = остановлен; иначе текст ошибки."""
    try:
        import psutil  # noqa: PLC0415
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            c.kill()
        p.kill()
        p.wait(10)
        return None
    except Exception:  # noqa: BLE001 - чужой пользователь: пробуем taskkill
        pass
    code, text = _run(["taskkill", "/PID", str(pid), "/T", "/F"])
    return None if code == 0 or "не найден" in text.lower() or "not found" in text.lower() else text


def port_free(port: int) -> bool:
    import socket  # noqa: PLC0415
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def site_version(port: int) -> str | None:
    """Версия работающего сайта; None - не отвечает; '' - старый сайт без заголовка версии."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            return r.headers.get("X-Vitrina-Version") or ""
    except Exception:  # noqa: BLE001
        return None


def start_detached(exe: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # новый процесс распакуется сам, а не в нашу папку
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # DETACHED | NEW_GROUP
    subprocess.Popen([str(exe), "--serve"], env=env, close_fds=True, creationflags=flags, cwd=str(exe.parent))


def restart(port: int, exe: Path, log=lambda *_: None) -> str:
    """Перезапустить сайт. Возвращает отчёт для окна; при неудаче - RuntimeError с причиной."""
    lines = []
    has_task = task_exists()
    if has_task:
        _run(["schtasks", "/End", "/TN", SITE_TASK])
        lines.append(f"Задача «{SITE_TASK}» снята.")
    targets = choose_targets(list_processes(port), own_pids())
    failed = []
    for p in targets:
        err = kill(p.pid)
        if err:
            failed.append(f"PID {p.pid} ({p.text}): {err}")
        else:
            lines.append(f"Остановлен PID {p.pid}: {p.text}")
    if not targets:
        lines.append("Запущенных копий сайта не было.")
    for _ in range(30):
        if port_free(port):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Порт {} всё ещё занят - старый сайт не остановился.\n{}\n\nЗапустите программу от "
                           "имени администратора и нажмите кнопку ещё раз.".format(port, "\n".join(failed)))
    if has_task:
        code, text = _run(["schtasks", "/Run", "/TN", SITE_TASK])
        if code != 0:
            raise RuntimeError(f"Не удалось запустить задачу «{SITE_TASK}»: {text}\n\n"
                               "Запустите программу от имени администратора.")
        lines.append(f"Сайт запущен задачей «{SITE_TASK}».")
    else:
        start_detached(exe)
        lines.append("Задачи Windows нет - сайт запущен этой программой; после перезагрузки сервера сам не запустится (задачу ставит tools\\setup_vitrina.ps1).")
    for _ in range(40):
        ver = site_version(port)
        if ver is not None:
            lines.append(f"Сайт отвечает, версия {ver or 'старая (до 0.11.3)'}.")
            log("Сайт витрины перезапущен", f"версия {ver or '?'}")
            return "\n".join(lines)
        time.sleep(0.5)
    raise RuntimeError("\n".join(lines) + "\n\nСайт не ответил за 20 секунд - смотрите журнал витрины.")
