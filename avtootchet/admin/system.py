"""Сведения о сервере для программы администратора.

Принцип - не нагружать сервер, который мы же и обслуживаем:
- процессы, память, диски - psutil (читает счётчики Windows, без внешних программ);
- сеансы пользователей - напрямую через Windows API (WTS), без запуска quser;
- задачи Планировщика, журнал событий, Tailscale, схема питания - одна короткая внешняя
  команда с пониженным приоритетом, и только когда открыт нужный раздел или нажата кнопка.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - в сборке psutil есть всегда
    psutil = None

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL = 0x00004000
PS = "powershell"


# ------------------------------------------------------------------ внешние команды
def run_cmd(args: list[str], timeout: float = 30) -> str:
    """Внешняя команда без окна и с пониженным приоритетом. Возвращает вывод (stdout)."""
    flags = (CREATE_NO_WINDOW | BELOW_NORMAL) if IS_WIN else 0
    r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    out = r.stdout
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return out.decode(enc)
        except UnicodeDecodeError:
            continue
    return out.decode("utf-8", "replace")


def run_ps(script: str, timeout: float = 60) -> str:
    """Короткий скрипт PowerShell, вывод в UTF-8."""
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ErrorActionPreference='SilentlyContinue';"
    return run_cmd([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", prefix + script], timeout)


def ps_json(script: str, timeout: float = 60) -> list[dict]:
    """Скрипт, который печатает JSON (массив или объект). Пустой вывод - пустой список."""
    text = run_ps(script, timeout).strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


# ------------------------------------------------------------------ обзор
@dataclass
class Disk:
    mount: str
    total_gb: float
    free_gb: float

    @property
    def free_pct(self) -> float:
        return 100 * self.free_gb / self.total_gb if self.total_gb else 0


@dataclass
class Overview:
    cpu: float
    mem_used_gb: float
    mem_total_gb: float
    mem_pct: float
    compressed_gb: float
    disks: list[Disk]
    boot: datetime
    self_mb: float
    self_cpu: float

    @property
    def uptime(self) -> timedelta:
        return datetime.now() - self.boot


_self_proc = None


def overview() -> Overview:
    global _self_proc
    vm = psutil.virtual_memory()
    disks = []
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype:
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        disks.append(Disk(p.mountpoint.rstrip("\\"), u.total / 2**30, u.free / 2**30))
    comp = 0.0
    for pr in psutil.process_iter(["name"]):
        if pr.info["name"] == "Memory Compression":
            try:
                comp = pr.memory_info().rss / 2**30
            except (psutil.Error, OSError):
                pass
            break
    if _self_proc is None:
        _self_proc = psutil.Process()
        _self_proc.cpu_percent(None)
    return Overview(cpu=psutil.cpu_percent(None), mem_used_gb=(vm.total - vm.available) / 2**30,
                    mem_total_gb=vm.total / 2**30, mem_pct=vm.percent, compressed_gb=comp,
                    disks=disks, boot=datetime.fromtimestamp(psutil.boot_time()),
                    self_mb=_self_proc.memory_info().rss / 2**20,
                    self_cpu=_self_proc.cpu_percent(None) / max(psutil.cpu_count() or 1, 1))


def top_memory(n: int = 10) -> list[dict]:
    rows = []
    for pr in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        mi = pr.info.get("memory_info")
        if mi is None:
            continue
        rows.append({"pid": pr.info["pid"], "name": pr.info["name"] or "",
                     "user": short_user(pr.info.get("username")), "mb": mi.rss / 2**20})
    rows.sort(key=lambda r: -r["mb"])
    return rows[:n]


# ------------------------------------------------------------------ процессы 1С
ONEC_NAMES = {"1cv8.exe", "1cv8c.exe", "1cv8s.exe", "1cv8", "1cv8c"}
OUR_PREFIXES = ("acmineevo", "avtootchet")
_PLATFORM_RE = re.compile(r"\\1cv8\\(\d+\.\d+\.\d+\.\d+)\\bin\\", re.I)


def platform_of(exe: str) -> str:
    m = _PLATFORM_RE.search(exe or "")
    if not m:
        return ""
    return m.group(1) + (" (32-бит)" if "(x86)" in exe else "")


def short_user(u: str | None) -> str:
    return (u or "").split("\\")[-1]


def client_kind(name: str) -> str:
    n = name.lower()
    if n.startswith("1cv8c"):
        return "тонкий клиент"
    if n.startswith("1cv8"):
        return "толстый клиент"
    return "программа ACMineevo"


class ProcSampler:
    """Процессы 1С и наших программ. Хранит объекты процессов между обновлениями,
    чтобы считать загрузку процессора без лишних замеров."""

    def __init__(self):
        self._cache: dict[int, object] = {}

    def sample(self, today: date | None = None) -> list[dict]:
        today = today or date.today()
        ncpu = max(psutil.cpu_count() or 1, 1)
        seen, rows = set(), []
        for pr in psutil.process_iter(["pid", "name", "username", "create_time", "exe"]):
            name = (pr.info["name"] or "")
            low = name.lower()
            if low not in ONEC_NAMES and not low.startswith(OUR_PREFIXES):
                continue
            pid = pr.info["pid"]
            seen.add(pid)
            p = self._cache.get(pid)
            if p is None:
                p = pr
                self._cache[pid] = p
                try:
                    p.cpu_percent(None)
                except psutil.Error:
                    pass
                cpu = 0.0
            else:
                try:
                    cpu = p.cpu_percent(None) / ncpu
                except psutil.Error:
                    cpu = 0.0
            try:
                mb = p.memory_info().rss / 2**20
            except psutil.Error:
                mb = 0.0
            started = datetime.fromtimestamp(pr.info["create_time"] or 0)
            rows.append({"pid": pid, "name": name, "kind": client_kind(name),
                         "user": short_user(pr.info.get("username")), "started": started,
                         "platform": platform_of(pr.info.get("exe") or ""), "mb": mb, "cpu": cpu,
                         "stale": started.date() < today})
        for pid in list(self._cache):
            if pid not in seen:
                del self._cache[pid]
        rows.sort(key=lambda r: (not r["stale"], r["user"], r["started"]))
        return rows


def kill_process(pid: int) -> None:
    p = psutil.Process(pid)
    p.terminate()
    try:
        p.wait(10)
    except psutil.TimeoutExpired:
        p.kill()


# ------------------------------------------------------------------ сеансы (WTS)
STATES = {0: "активен", 1: "подключён", 2: "подключение", 3: "наблюдение", 4: "отключён",
          5: "простаивает", 6: "ожидает", 7: "сброс", 8: "не работает", 9: "запуск"}


@dataclass
class Session:
    id: int
    station: str
    user: str
    state: str
    logon: datetime | None = None
    idle_min: int | None = None
    client: str = ""


def _filetime(v: int) -> datetime | None:
    if not v:
        return None
    return datetime(1601, 1, 1) + timedelta(microseconds=v // 10)


def sessions() -> list[Session]:
    """Сеансы Windows с пользователем (консоль и удалённые рабочие столы)."""
    if not IS_WIN:
        return []
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class SessionInfo(ctypes.Structure):
        _fields_ = [("SessionId", wintypes.DWORD), ("pWinStationName", wintypes.LPWSTR),
                    ("State", ctypes.c_int)]

    class WtsInfo(ctypes.Structure):
        _fields_ = [("State", ctypes.c_int), ("SessionId", wintypes.DWORD),
                    ("IncomingBytes", wintypes.DWORD), ("OutgoingBytes", wintypes.DWORD),
                    ("IncomingFrames", wintypes.DWORD), ("OutgoingFrames", wintypes.DWORD),
                    ("IncomingCompressedBytes", wintypes.DWORD), ("OutgoingCompressedBytes", wintypes.DWORD),
                    ("WinStationName", wintypes.WCHAR * 32), ("Domain", wintypes.WCHAR * 17),
                    ("UserName", wintypes.WCHAR * 21),
                    ("ConnectTime", ctypes.c_longlong), ("DisconnectTime", ctypes.c_longlong),
                    ("LastInputTime", ctypes.c_longlong), ("LogonTime", ctypes.c_longlong),
                    ("CurrentTime", ctypes.c_longlong)]

    wts = ctypes.windll.wtsapi32
    pinfo, count = ctypes.POINTER(SessionInfo)(), wintypes.DWORD()
    if not wts.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(pinfo), ctypes.byref(count)):
        raise OSError("Не удалось получить список сеансов Windows")

    def query(sid: int, cls: int):
        buf, size = wintypes.LPWSTR(), wintypes.DWORD()
        if not wts.WTSQuerySessionInformationW(None, sid, cls, ctypes.byref(buf), ctypes.byref(size)):
            return None
        return buf

    out = []
    try:
        for i in range(count.value):
            s = pinfo[i]
            ub = query(s.SessionId, 5)                       # WTSUserName
            user = ub.value if ub else ""
            if ub:
                wts.WTSFreeMemory(ub)
            if not user:
                continue
            cb = query(s.SessionId, 10)                      # WTSClientName
            client = cb.value if cb else ""
            if cb:
                wts.WTSFreeMemory(cb)
            logon, idle = None, None
            ib = query(s.SessionId, 24)                      # WTSSessionInfo
            if ib:
                info = ctypes.cast(ib, ctypes.POINTER(WtsInfo)).contents
                logon = _filetime(info.LogonTime)
                if info.LastInputTime and info.CurrentTime:
                    idle = max(0, int((info.CurrentTime - info.LastInputTime) / 600_000_000))
                elif info.DisconnectTime and info.CurrentTime and s.State == 4:
                    idle = max(0, int((info.CurrentTime - info.DisconnectTime) / 600_000_000))
                wts.WTSFreeMemory(ib)
            out.append(Session(s.SessionId, s.pWinStationName or "", user,
                               STATES.get(s.State, str(s.State)), logon, idle, client))
    finally:
        wts.WTSFreeMemory(pinfo)
    out.sort(key=lambda x: (x.state != "отключён", x.user.lower()))
    return out


def logoff(session_id: int) -> None:
    import ctypes  # noqa: PLC0415
    if not ctypes.windll.wtsapi32.WTSLogoffSession(None, session_id, False):
        raise OSError(f"Не удалось завершить сеанс {session_id} (нужны права администратора)")


def send_message(session_id: int, title: str, text: str, timeout_s: int = 300) -> None:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415
    resp = wintypes.DWORD()
    ok = ctypes.windll.wtsapi32.WTSSendMessageW(
        None, session_id, title, len(title) * 2, text, len(text) * 2, 0x40, timeout_s,
        ctypes.byref(resp), False)
    if not ok:
        raise OSError(f"Не удалось отправить сообщение в сеанс {session_id}")


# ------------------------------------------------------------------ задачи Планировщика
TASKS_PS = r"""
$r = @(Get-ScheduledTask -TaskPath '\' | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  [pscustomobject]@{
    Name = $_.TaskName; State = [string]$_.State
    Last = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('s') } else { '' }
    Next = if ($i.NextRunTime) { $i.NextRunTime.ToString('s') } else { '' }
    Result = [int64]$i.LastTaskResult; Missed = $i.NumberOfMissedRuns
    User = $_.Principal.UserId
    Triggers = @($_.Triggers | ForEach-Object { [pscustomobject]@{
      Kind = $_.CimClass.CimClassName; Start = [string]$_.StartBoundary
      Days = [string]$_.DaysOfWeek; Enabled = $_.Enabled } })
  } })
