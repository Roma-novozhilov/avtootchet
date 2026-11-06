"""Утренний отчёт «Залежавшиеся запчасти» без окна: запускается планировщиком Windows

    AvtoOtchet-Vladelec.exe --stock-report

Читает Далион (только чтение), сохраняет зашифрованный Excel в папку из настроек и
завершается. В папке остаётся только свежий отчёт: старые удаляются ПОСЛЕ того, как
новый записан. Если отчёт построить не удалось - старый не трогается, рядом появляется
«ОШИБКА — отчёт не построен.txt» с причиной (при следующем удачном запуске он удаляется).
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from ..common.journal import Journal
from ..common.settings import Settings
from ..common.xlsx_crypto import check_password, encrypt_xlsx
from .sources import ComSource, SourceError, make_source
from .stock import group_words, load_stock
from .stock_report import build_stock_report, stock_filename

PREFIX = "Залежавшиеся запчасти "
ERROR_FILE = "ОШИБКА — отчёт не построен.txt"
DEFAULT_FOLDER = r"F:\Отчёты\Склад"


def stock_settings(settings) -> dict:
    st = settings["stock"] if "stock" in settings.data else {}
    return {"folder": st.get("folder") or DEFAULT_FOLDER,
            "days": int(st.get("days") or 14),
            "months": int(st.get("months", 6) or 0),
            "exclude": st.get("exclude", "Масло, Фильтр")}


def build(settings, today: date | None = None):
    """Данные и незашифрованный xlsx (в память)."""
    src = make_source(settings)
    if not isinstance(src, ComSource):
        raise SourceError("Отчёт по складу строится только из 1С:Далион (COM)")
    st = stock_settings(settings)
    src._check_settings()
    conn = src._connect()
    try:
        data = load_stock(conn, today or date.today(), st["days"], group_words(st["exclude"]),
                          st["months"])
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"Ошибка запроса к 1С: {e or type(e).__name__}") from None
    return data, build_stock_report(data)


def save_rotating(folder: Path, name: str, payload: bytes) -> Path:
    """Записать новый отчёт и удалить прежние. Если файл с таким именем открыт в Excel -
    пишем рядом с временем в имени."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    tmp = folder / (name + ".tmp")
    tmp.write_bytes(payload)
    try:
        tmp.replace(target)
    except PermissionError:
        target = folder / name.replace(".xlsx", f" {datetime.now():%H-%M}.xlsx")
        tmp.replace(target)
    for old in folder.glob(PREFIX + "*.xlsx"):
        if old != target:
            try:
                old.unlink()
            except OSError:
                pass             # открыт в Excel - удалится в следующий раз
    try:
        (folder / ERROR_FILE).unlink()
    except OSError:
        pass
    return target


def run_morning(settings=None, today: date | None = None) -> int:
    """Код возврата: 0 - отчёт записан, 1 - ошибка (записана в папку и журнал)."""
    from .app import DEFAULTS  # noqa: PLC0415  - те же настройки, что у окна программы
    settings = settings or Settings("owner", DEFAULTS)
    journal = Journal("owner")
    st = stock_settings(settings)
    folder = Path(st["folder"])
    try:
        check_password(settings["report_password"])
        data, xlsx = build(settings, today)
        target = save_rotating(folder, stock_filename(data), encrypt_xlsx(xlsx, settings["report_password"]))
        journal.write("Утренний отчёт по складу записан",
                      f"{target.name}, позиций: {len(data.stale)}, под заказ: {len(data.ordered)}")
        return 0
    except Exception as e:  # noqa: BLE001
        msg = f"{datetime.now():%d.%m.%Y %H:%M} — отчёт «Залежавшиеся запчасти» не построен.\n\nПричина: {e}\n"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ERROR_FILE).write_text(msg, encoding="utf-8-sig")
        except OSError:
            pass
        journal.write("Утренний отчёт по складу: ошибка", type(e).__name__)
        return 1
