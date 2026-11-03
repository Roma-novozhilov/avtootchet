"""Передача кассовой книги с ПК бухгалтера на ПК владельца по локальной сети.

Программа владельца, пока открыта, слушает TCP-порт. Программа бухгалтера
шифрует файл общим кодом доступа и отправляет. Владелец получает файл сразу
в память - на диск он не записывается.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import socketserver
import threading
import time
from datetime import datetime
from typing import Callable

from .crypto import WrongSecret, seal, unseal

DEFAULT_PORT = 50505
MAX_SIZE = 50 * 1024 * 1024


class TransferError(Exception):
    def __init__(self, message: str, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable   # ПК не найден / не отвечает - имеет смысл поискать заново


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("соединение прервано")
        buf += chunk
    return bytes(buf)


# ------------------------------------------------------------------ отправка
def send_file(host: str, port: int, secret: str, filename: str, data: bytes,
              sender: str = "", timeout: float = 20) -> str:
    header = {"filename": filename, "sender": sender or socket.gethostname(),
              "sent_at": datetime.now().isoformat(timespec="seconds")}
    blob = seal(secret, header, data)
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(len(blob).to_bytes(8, "big") + blob)
            n = int.from_bytes(_recv_exact(s, 4), "big")
            reply = json.loads(_recv_exact(s, n))
    except socket.gaierror:
        raise TransferError(f"Не найден компьютер «{host}». Проверьте адрес ПК владельца.",
                            unreachable=True) from None
    except (ConnectionRefusedError, TimeoutError, socket.timeout):
        raise TransferError("ПК владельца не отвечает. На нём должна быть открыта программа "
                            "АвтоОтчёт (владелец), и оба ПК должны быть в одной сети.",
                            unreachable=True) from None
    except (ConnectionError, OSError) as e:
        raise TransferError(f"Ошибка сети: {e}") from None
    if not reply.get("ok"):
        raise TransferError(reply.get("message", "ПК владельца отклонил файл"))
    return reply.get("message", "Файл получен")


# ------------------------------------------------------------------ приём
class Receiver:
    """Фоновый приём файлов. on_file(header, payload, peer_ip) вызывается из
    рабочего потока - интерфейс сам переносит вызов в свой поток."""

    def __init__(self, port: int, secret: str,
                 on_file: Callable[[dict, bytes, str], str],
                 on_reject: Callable[[str, str], None] | None = None):
        self.port, self.secret = port, secret
        self.on_file, self.on_reject = on_file, on_reject
        self._server: socketserver.ThreadingTCPServer | None = None

    def start(self) -> None:
        receiver = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                sock: socket.socket = self.request
                sock.settimeout(30)
                peer = self.client_address[0]
                try:
                    size = int.from_bytes(_recv_exact(sock, 8), "big")
                    if size > MAX_SIZE:
                        raise TransferError("Файл слишком большой")
                    header, payload = unseal(receiver.secret, _recv_exact(sock, size))
                    message = receiver.on_file(header, payload, peer)
                    reply = {"ok": True, "message": message}
                except WrongSecret as e:
                    if receiver.on_reject:
                        receiver.on_reject(peer, str(e))
                    reply = {"ok": False, "message": f"{e}. Сверьте код доступа в настройках."}
                except Exception as e:  # noqa: BLE001 - отвечаем отправителю любой ошибкой
                    reply = {"ok": False, "message": f"Ошибка приёма: {e}"}
                data = json.dumps(reply, ensure_ascii=False).encode()
                try:
                    sock.sendall(len(data).to_bytes(4, "big") + data)
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("0.0.0.0", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def local_ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # адрес основного сетевого интерфейса (пакет никуда не уходит)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


# ------------------------------------------------------------------ автопоиск
# Бухгалтер рассылает по сети запрос со случайным числом. Программа владельца отвечает
# своим портом и подписью этого числа общим кодом доступа (HMAC). Бухгалтер принимает
# только ответы с верной подписью - посторонний компьютер не может выдать себя за владельца.
DISCOVERY_PORT = 50506
_ASK = b"AVR1?"
_ANSWER = b"AVR1!"


def _proof(secret: str, nonce: bytes) -> str:
    key = hashlib.sha256(b"avtootchet-discovery:" + secret.strip().upper().replace(" ", "").encode()).digest()
    return hmac.new(key, nonce, hashlib.sha256).hexdigest()


class DiscoveryResponder:
    """Отвечает на поисковые запросы, пока открыта программа владельца."""

    def __init__(self, tcp_port: int, secret: str, port: int = DISCOVERY_PORT):
        self.tcp_port, self.secret, self.port = tcp_port, secret, port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", self.port))
        s.settimeout(0.5)
        self._sock = s
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(512)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            if not data.startswith(_ASK) or len(data) != len(_ASK) + 16:
                continue
            nonce = data[len(_ASK):]
            reply = _ANSWER + json.dumps({
                "port": self.tcp_port, "host": socket.gethostname(),
                "proof": _proof(self.secret, nonce)}).encode()
            try:
                self._sock.sendto(reply, addr)
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
            self._sock = None


def _broadcast_targets() -> list[str]:
    targets = {"255.255.255.255", "127.0.0.1"}
    for ip in local_ips():
        parts = ip.split(".")
        if len(parts) == 4:
            targets.add(".".join(parts[:3] + ["255"]))   # широковещательный адрес сети /24
    return sorted(targets)


def discover_owner(secret: str, timeout: float = 2.0,
                   port: int = DISCOVERY_PORT) -> list[dict]:
    """Найти программы владельца с тем же кодом доступа: [{"ip", "port", "host"}]."""
    nonce = os.urandom(16)
    expected = _proof(secret, nonce)
    found: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        for target in _broadcast_targets():
            try:
                s.sendto(_ASK + nonce, (target, port))
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (ip, _) = s.recvfrom(1024)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            if not data.startswith(_ANSWER):
                continue
            try:
                info = json.loads(data[len(_ANSWER):])
            except ValueError:
                continue
            if hmac.compare_digest(str(info.get("proof", "")), expected):
                found[ip] = {"ip": ip, "port": int(info.get("port", DEFAULT_PORT)),
                             "host": str(info.get("host", ip))}
    # один и тот же ПК может ответить и на 127.0.0.1, и на сетевой адрес - оставляем сетевой
    if len(found) > 1:
        found.pop("127.0.0.1", None)
    return list(found.values())
