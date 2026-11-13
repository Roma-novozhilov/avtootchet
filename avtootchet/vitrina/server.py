"""Веб-сервер витрины: страница и JSON для неё. Только стандартная библиотека Python.

* Вход по паролю. Паролей два: «владелец» (всё) и «маркетолог» (без денег). Хранятся только
  отпечатки PBKDF2 - как пароль на вход в программы АвтоОтчёта.
* После 5 неверных паролей с одного адреса вход с него закрыт на 5 минут.
* Сеанс - случайный ключ в cookie (HttpOnly, SameSite=Strict), живёт 12 часов, хранится в памяти:
  после перезапуска сервера войти нужно заново.
* Данные берутся из файла витрины; когда сбор в 6:00 его заменит, сервер перечитает сам.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import metrics as M
from .db import read_db

ITERATIONS = 200_000
SESSION_HOURS = 12
MAX_FAILS, LOCK_SECONDS = 5, 300
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


# ---------------------------------------------------------------------------- пароли
def make_lock(password: str) -> dict:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(),
            "iterations": ITERATIONS}


def check_lock(lock: dict | None, password: str) -> bool:
    if not lock or not lock.get("hash"):
        return False
    try:
        salt, want = base64.b64decode(lock["salt"]), base64.b64decode(lock["hash"])
        n = int(lock.get("iterations") or ITERATIONS)
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n), want)


# ---------------------------------------------------------------------------- данные
class Store:
    """Файл витрины в памяти; перечитывается, когда файл заменили."""

    def __init__(self, path: Path, lines: dict | None = None, full_phones: bool = False):
        self.path, self.lines, self.full_phones = Path(path), lines or {}, full_phones
        self._mtime, self._data, self._lock = None, None, threading.Lock()

    def get(self) -> M.Data | None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return self._data
        with self._lock:
            if mtime != self._mtime:
                tables, meta = read_db(self.path)
                self._data = M.Data.from_tables(tables, meta, self.lines, self.full_phones)
                self._mtime = mtime
            return self._data


class App:
    def __init__(self, store: Store, locks: dict, web_dir: Path, now=datetime.now):
        self.store, self.locks, self.web_dir, self.now = store, locks, Path(web_dir), now
        self.sessions: dict[str, tuple[str, float]] = {}
        self.fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------- сеансы
    def login(self, ip: str, password: str) -> str | None:
        with self._lock:
            recent = [t for t in self.fails.get(ip, []) if time.time() - t < LOCK_SECONDS]
            self.fails[ip] = recent
            if len(recent) >= MAX_FAILS:
                raise PermissionError("Слишком много неверных паролей. Попробуйте через 5 минут.")
        role = next((r for r in (M.OWNER, M.MARKETER) if check_lock(self.locks.get(r), password)), None)
        with self._lock:
            if role is None:
                self.fails.setdefault(ip, []).append(time.time())
                return None
            self.fails.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = (role, time.time() + SESSION_HOURS * 3600)
            return token

    def role(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            item = self.sessions.get(token)
            if not item:
                return None
            if item[1] < time.time():
                self.sessions.pop(token, None)
                return None
            return item[0]

    def logout(self, token: str | None):
        with self._lock:
            self.sessions.pop(token or "", None)

    # -------------------------------------------------------------- API
    def filters(self, q: dict) -> M.Filters:
        def day(name, default):
            try:
                return date.fromisoformat(q.get(name, [""])[0])
            except ValueError:
                return default
        today = self.now().date()
        d2 = day("to", today)
        d1 = day("from", d2 - timedelta(days=29))
        if d1 > d2:
            d1, d2 = d2, d1
        if (d2 - d1).days > 400:
            d1 = d2 - timedelta(days=400)
        return M.Filters(d1, d2, q.get("user", [""])[0], q.get("line", [""])[0])

    def api(self, path: str, q: dict, role: str):
        d = self.store.get()
        if d is None:
            return 503, {"error": "Данных ещё нет: сбор из Далиона не выполнялся."}
        f, now = self.filters(q), self.now()
        if path == "/api/meta":
            meta = dict(d.meta)
            return 200, {"role": role, "meta": meta, "options": M.filter_options(d)}
        if path == "/api/home":
            return 200, M.home(d, f, role, now)
        if path == "/api/telephony":
            return 200, M.telephony(d, f)
        if path == "/api/managers":
            return 200, M.managers(d, f, role)
        if path == "/api/finance":
            if role != M.OWNER:
                return 403, {"error": "Раздел доступен только владельцу."}
            return 200, M.finance(d, f)
        if path == "/api/list":
            key = q.get("key", [""])[0]
            try:
                return 200, M.list_rows(d, key, f, role, now)
            except PermissionError:
                return 403, {"error": "Список доступен только владельцу."}
            except KeyError:
                return 404, {"error": "Нет такого списка."}
        if path == "/api/client":
            return 200, M.client_path(d, role, q.get("code", [""])[0], q.get("call", [""])[0])
        return 404, {"error": "Нет такого адреса."}


# ---------------------------------------------------------------------------- HTTP
def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ACMineevo-Vitrina"
        sys_version = ""

        def log_message(self, fmt, *args):     # без журнала запросов в консоль
            pass

        def _token(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "vs":
                    return v
            return None

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                             "frame-ancestors 'none'; form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj, extra: dict | None = None, role: str = M.OWNER):
            body = json.dumps(M.strip_money(obj, role), ensure_ascii=False, default=str).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8", extra)

        def do_GET(self):  # noqa: N802
            url = urlsplit(self.path)
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                try:
                    body = (app.web_dir / name).read_bytes()
                except OSError:
                    return self._send(500, "Нет файлов сайта".encode(), "text/plain; charset=utf-8")
                return self._send(200, body, ctype)
            if url.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if url.path.startswith("/api/"):
                role = app.role(self._token())
                if not role:
                    return self._json(401, {"error": "Нужно войти."})
                try:
                    code, obj = app.api(url.path, parse_qs(url.query), role)
                except Exception as e:  # noqa: BLE001 - ошибка расчёта не должна ронять сервер
                    code, obj = 500, {"error": f"Ошибка расчёта: {type(e).__name__}: {e}"}
                return self._json(code, obj, role=role)
            self._send(404, b"Not found", "text/plain")

        def do_POST(self):  # noqa: N802
            url = urlsplit(self.path)
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            raw = self.rfile.read(n) if n else b""
            if url.path == "/api/login":
                try:
                    password = str(json.loads(raw or b"{}").get("password", ""))
                except ValueError:
                    password = ""
                try:
                    token = app.login(self.client_address[0], password)
                except PermissionError as e:
                    return self._json(429, {"error": str(e)})
                if not token:
                    return self._json(401, {"error": "Неверный пароль."})
                cookie = f"vs={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS * 3600}"
                return self._json(200, {"ok": True, "role": app.role(token)}, {"Set-Cookie": cookie})
            if url.path == "/api/logout":
                app.logout(self._token())
                return self._json(200, {"ok": True}, {"Set-Cookie": "vs=; Path=/; Max-Age=0"})
            self._send(404, b"Not found", "text/plain")

    return Handler


def serve(app: App, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
