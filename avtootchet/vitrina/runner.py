"""Запуски витрины без окна (для задач Windows):

    ACMineevo-Vitrina.exe --collect     полный сбор из Далиона в файл витрины (задача в 6:00)
    ACMineevo-Vitrina.exe --collect-today  дневное обновление: свежие звонки, ЗН, задачи (10, 13, 16, 19)
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
    # users: [{"login": "Саша", "role": "owner"|"marketer", "lock": {...}}]; owner_lock/marketer_lock - от
    # версий до 0.12 (вход только по паролю), читаются как логины «владелец» и «маркетолог»
    "site": {"host": "0.0.0.0", "port": 8765, "lines": "", "full_phones": False,
             "users": [], "owner_lock": {}, "marketer_lock": {},
             # слова в типе/статусе/виде ЗН для отдельных строк в «Финансах» (через запятую)
             "special": {"warranty": "гарант", "personal": "личн", "payer": ""}},
}
LEGACY_LOGINS = {"owner": "владелец", "marketer": "маркетолог"}


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


def do_collect(s, journal: Journal, light: bool = False) -> dict:
    from ..common.com import run_released  # noqa: PLC0415
    from ..marketing.export import Directions  # noqa: PLC0415
    from ..owner.sources import ComSource  # noqa: PLC0415
    from .collect import collect_to_file  # noqa: PLC0415
    c, d = s["com"], s["data"]
    src = ComSource(c["server"], c.get("base", ""), c["user"], c["password"], platform=c.get("platform", ""))
    dirs = Directions.parse(d.get("in_codes", "0"), d.get("out_codes", "1"))

    def work():
        src._check_settings()
        return collect_to_file(src._connect(), data_file(s), int(d.get("months") or 6), dirs, light=light)

    title = "Обновление витрины" if light else "Сбор витрины"
    meta, err = run_released(work)
    if err:
        journal.write(f"{title}: ошибка", f"{type(err).__name__}: {err}"[:300])
        raise err
    journal.write(title, ", ".join(f"{k} {v}" for k, v in meta["counts"].items())
                  + (f"; ошибки: {', '.join(meta['errors'])}" if meta["errors"] else ""))
    return meta


DEMO_USERS = [("владелец", "owner", "demo"), ("маркетолог", "marketer", "marketing")]


def site_users(site: dict) -> dict:
    """Пользователи сайта: логин (без регистра) -> {login, role, lock}. Старые пароли ролей (до 0.12)
    становятся логинами «владелец» и «маркетолог», пока для роли не заведён ни один пользователь."""
    from .server import norm_login  # noqa: PLC0415
    out = {}
    for u in site.get("users") or []:
        if u.get("login") and (u.get("lock") or {}).get("hash") and u.get("role") in LEGACY_LOGINS:
            out[norm_login(u["login"])] = {"login": u["login"], "role": u["role"], "lock": u["lock"]}
    roles = {u["role"] for u in out.values()}
    for role, login in LEGACY_LOGINS.items():
        lock = site.get(f"{role}_lock") or {}
        if role not in roles and lock.get("hash") and norm_login(login) not in out:
            out[norm_login(login)] = {"login": login, "role": role, "lock": lock}
    return out


def has_owner(site: dict) -> bool:
    """Есть ли хоть один вход владельца - иначе сайт не запускается."""
    return any(u["role"] == "owner" for u in site_users(site).values())


def make_app(s, path: Path | None = None, demo: bool = False):
    from .server import App, Store, make_lock  # noqa: PLC0415
    site = s["site"]
    store = Store(path or data_file(s), parse_lines(site.get("lines", "")), bool(site.get("full_phones")))
    if demo:   # данные выдуманные - логины и пароли простые и известные
        users = site_users({"users": [{"login": l, "role": r, "lock": make_lock(p)} for l, r, p in DEMO_USERS]})
    else:
        users = site_users(site)
    special = special_rules(site)
    if demo:   # в демо «наши машины» - плательщик «Владелец»
        special["payer"] = ["владелец"]
    return App(store, users, web_dir(), special=special)


def special_rules(site: dict) -> dict:
    sp = {**DEFAULTS["site"]["special"], **(site.get("special") or {})}
    return {k: [w.strip().lower() for w in str(v or "").replace(";", ",").split(",") if w.strip()] for k, v in sp.items()}


def do_serve(s, journal: Journal, path: Path | None = None, port: int | None = None, demo: bool = False) -> None:
    from .server import serve  # noqa: PLC0415
    site = s["site"]
    port = port or int(site.get("port") or 8765)
    try:
        httpd = serve(make_app(s, path, demo), site.get("host") or "0.0.0.0", port)
    except OSError as e:
        journal.write("Сайт витрины не запущен", f"порт {port} занят другим процессом ({e})")
        raise
    journal.write("Сайт витрины запущен", f"порт {port}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv: list[str]) -> int:
    journal = Journal("vitrina")
    s = settings()
    if "--collect" in argv or "--collect-today" in argv:
        try:
            do_collect(s, journal, light="--collect-today" in argv)
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
        if not has_owner(s["site"]):
            journal.write("Сайт витрины не запущен", "не заведён ни один пользователь-владелец")
            return 2
        do_serve(s, journal)
        return 0
    return -1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