ConvertTo-Json -InputObject $r -Depth 4 -Compress
"""

RESULTS = {0: "успешно", 0x41300: "готова", 0x41301: "выполняется сейчас", 0x41302: "отключена",
           0x41303: "ещё не запускалась", 0x41306: "прервана", 0x8004131F: "уже выполняется",
           0x800710E0: "отклонена оператором", 1: "ошибка (код 1)", 2: "файл не найден (код 2)"}
STATE_RU = {"Ready": "готова", "Running": "выполняется", "Disabled": "отключена", "Queued": "в очереди"}
WEEKDAYS = {1: "вс", 2: "пн", 4: "вт", 8: "ср", 16: "чт", 32: "пт", 64: "сб"}


@dataclass
class Task:
    name: str
    state: str
    schedule: str
    last: datetime | None
    next: datetime | None
    result: str
    ok: bool
    user: str = ""


def _iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def describe_trigger(t: dict) -> str:
    kind = (t.get("Kind") or "").replace("MSFT_Task", "").replace("Trigger", "")
    start = _iso((t.get("Start") or "")[:19])
    hm = start.strftime("%H:%M") if start else ""
    if kind == "Daily":
        return f"каждый день {hm}"
    if kind == "Weekly":
        try:
            mask = int(t.get("Days") or 0)
        except ValueError:
            mask = 0
        days = ", ".join(v for k, v in sorted(WEEKDAYS.items(), key=lambda kv: (kv[0] == 1, kv[0])) if mask & k)
        return f"по неделям ({days or '?'}) {hm}".strip()
    if kind == "Boot":
        return "при включении"
    if kind == "Logon":
        return "при входе"
    if kind == "Time":
        return f"один раз {start:%d.%m %H:%M}" if start else "один раз"
    return kind.lower() or "—"


def parse_tasks(rows: list[dict]) -> list[Task]:
    out = []
    for r in rows:
        code = int(r.get("Result") or 0) & 0xFFFFFFFF
        trig = r.get("Triggers") or []
        if isinstance(trig, dict):
            trig = [trig]
        sched = "; ".join(describe_trigger(t) for t in trig) or "по запросу"
        state = STATE_RU.get(r.get("State") or "", r.get("State") or "")
        last = _iso(r.get("Last") or "")
        ok = code in (0, 0x41300, 0x41301, 0x41303) or last is None
        res = RESULTS.get(code, f"ошибка 0x{code:08X}") if last else "ещё не запускалась"
        out.append(Task(r.get("Name") or "", state, sched, last, _iso(r.get("Next") or ""),
                        res, ok, short_user(r.get("User"))))
    out.sort(key=lambda t: (t.ok, t.next or datetime.max))
    return out


def tasks() -> list[Task]:
    return parse_tasks(ps_json(TASKS_PS))


def run_task(name: str) -> None:
    run_ps(f"Start-ScheduledTask -TaskName '{name.replace(chr(39), chr(39) * 2)}'")


# ------------------------------------------------------------------ события включения/сна
EVENT_IDS = {41: "аварийная перезагрузка (питание или зависание)",
             42: "переход в сон", 107: "выход из сна", 1074: "выключение/перезагрузка по команде",
             6005: "Windows запущена", 6006: "штатное выключение", 6008: "неожиданное выключение"}
EVENTS_PS = r"""
$e = @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,42,107,1074,6005,6006,6008;
       StartTime=(Get-Date).AddDays(-%d)} -MaxEvents 200 | ForEach-Object {
  [pscustomobject]@{ Time = $_.TimeCreated.ToString('s'); Id = $_.Id;
                     Text = (($_.Message -split "`n")[0]).Trim() } })
