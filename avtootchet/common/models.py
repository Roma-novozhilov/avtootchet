"""Общие структуры данных и разбор чисел/номеров."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

IN = "in"     # приход
OUT = "out"   # расход
KIND_NAMES = {IN: "ПКО", OUT: "РКО"}


def norm_number(raw) -> str:
    """'ПКО 00БП-000012' / '000000012' / 12 -> '12'. Пусто, если цифр нет."""
    if raw is None:
        return ""
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    groups = re.findall(r"\d+", str(raw))
    if not groups:
        return ""
    return groups[-1].lstrip("0") or "0"


def full_number(raw) -> str:
    """Номер целиком, с префиксом кассы: ' СЕ-000012 ' -> 'СЕ-000012'.
    Нужен, когда у разных касс одинаковые цифры номеров."""
    if raw is None:
        return ""
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    s = re.sub(r"\s+", "", str(raw)).upper()
    s = re.sub(r"^(ПКО|РКО|№)+", "", s)
    return s


def parse_amount(value) -> float | None:
    """Число из ячейки: 1234.5 / '1 234,50' / '1234,50 руб.' -> 1234.5."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def same_money(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.005


@dataclass
class CashOp:
    """Кассовая операция из 1С (ПКО/РКО по наличной кассе)."""
    dt: datetime
    kind: str
    number: str
    raw_number: str
    amount: float
    counterparty: str = ""
    article: str = ""
    cashbox: str = ""
    comment: str = ""
    doc_label: str = ""   # "ПКО", "РКО", "Перемещение ДС"... (пусто = ПКО/РКО по виду)

    @property
    def label(self) -> str:
        return self.doc_label or KIND_NAMES[self.kind]

    @property
    def is_cash_order(self) -> bool:
        """ПКО/РКО - только их номера есть в кассовой книге."""
        return self.label in ("ПКО", "РКО")

    @property
    def doc_name(self) -> str:
        return f"{self.label} №{self.number}"


@dataclass
class OneCData:
    period: tuple[date, date]
    ops: list[CashOp]
    opening: float          # остаток наличных на начало периода
    loaded_at: datetime
    source_name: str


@dataclass
class BookEntry:
    """Строка кассовой книги бухгалтера."""
    date: date
    kind: str
    number: str
    raw_number: str
    amount: float
    counterparty: str = ""
    account: str = ""
    sheet: str = ""
    row: int = 0

    @property
    def doc_name(self) -> str:
        return f"{KIND_NAMES[self.kind]} №{self.number or '?'}"


@dataclass
class BookDay:
    date: date
    opening: float | None = None
    closing: float | None = None
    total_in: float | None = None
    total_out: float | None = None


@dataclass
class CashBook:
    filename: str
    received_at: datetime
    entries: list[BookEntry] = field(default_factory=list)
    days: dict[date, BookDay] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def day(self, d: date) -> BookDay:
        return self.days.setdefault(d, BookDay(d))

    @property
    def date_range(self) -> tuple[date, date] | None:
        ds = [e.date for e in self.entries] + list(self.days)
        return (min(ds), max(ds)) if ds else None
