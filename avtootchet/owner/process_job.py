"""Построение отчёта «Анализ процессов» (PDF по ГОСТ) из Далиона. Только чтение."""
from __future__ import annotations

from datetime import date

from ..common.com import forget_frames, release
from .process import default_period, load_process
from .process_pdf import build_process_pdf
from .sources import ComSource, SourceError, make_source


def process_settings(settings) -> dict:
    p = settings["process"] if "process" in settings.data else {}
    return {"months": int(p.get("months") or 6), "stuck_days": int(p.get("stuck_days") or 7),
            "org": (p.get("org") or "Авто Центр Минеево").strip(), "city": (p.get("city") or "Иваново").strip()}


def build(settings, today: date | None = None):
    """(данные, pdf-байты)."""
    src = make_source(settings)
    if not isinstance(src, ComSource):
        raise SourceError("Анализ процессов строится только из 1С:Далион (COM)")
    ps = process_settings(settings)
    today = today or date.today()
    d1, d2 = default_period(today, ps["months"])
    src._check_settings()
    conn = src._connect()
    try:
        data = load_process(conn, d1, d2, today, ps["stuck_days"])
    except Exception as e:  # noqa: BLE001
        msg = f"Ошибка запроса к 1С: {e or type(e).__name__}"
        forget_frames(e)   # стек ошибки держит соединение - отпускаем до выключения COM
        raise SourceError(msg) from None
    finally:
        conn = None  # noqa: F841 - последняя ссылка на соединение
        release()    # сеанс 1С закрыт до построения файла
    return data, build_process_pdf(data, ps["org"], ps["city"])
