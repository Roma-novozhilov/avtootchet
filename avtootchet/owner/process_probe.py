"""«Проверить данные для анализа процессов»: что в Далионе есть для схемы бизнес-процессов.

Нужен, чтобы построить журнал событий «заказ-наряд - действие - время - кто» и по нему
схему реального процесса (process mining), не расспрашивая сотрудников.
Только чтение и только НАЗВАНИЯ и КОЛИЧЕСТВА: ни сумм, ни клиентов, ни номеров документов,
ни имён пользователей в отчёт не попадает. Каждая проверка независима.

Проверяет:
  1. журнал регистрации 1С (папка 1Cv8Log): формат, за какой период хранится, размер;
  2. какие уровни событий пишутся в журнал;
  3. сколько документов каждого вида за период - какие шаги процесса вообще есть в базе;
  4. заказ-наряд: реквизиты-даты, статусы (перечисления) и как они заполнены;
  5. связи документов: какой документ на какой ссылается (шапка и табличные части);
  6. регистры сведений про статусы, историю, этапы.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .probe import Probe
from .sources import ComSource, SourceError
from .stock_probe import _call, _find, _items, _prop

STATUS_REG_RE = re.compile(r"статус|состоян|истор|этап|сроки|журнал|изменен|смен", re.I)
ZN = "ЗаказНаряд"
MAX_LINES = 40


def _ticks_to_dt(v) -> datetime | None:
    """Дата в журнале нового формата (.lgd): 1/10000 секунды от 01.01.0001."""
    try:
        return datetime(1, 1, 1) + timedelta(seconds=int(v) / 10000)
    except (TypeError, ValueError, OverflowError):
        return None


def _lgd_stats(lgd: Path) -> tuple:
    """MIN/MAX даты и число записей. Только чтение; если 1С держит блокировку - читаем без неё
    (immutable): для подсчёта это безопасно, файл не меняется."""
    uri = lgd.resolve().as_uri()
    last = None
    for opts in ("?mode=ro", "?mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(uri + opts, uri=True, timeout=5)
            try:
                return con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM EventLog").fetchone()
            finally:
                con.close()
        except sqlite3.Error as e:
            last = e
    raise last


def log_files(base_dir: Path) -> list[str]:
    """Папка журнала регистрации файловой базы: формат, период, размер. Только чтение."""
    folder = base_dir / "1Cv8Log"
    if not folder.is_dir():
        return [f"папка журнала не найдена: {folder}"]
    files = [p for p in folder.iterdir() if p.is_file()]
    size = sum(p.stat().st_size for p in files) / 1024 / 1024
    out = [f"папка: {folder}", f"файлов: {len(files)}; размер: " + f"{size:,.1f} МБ".replace(",", " ")]
    lgd = folder / "1Cv8.lgd"
    lgp = sorted(p for p in files if p.suffix.lower() == ".lgp")
    if lgd.exists():
        out.append("формат: новый (1Cv8.lgd, SQLite)")
        try:
            lo, hi, n = _lgd_stats(lgd)
            a, b = _ticks_to_dt(lo), _ticks_to_dt(hi)
            out.append(f"записей: {n:,}".replace(",", " "))
            if a and b:
                out.append(f"период: {a:%d.%m.%Y} – {b:%d.%m.%Y} ({(b - a).days} дн.)")
        except Exception as e:  # noqa: BLE001
            out.append(f"прочитать 1Cv8.lgd не удалось: {e}")
    if lgp:
        out.append(f"формат: старый (*.lgp), файлов-периодов: {len(lgp)}")
        names = [p.stem for p in lgp if re.fullmatch(r"\d{14}", p.stem)]
        if names:
            a = datetime.strptime(names[0], "%Y%m%d%H%M%S")
            b = datetime.fromtimestamp(lgp[-1].stat().st_mtime)
            out.append(f"период: {a:%d.%m.%Y} – {b:%d.%m.%Y} ({(b - a).days} дн.)")
    if not lgd.exists() and not lgp:
        out.append("файлов журнала (1Cv8.lgd или *.lgp) нет")
    return out


class ProcessProbe(Probe):
    def _md(self):
        return _prop(self.conn, "Metadata", "Метаданные")

    def _name(self, md) -> str:
        return str(_prop(md, "Name", "Имя"))

    def _title(self, md) -> str:
        name = self._name(md)
        try:
            syn = str(_prop(md, "Synonym", "Синоним"))
        except AttributeError:
            syn = ""
        return f"{name} ({syn})" if syn and syn != name else name

    def _ref_kinds(self, attr) -> list[str]:
        """Полные имена объектов, на которые может ссылаться реквизит: «Документ.ЗаказНаряд», …"""
        out = []
        try:
            types = _items(_call(_prop(attr, "Type", "Тип"), "Types", "Типы"))
        except AttributeError:
            return out
        md = self._md()
        for t in types:
            try:
                m = _call(md, "FindByType", "НайтиПоТипу", args=(t,))
                if m is not None:
                    out.append(str(_call(m, "FullName", "ПолноеИмя")))
            except Exception:  # noqa: BLE001
                continue
        return out

    def _type(self, attr) -> str:
        try:
            return self._s(_prop(attr, "Type", "Тип"))
        except AttributeError:
            return "?"

    # ---------------------------------------------------------------- 1-2. журнал регистрации
    def log_folder(self) -> list[str]:
        if not self.src.is_file_base:
            return ["база серверная - журнал регистрации лежит на сервере 1С, папку отсюда не проверить"]
        return log_files(Path(self.src.server.strip()))

    def log_sample(self) -> list[str]:
        out = []
        try:
            usage = _call(self.conn, "ПолучитьИспользованиеЖурналаРегистрации", "GetEventLogUsage")
            levels = [self._s(x) for x in _items(usage)]
            out.append(f"уровни, которые пишутся: {', '.join(levels) or '(никакие)'}")
        except Exception as e:  # noqa: BLE001
            out.append(f"уровень журнала не прочитан: {e}")
        # Выгрузку журнала через 1С (ВыгрузитьЖурналРегистрации) НЕ делаем: на журнале в 13 ГБ она
        # съела 18 ГБ памяти сервера. Содержимое журнала будем читать из файлов, по частям.
        return out

    # ---------------------------------------------------------------- 3. документы за период
    def documents(self) -> list[str]:
        self.docs = {}
        out = []
        for d in _items(_prop(self._md(), "Documents", "Документы")):
            name = self._name(d)
            try:
                rows = self._rows(Q_DOC_COUNT.format(doc=name), "Всего", "Проведено", "Авторов")
            except Exception as e:  # noqa: BLE001
                out.append(f"[{self._title(d)}] ОШИБКА: {e}")
                continue
            total, posted, _ = rows[0] if rows else (0, 0, 0)
            if not int(total or 0):
                continue
            self.docs[name] = d
            line = f"[{self._title(d)}] всего: {int(total)}, проведено: {int(posted or 0)}"
            names = {self._name(a) for a in _items(_prop(d, "Attributes", "Реквизиты"))}
            for who in ("Автор", "Ответственный", "Менеджер"):
                if who in names:
                    try:
                        r = self._rows(Q_WHO.format(doc=name, attr=who), "Разных")
                        line += f", разных «{who}»: {int(r[0][0]) if r else 0}"
                    except Exception as e:  # noqa: BLE001
                        line += f", «{who}»: ОШИБКА {e}"
            out.append(line)
        return out

    # ---------------------------------------------------------------- 4. заказ-наряд
    def order(self) -> list[str]:
        zn = _find(_prop(self._md(), "Documents", "Документы"), ZN)
        out = []
        for a in _items(_prop(zn, "Attributes", "Реквизиты")):
            an, typ = self._name(a), self._type(a)
            line = f"{an}: {typ}"
            kinds = self._ref_kinds(a)
            try:
                if "дата" in typ.lower():
                    rows = self._rows(Q_FILLED_DATE.format(attr=an), "Кол")
                    line += f" - заполнено: {int(rows[0][0]) if rows else 0}"
                elif any(k.startswith(("Перечисление.", "Enum.")) for k in kinds) or \
                        re.search(r"статус|состоян|этап|вид|тип", an, re.I):
                    rows = self._rows(Q_VALUES.format(attr=an), "Знач", "Кол")
                    vals = "; ".join(f"{self._s(v)}: {int(c)}" for v, c in rows[:15])
                    line += f" - значения: {vals or '(нет)'}"
            except Exception as e:  # noqa: BLE001
                line += f" - ОШИБКА: {e}"
            out.append(line)
        for ts in _items(_prop(zn, "TabularSections", "ТабличныеЧасти")):
            cols = ", ".join(self._name(c) for c in _items(_prop(ts, "Attributes", "Реквизиты")))
            out.append(f"ТЧ {self._name(ts)}: {cols}")
        return out

    # ---------------------------------------------------------------- 5. связи документов
    def links(self) -> list[str]:
        out = []
        for name, d in getattr(self, "docs", {}).items():
            parts = [("", a) for a in _items(_prop(d, "Attributes", "Реквизиты"))]
            for ts in _items(_prop(d, "TabularSections", "ТабличныеЧасти")):
                parts += [(self._name(ts), a) for a in _items(_prop(ts, "Attributes", "Реквизиты"))]
            for ts_name, a in parts:
                if not any(k.startswith(("Документ.", "Document.")) for k in self._ref_kinds(a)):
                    continue
                an = self._name(a)
                where = f"{ts_name}.{an}" if ts_name else an
                text = (Q_LINK_TS.format(doc=name, ts=ts_name, attr=an) if ts_name
                        else Q_LINK.format(doc=name, attr=an))
                try:
                    rows = self._rows(text, "Тип", "Док")
                except Exception as e:  # noqa: BLE001
                    out.append(f"{name}.{where}: ОШИБКА: {e}")
                    continue
                filled = [(t, n) for t, n in rows if t is not None and int(n)]
                if filled:
                    out.append(f"{name}.{where} → " + "; ".join(f"{self._s(t)}: {int(n)}" for t, n in filled))
        return out

    # ---------------------------------------------------------------- 6. регистры статусов
    def status_registers(self) -> list[str]:
        out = []
        for r in _items(_prop(self._md(), "InformationRegisters", "РегистрыСведений")):
            if not STATUS_REG_RE.search(self._title(r)):
                continue
            parts = []
            for kind, en, ru in (("измерения", "Dimensions", "Измерения"), ("ресурсы", "Resources", "Ресурсы"),
                                 ("реквизиты", "Attributes", "Реквизиты")):
                try:
                    parts.append(f"{kind}: " + ", ".join(self._name(x) for x in _items(_prop(r, en, ru))))
                except AttributeError:
                    continue
            try:
                rows = self._rows(Q_REG_COUNT.format(reg=self._name(r)), "Кол")
                parts.append(f"записей: {int(rows[0][0]) if rows else 0}")
            except Exception as e:  # noqa: BLE001
                parts.append(f"записей: ОШИБКА {e}")
            out.append(f"[{self._title(r)}] " + "; ".join(parts))
        return out

    def run(self) -> str:
        self.src._check_settings()
        self.conn = self.src._connect()
        self.lines = [f"Данные для анализа процессов, документы за {self.d1:%d.%m.%Y}–{self.d2:%d.%m.%Y}",
                      "Только названия и количества — без сумм, клиентов, номеров и имён пользователей."]
        self._section("1. Журнал регистрации 1С: формат, период хранения, размер", self.log_folder)
        self._section("2. Журнал регистрации: какие уровни событий пишутся", self.log_sample)
        self._section("3. Документы за период: всего / проведено / сколько разных авторов и ответственных", self.documents)
        self._section("4. Заказ-наряд: реквизиты, даты и статусы", self.order)
        self._section("5. Связи документов: реквизит → на какие документы ссылается (сколько документов)",
                      self.links)
        self._section("6. Регистры сведений про статусы, историю, этапы", self.status_registers)
        return "\n".join(self.lines)


Q_DOC_COUNT = """ВЫБРАТЬ
	КОЛИЧЕСТВО(*) КАК Всего,
	СУММА(ВЫБОР КОГДА Д.Проведен ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК Проведено,
	NULL КАК Авторов
ИЗ
	Документ.{doc} КАК Д
ГДЕ
	НЕ Д.ПометкаУдаления
	И Д.Дата МЕЖДУ &Начало И &Конец"""

Q_WHO = """ВЫБРАТЬ
	КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Д.{attr}) КАК Разных
