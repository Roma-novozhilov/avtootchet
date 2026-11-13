"""Запуски витрины без окна (для задач Windows):

    ACMineevo-Vitrina.exe --collect     сбор из Далиона в файл витрины (задача в 6:00)
    ACMineevo-Vitrina.exe --serve       сайт (задача «при включении компьютера»)
    ACMineevo-Vitrina.exe --demo        сайт на выдуманных данных - показать без Далиона

Настройки - те же, что задаются в окне программы (%APPDATA%\\AvtoOtchet\\vitrina_settings.json).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from ..common.journal import Journal
from ..common.paths import app_dir, resource, temp_dir

DEFAULTS = {
    "com": {"server": r"C:\1C\Dalion", "base": "", "user": "", "password": "", "platform": ""},
    "app_lock": {},
    "data": {"file": "", "months": 6, "in_codes": "0", "out_codes": "1"},
    # lines: «21=Городская; 45=Авито» - кусок номера линии = название на сайте
    "site": {"host": "0.0.0.0", "port": 8765, "lines": "", "full_phones": False,
             "owner_lock": {}, "marketer_lock": {}},
}


def settings():
    from ..common.settings import Settings  # noqa: PLC0415 - DPAPI только на Windows
    return Settings("vitrina", DEFAULTS)


def data_file(s) -> Path:
    return Path(s["data"].get("file") or (app_dir() / "vitrina.db"))


def parse_lines(text: str) -> dict:
    out = {}
    for part in re.split(r"[;\n]", text or ""):
        k, sep, v = part.partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def web_dir() -> Path:
    return resource("avtootchet/vitrina/web")


def do_collect(s, journal: Journal) -> dict:
    from ..common.com import run_released  # noqa: PLC0415
    from ..marketing.export import Directions  # noqa: PLC0415
    from ..owner.sources import ComSource  # noqa: PLC0415
    from .collect import collect_to_file  # noqa: PLC0415
    c, d = s["com"], s["data"]
    src = ComSource(c["server"], c.get("base", ""), c["user"], c["password"], platform=c.get("platform", ""))
    dirs = Directions.parse(d.get("in_codes", "0"), d.get("out_codes", "1"))

    def work():
        src._check_settings()
        return collect_to_file(src._connect(), data_file(s), int(d.get("months") or 6), dirs)

    meta, err = run_released(work)
    if err:
        journal.write("Сбор витрины: ошибка", f"{type(err).__name__}: {err}"[:300])
        raise err
    journal.write("Сбор витрины", ", ".join(f"{k} {v}" for k, v in meta["counts"].items())
                  + (f"; ошибки: {', '.join(meta['errors'])}" if meta["errors"] else ""))
    return meta


DEMO_PASSWORDS = {"owner": "demo", "marketer": "marketing"}


def make_app(s, path: Path | None = None, demo: bool = False):
    from .server import App, Store, make_lock  # noqa: PLC0415
    site = s["site"]
    store = Store(path or data_file(s), parse_lines(site.get("lines", "")), bool(site.get("full_phones")))
    if demo:   # данные выдуманные - пароли простые и известные
        locks = {k: make_lock(v) for k, v in DEMO_PASSWORDS.items()}
    else:
        locks = {"owner": site.get("owner_lock") or {}, "marketer": site.get("marketer_lock") or {}}
    return App(store, locks, web_dir())


def do_serve(s, journal: Journal, path: Path | None = None, port: int | None = None, demo: bool = False) -> None:
    from .server import serve  # noqa: PLC0415
    site = s["site"]
    port = port or int(site.get("port") or 8765)
    httpd = serve(make_app(s, path, demo), site.get("host") or "0.0.0.0", port)
    journal.write("Сайт витрины запущен", f"порт {port}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv: list[str]) -> int:
    journal = Journal("vitrina")
    s = settings()
    if "--collect" in argv:
        try:
            do_collect(s, journal)
            return 0
        except Exception:  # noqa: BLE001 - причина уже в журнале
            return 1
    if "--demo" in argv:
        from .demo import write_demo  # noqa: PLC0415
        path = temp_dir() / "vitrina_demo.db"
        write_demo(path)
        do_serve(s, journal, path, demo=True)
        return 0
    if "--serve" in argv:
        if not (s["site"].get("owner_lock") or {}).get("hash"):
            journal.write("Сайт витрины не запущен", "не задан пароль владельца")
            return 2
        do_serve(s, journal)
        return 0
    return -1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
