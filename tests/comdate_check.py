"""Даты из 1С через COM без сдвига часового пояса.

    python tests/comdate_check.py
"""
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from avtootchet.common.comdate import com_datetime, inline_dates, literal  # noqa: E402

# наивная дата (как отдаёт поддельная 1С в тестах) - без изменений
assert com_datetime(datetime(2026, 11, 2, 0, 0)) == datetime(2026, 11, 2, 0, 0)

# pywin32: полночь по Москве приходит как 21:00 UTC предыдущего дня
if hasattr(time, "tzset"):
    os.environ["TZ"] = "Europe/Moscow"
    time.tzset()
    utc = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)
    assert com_datetime(utc) == datetime(2026, 11, 2, 0, 0), com_datetime(utc)
else:  # Windows: пояс сборщика неизвестен - проверяем, что вернулось местное время
    utc = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)
    local = utc.astimezone().replace(tzinfo=None)
    assert com_datetime(utc) == local
assert com_datetime(datetime(2026, 11, 2, 9, 30, tzinfo=timezone(timedelta(hours=3)))).tzinfo is None

assert literal(datetime(2026, 11, 4, 23, 59, 59)) == "ДАТАВРЕМЯ(2026, 11, 4, 23, 59, 59)"
assert literal(date(2026, 11, 1)) == "ДАТАВРЕМЯ(2026, 11, 1)"
text, rest = inline_dates("ГДЕ Д МЕЖДУ &Начало И &Конец И К В (&Кассы)",
                          {"Начало": datetime(2026, 11, 1), "Конец": datetime(2026, 11, 4, 23, 59, 59),
                           "Кассы": ["А"]})
assert text == "ГДЕ Д МЕЖДУ ДАТАВРЕМЯ(2026, 11, 1, 0, 0, 0) И ДАТАВРЕМЯ(2026, 11, 4, 23, 59, 59) И К В (&Кассы)", text
assert rest == {"Кассы": ["А"]}
print("OK: даты 1С")
