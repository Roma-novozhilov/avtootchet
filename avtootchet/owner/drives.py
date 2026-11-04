"""Поиск подключённых флешек и запись отчёта на них.

Программа может работать на сервере через удалённый рабочий стол. Тогда флешка
вставлена в компьютер владельца и видна на сервере как диск «tsclient»
(если в подключении к удалённому столу включены «Диски»)."""
from __future__ import annotations

import os
import shutil
import string
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

DRIVE_REMOVABLE = 2


@dataclass
class Drive:
    root: str
    label: str
    free: int
    remote: bool = False   # диск компьютера владельца через удалённый рабочий стол

    @property
    def title(self) -> str:
        gb = self.free / 1024 ** 3
        if self.remote:
            letter = self.root.rstrip("\\")[-1]
            return f"{letter}: на вашем компьютере (через удалённый стол)  (свободно {gb:.1f} ГБ)"
        return f"{self.root}  {self.label or 'Съёмный диск'}  (свободно {gb:.1f} ГБ)"


def in_remote_session() -> bool:
    return os.environ.get("SESSIONNAME", "").upper().startswith("RDP")


def _probe_tsclient(letter: str) -> Drive | None:
    root = f"\\\\tsclient\\{letter}\\"
    try:
        if not os.path.isdir(root):
            return None
        return Drive(root, "", shutil.disk_usage(root).free, remote=True)
    except OSError:
        return None


def remote_drives() -> list[Drive]:
    """Диски компьютера владельца, проброшенные в удалённый сеанс. Системный C: не показываем."""
    if os.name != "nt" or not in_remote_session():
        return []
    letters = [c for c in string.ascii_uppercase if c != "C"]
    with ThreadPoolExecutor(max_workers=len(letters)) as pool:
        found = pool.map(_probe_tsclient, letters)
    return [d for d in found if d]


def removable_drives() -> list[Drive]:
    fake = os.environ.get("AVTO_FAKE_USB")  # для проверки без Windows
    if fake:
        return [Drive(fake, "ТЕСТ", shutil.disk_usage(fake).free)]
    if os.name != "nt":
        return []
    import ctypes  # noqa: PLC0415

    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    result = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask >> i & 1:
            continue
        root = f"{letter}:\\"
        if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) != DRIVE_REMOVABLE:
            continue
        label = ctypes.create_unicode_buffer(261)
        k32.GetVolumeInformationW(ctypes.c_wchar_p(root), label, 261, None, None, None, None, 0)
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            continue  # картридер без карты
        result.append(Drive(root, label.value, free))
    return result + remote_drives()


def write_to_drive(drive_root: str, filename: str, data: bytes) -> Path:
    root = Path(drive_root)
    if not root.exists():
        raise OSError("Папка или флешка не найдена (возможно, флешку вынули)")
    target = root / filename
    n = 2
    while target.exists():
        target = root / f"{Path(filename).stem} ({n}){Path(filename).suffix}"
        n += 1
    with open(target, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return target