ConvertTo-Json -InputObject $e -Compress
"""


def power_events(days: int = 7) -> list[dict]:
    rows = ps_json(EVENTS_PS % days)
    return [{"time": _iso(r.get("Time") or ""), "id": r.get("Id"),
             "what": EVENT_IDS.get(r.get("Id"), str(r.get("Id"))), "text": r.get("Text") or ""}
            for r in rows]


# ------------------------------------------------------------------ питание и Tailscale
def parse_sleep_ac(text: str) -> int | None:
    """Таймаут сна от сети в секундах из вывода powercfg /query (последние два значения - AC и DC)."""
    vals = re.findall(r"0x([0-9a-fA-F]{8})", text)
    if len(vals) < 2:
        return None
    return int(vals[-2], 16)


def power_state() -> dict:
    active = run_cmd(["powercfg", "/getactivescheme"], 15)
    m = re.search(r"\((.+)\)", active)
    sleep = parse_sleep_ac(run_cmd(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"], 15))
    return {"scheme": m.group(1) if m else active.strip(), "sleep_s": sleep}


TAILSCALE = r"C:\Program Files\Tailscale\tailscale.exe"


def parse_tailscale(status: dict, prefs: dict | None) -> dict:
    me = status.get("Self") or {}
    peers = []
    for p in (status.get("Peer") or {}).values():
        peers.append({"name": p.get("HostName") or "", "online": bool(p.get("Online")),
                      "os": p.get("OS") or "", "ip": (p.get("TailscaleIPs") or [""])[0]})
    peers.sort(key=lambda p: (not p["online"], p["name"].lower()))
    return {"online": bool(me.get("Online")), "name": me.get("HostName") or "",
            "ip": (me.get("TailscaleIPs") or [""])[0], "state": status.get("BackendState") or "",
            "unattended": None if prefs is None else bool(prefs.get("ForceDaemon")), "peers": peers}


def tailscale() -> dict | None:
    if not Path(TAILSCALE).exists():
        return None
    st = json.loads(run_cmd([TAILSCALE, "status", "--json"], 20) or "{}")
    try:
        prefs = json.loads(run_cmd([TAILSCALE, "debug", "prefs"], 20) or "{}")
    except (ValueError, OSError):
        prefs = None
    return parse_tailscale(st, prefs)


# ------------------------------------------------------------------ журналы и папки
def tail(path: str | Path, n: int = 20) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8-sig", "replace")
    return [ln for ln in text.splitlines() if ln.strip()][-n:]


@dataclass
class BackupStatus:
    last: datetime | None
    errors: int | None
    lines: list[str] = field(default_factory=list)


_BACKUP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+Итог: ошибок (\d+)")


def backup_status(path: str | Path) -> BackupStatus:
    lines = tail(path, 40)
    last, errors = None, None
    for ln in reversed(lines):
        m = _BACKUP_RE.match(ln)
        if m:
            last, errors = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), int(m.group(2))
            break
    return BackupStatus(last, errors, lines[-12:])


def folder_status(path: str | Path, fresh_min: int = 10) -> dict:
    """Размер папки, число файлов и сколько из них менялись за последние fresh_min минут."""
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    total, files, fresh, newest = 0, 0, 0, 0.0
    limit = datetime.now().timestamp() - fresh_min * 60
    for root, _dirs, names in os.walk(p):
        for n in names:
            try:
                st = os.stat(os.path.join(root, n))
            except OSError:
                continue
            files += 1
            total += st.st_size
            newest = max(newest, st.st_mtime)
            if st.st_mtime >= limit:
                fresh += 1
    return {"exists": True, "mb": total / 2**20, "files": files, "fresh": fresh,
            "newest": datetime.fromtimestamp(newest) if newest else None}


def stock_report_error(folder: str | Path) -> str | None:
    p = Path(folder)
    if not p.exists():
        return None
    for f in p.glob("ОШИБКА*.txt"):
        return f.name
    return None


def perf_collectors() -> list[dict]:
    """Сборщики счётчиков производительности (logman): имя и состояние."""
    text = run_cmd(["logman", "query"], 20)
    rows = []
    for ln in text.splitlines():
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) >= 3 and not set(parts[0]) <= set("-="):
            if parts[0].lower().startswith(("группа", "data collector", "команда", "the command")):
                continue
            rows.append({"name": parts[0], "type": parts[1], "state": parts[2]})
    return rows


# ------------------------------------------------------------------ предупреждения
def warnings(ov: Overview | None, procs: list[dict], sess: list[Session], power: dict | None,
             ts: dict | None, backup: BackupStatus | None, task_list: list[Task] | None,
             now: datetime | None = None) -> list[str]:
    """Что требует внимания администратора - коротко, по одной строке."""
    now = now or datetime.now()
    w = []
    if ov:
        if ov.mem_pct >= 85:
            w.append(f"Память занята на {ov.mem_pct:.0f}% — закройте брошенные сеансы")
        for d in ov.disks:
            if d.free_pct < 10:
                w.append(f"На диске {d.mount} свободно {d.free_gb:.0f} ГБ ({d.free_pct:.0f}%)")
        if ov.uptime > timedelta(days=8):
            w.append(f"Сервер не перезагружался {ov.uptime.days} дн.")
    stale = [p for p in procs if p["stale"] and p["kind"] != "программа ACMineevo"]
    if stale:
        users = ", ".join(sorted({p["user"] for p in stale}))
        w.append(f"Процессов 1С со вчера и раньше: {len(stale)} ({users})")
    plats = {p["platform"].split(" ")[0] for p in procs if p["platform"]}
    if len(plats) > 1:
        w.append("1С запущена на разных версиях платформы: " + ", ".join(sorted(plats)))
    for s in sess:
        if s.state == "отключён" and s.idle_min is not None and s.idle_min >= 8 * 60:
            w.append(f"Сеанс {s.user} отключён и брошен {s.idle_min // 60} ч")
    if power and power.get("sleep_s"):
        w.append(f"В схеме «{power['scheme']}» включён сон через {power['sleep_s'] // 60} мин — сервер уснёт")
    if ts is not None:
        if not ts["online"]:
            w.append("Tailscale: сервер не в сети")
        if ts["unattended"] is False:
            w.append("Tailscale не в режиме «Run unattended»: выход пользователя отключит доступ")
    if backup is not None:
        if backup.last is None:
            w.append("Нет записи об успешной резервной копии")
        else:
            if now - backup.last > timedelta(hours=26):
                w.append(f"Последняя резервная копия {backup.last:%d.%m %H:%M} — больше суток назад")
            if backup.errors:
                w.append(f"Последняя резервная копия с ошибками: {backup.errors}")
    for t in task_list or []:
        if not t.ok:
            w.append(f"Задача «{t.name}»: {t.result}")
    return w
