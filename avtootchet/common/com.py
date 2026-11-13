"""Подключения к 1С через COM: гарантированное закрытие после каждой операции.

Сеанс внешнего соединения 1С живёт, пока в программе есть хоть одна ссылка на объект 1С
(соединение, запрос, выборку). В файловой базе даже простаивающий сеанс мешает остальным:
он числится в «Активных пользователях» и участвует в блокировках. Поэтому после каждой
операции (загрузка, отчёт, проверка, выгрузка) все ссылки сбрасываются, а COM в потоке
выключается - 1С закрывает сеанс сразу, а не когда-нибудь потом.
"""
from __future__ import annotations

import gc
import threading
import traceback

_local = threading.local()


def co_initialize() -> None:
    """Включить COM в текущем потоке (вызывается перед каждым подключением к 1С)."""
    import pythoncom  # noqa: PLC0415 - есть только на Windows
    pythoncom.CoInitialize()
    _local.inits = getattr(_local, "inits", 0) + 1


def release() -> None:
    """Закрыть сеансы 1С этого потока: собрать мусор (последние ссылки на объекты 1С)
    и выключить COM столько раз, сколько включали."""
    gc.collect()
    n = getattr(_local, "inits", 0)
    if not n:
        return
    _local.inits = 0
    try:
        import pythoncom  # noqa: PLC0415
    except ImportError:
        return
    for _ in range(n):
        pythoncom.CoUninitialize()


def forget_frames(exc: BaseException | None) -> None:
    """Ошибка хранит стек вызовов, а в нём - соединение с 1С. Чистим, чтобы сеанс закрылся."""
    while exc is not None:
        if exc.__traceback__ is not None:
            traceback.clear_frames(exc.__traceback__)
            exc.__traceback__ = None
        exc = exc.__cause__ or exc.__context__


def run_released(fn):
    """Выполнить fn и закрыть все сеансы 1С, которые она открыла. Возвращает (результат, ошибка)."""
    try:
        result, err = fn(), None
    except Exception as e:  # noqa: BLE001
        result, err = None, e
        forget_frames(e)   # сначала отпустить соединение из стека ошибки, потом выключать COM
    release()
    return result, err