ИЗ
	Документ.{doc} КАК Д
ГДЕ
	НЕ Д.ПометкаУдаления
	И Д.Дата МЕЖДУ &Начало И &Конец"""

Q_FILLED_DATE = """ВЫБРАТЬ
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	Документ.ЗаказНаряд КАК Д
ГДЕ
	НЕ Д.ПометкаУдаления
	И Д.Дата МЕЖДУ &Начало И &Конец
	И Д.{attr} > ДАТАВРЕМЯ(1, 1, 2)"""

Q_VALUES = """ВЫБРАТЬ
	Д.{attr} КАК Знач,
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	Документ.ЗаказНаряд КАК Д
ГДЕ
	НЕ Д.ПометкаУдаления
	И Д.Дата МЕЖДУ &Начало И &Конец
СГРУППИРОВАТЬ ПО
	Д.{attr}
УПОРЯДОЧИТЬ ПО
	Кол УБЫВ"""

Q_LINK = """ВЫБРАТЬ
	ТИПЗНАЧЕНИЯ(Д.{attr}) КАК Тип,
	КОЛИЧЕСТВО(*) КАК Док
ИЗ
	Документ.{doc} КАК Д
ГДЕ
	НЕ Д.ПометкаУдаления
	И Д.Дата МЕЖДУ &Начало И &Конец
СГРУППИРОВАТЬ ПО
	ТИПЗНАЧЕНИЯ(Д.{attr})"""

Q_LINK_TS = """ВЫБРАТЬ
	ТИПЗНАЧЕНИЯ(Т.{attr}) КАК Тип,
	КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Т.Ссылка) КАК Док
ИЗ
	Документ.{doc}.{ts} КАК Т
ГДЕ
	НЕ Т.Ссылка.ПометкаУдаления
	И Т.Ссылка.Дата МЕЖДУ &Начало И &Конец
СГРУППИРОВАТЬ ПО
	ТИПЗНАЧЕНИЯ(Т.{attr})"""

Q_REG_COUNT = """ВЫБРАТЬ
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	РегистрСведений.{reg} КАК Р"""


def run_process_probe(src: ComSource, days: int = 183) -> str:
    if not isinstance(src, ComSource):
        raise SourceError("Проверка работает только с 1С:Далион через COM")
    d2 = date.today()
    return ProcessProbe(src, d2 - timedelta(days=days - 1), d2).run()
