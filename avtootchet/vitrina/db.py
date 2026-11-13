"""Файл данных витрины (SQLite). Пишется целиком при каждом сборе: сначала во временный файл,
потом подменяет старый - сайт никогда не видит наполовину записанные данные.

Таблицы:
  calls     - звонки «Простых звонков» (номер телефона - только последние 10 цифр, для
              перезвонов и повторных обращений; на сайте показывается маской);
  orders    - заказ-наряды: даты, статус, люди, источник рекламы, суммы работ/запчастей/материалов;
  payments  - оплаты клиентов в кассы по ЗН и расходным накладным (как «Выручка» владельца);
  works     - работы из ЗН по дням: сколько раз и на какую сумму;
  tasks     - задачи и напоминания;
  meta      - когда собрано, за какой период, ошибки разделов.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA = {
    "calls": ["id", "start", "end", "dir", "dur", "wait", "missed", "callback", "callback_min", "user",
              "client_code", "client", "line", "phone"],
    "orders": ["number", "date", "created", "author", "type", "status", "source", "client_code", "client", "car",
               "master", "in_work", "done", "issued", "repeat", "posted", "amount", "works", "parts", "materials"],
    "payments": ["date", "number", "amount", "cashbox", "method", "direction", "client_code", "client",
                 "basis_kind", "basis_number"],
    "works": ["day", "work", "orders", "amount"],
    "tasks": ["created", "type", "is_task", "executor", "done", "done_at", "hours_to_done"],
}
NUMERIC = {"dur", "wait", "callback_min", "amount", "works", "parts", "materials", "orders", "hours_to_done"}


def write_db(path: Path, tables: dict[str, list[dict]], meta: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        for name, cols in SCHEMA.items():
            defs = ", ".join(f'"{c}" {"REAL" if c in NUMERIC else "TEXT"}' for c in cols)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            rows = tables.get(name) or []
            con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})',
                            [tuple(r.get(c) for c in cols) for r in rows])
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)


def read_db(path: Path) -> tuple[dict[str, list[dict]], dict]:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {}
        for name in SCHEMA:
            try:
                tables[name] = [dict(r) for r in con.execute(f'SELECT * FROM "{name}"')]
            except sqlite3.OperationalError:
                tables[name] = []
        meta = {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM meta")}
    finally:
        con.close()
    return tables, meta
