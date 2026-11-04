"""Откуда берутся данные 1С.

DemoSource - читает файл выгрузки учебной базы (export.json), чтобы всё работало
без настоящего Далиона. ComSource - подключение к 1С:Далион по COM (заготовка,
запросы допишем, когда будет видна структура базы).
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path

from ..common.models import IN, OUT, CashOp, OneCData, norm_number


class SourceError(Exception):
    pass


class DataSource(ABC):
    name = ""

    @abstractmethod
    def load(self, d1: date, d2: date) -> OneCData:
        """Операции наличной кассы за период + остаток на начало периода."""


# ------------------------------------------------------------------ демо
class DemoSource(DataSource):
    name = "Демо (файл выгрузки учебной базы)"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @staticmethod
    def _is_cash(cashbox: str) -> bool:
        """Все кассы, включая эквайринг; расчётные счета - нет."""
        low = cashbox.lower()
        return not any(w in low for w in ("банк", "расч"))

    def load(self, d1: date, d2: date) -> OneCData:
        if not self.path.exists():
            raise SourceError(f"Нет файла выгрузки: {self.path}")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            raise SourceError(f"Файл выгрузки повреждён: {e}") from None
        ops, opening = [], 0.0
        for r in raw.get("cash", []):
            if not self._is_cash(r.get("cashbox", "")):
                continue
            dt = datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S")
            income, expense = float(r.get("income") or 0), float(r.get("expense") or 0)
            if dt.date() < d1:
                opening += income - expense
                continue
            if dt.date() > d2:
                continue
            kind, amount = (IN, income) if income > 0 else (OUT, expense)
            ops.append(CashOp(dt=dt, kind=kind, number=norm_number(r.get("number")),
                              raw_number=str(r.get("number", "")), amount=amount,
                              counterparty=r.get("counterparty", ""), article=r.get("article", ""),
                              cashbox=r.get("cashbox", ""), comment=r.get("comment", "")))
        ops.sort(key=lambda o: (o.dt, o.kind, int(o.number or 0)))
        return OneCData((d1, d2), ops, opening, datetime.now(), self.name)


# ------------------------------------------------------------------ COM
# Структура «АвтоБизнес» (Далион) 1.3.6, проверено по выгрузке конфигурации:
#   РегистрНакопления.ДенежныеСредства: измерения СчетДенежныхСредств, Валюта;
#     ресурсы Сумма, СуммаУпр; реквизиты Контрагент, ВидДвиженияДенежныхСредств
#   Документ.ПриходДенег = ПКО, Документ.РасходДенег = РКО, КонвертацияДС = перемещение
#   Справочник.Кассы: Организация, ДляОплатПоКартам (эквайринг - не наличные)
# Берём движения регистра, а не сами документы: так в сверку попадает всё,
# что реально меняло остаток наличных, и остаток на начало считается тем же регистром.

DOC_LABELS = {"ПриходДенег": "ПКО", "РасходДенег": "РКО", "КонвертацияДС": "Перемещение ДС",
              "ЧекККМ": "Чек ККМ", "ОтчетОРозничныхПродажах": "Отчёт о розн. продажах",
              "АвансовыйОтчет": "Авансовый отчёт", "КорректировкаРегистров": "Корректировка"}

# Все кассы (наличные, без чека, эквайринг, СБП) - так ведётся кассовая книга в Далионе.
# Расчётные счета (Справочник.БанковскиеСчета) в кассовую книгу не входят.
CASH_FILTER = "{p}СчетДенежныхСредств ССЫЛКА Справочник.Кассы{extra}"

QUERY_OPS = """ВЫБРАТЬ
	ДС.Период КАК Период,
	ДС.Регистратор.Номер КАК Номер,
	ВЫБОР
		{doc_cases}
		ИНАЧЕ "Документ"
	КОНЕЦ КАК ТипДокумента,
	ВЫБОР КОГДА ДС.ВидДвижения = ЗНАЧЕНИЕ(ВидДвиженияНакопления.Приход) ТОГДА 1 ИНАЧЕ 0 КОНЕЦ КАК ЭтоПриход,
	ДС.Сумма КАК Сумма,
	ПРЕДСТАВЛЕНИЕ(ДС.Контрагент) КАК Контрагент,
	ПРЕДСТАВЛЕНИЕ(ДС.ВидДвиженияДенежныхСредств) КАК Статья,
	ПРЕДСТАВЛЕНИЕ(ДС.СчетДенежныхСредств) КАК Касса
ИЗ
	РегистрНакопления.ДенежныеСредства КАК ДС
ГДЕ
	ДС.Активность
	И ДС.Период МЕЖДУ &Начало И &Конец
	И {filter}
УПОРЯДОЧИТЬ ПО
	Период"""

QUERY_OPENING = """ВЫБРАТЬ
	ЕСТЬNULL(СУММА(Ост.СуммаОстаток), 0) КАК Остаток
ИЗ
	РегистрНакопления.ДенежныеСредства.Остатки(&Начало, {filter}) КАК Ост"""

QUERY_CASHBOXES = """ВЫБРАТЬ
	Кассы.Наименование КАК Касса,
	ЕСТЬNULL(Кассы.Организация.Наименование, "") КАК Организация
ИЗ
	Справочник.Кассы КАК Кассы
ГДЕ
	НЕ Кассы.ПометкаУдаления
	И НЕ Кассы.ЭтоГруппа
УПОРЯДОЧИТЬ ПО
	Кассы.Организация.Наименование,
	Кассы.Наименование"""


def registered_connector_path() -> str:
    """Путь к comcntr.dll, который сейчас зарегистрирован как V83.COMConnector (64-бит)."""
    try:
        import winreg  # noqa: PLC0415 - только Windows
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"V83.COMConnector\CLSID") as k:
            clsid = winreg.QueryValue(k, None)
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\InprocServer32") as k:
            return winreg.QueryValue(k, None)
    except OSError:
        return ""


def _com_datetime(v) -> datetime:
    """pywintypes.datetime -> обычный datetime."""
    return datetime(v.year, v.month, v.day, v.hour, v.minute, v.second)


class ComSource(DataSource):
    """Подключение к 1С:Далион через COM (V83.COMConnector). Только чтение.

    Что нужно для работы:
      * на ПК владельца установлена платформа 1С той же версии, что на сервере,
        и зарегистрирован comcntr.dll ТОЙ ЖЕ разрядности, что программа (64 бита);
      * в базе пользователь с правом «Внешнее соединение» и чтением кассовых данных.
    """
    name = "1С:Далион (COM)"

    def __init__(self, server: str, base: str, user: str, password: str,
                 cashboxes: str = "", organization: str = "", platform: str = ""):
        self.server, self.base, self.user, self.password = server, base, user, password
        self.platform = platform.strip()   # версия платформы, на которой работает база
        self.cashboxes = [c.strip() for c in cashboxes.split(",") if c.strip()]
        self.organization = organization.strip()

    @property
    def is_file_base(self) -> bool:
        """Указан путь к папке базы (сетевой или локальный) - значит файловый режим."""
        return bool(re.match(r"^(\\\\|[A-Za-z]:[\\/])", self.server.strip()))

    def connection_string(self) -> str:
        def q(v: str) -> str:
            return v.replace('"', '""')
        auth = f'Usr="{q(self.user)}";Pwd="{q(self.password)}";'
        if self.is_file_base:
            return f'File="{q(self.server.strip())}";' + auth
        return f'Srvr="{q(self.server)}";Ref="{q(self.base)}";' + auth

    def _connect(self):
        try:
            import pythoncom  # noqa: PLC0415 - есть только на Windows
            import win32com.client  # noqa: PLC0415
        except ImportError:
            raise SourceError("COM доступен только в Windows (нужен пакет pywin32)") from None
        if self.platform:
            path = registered_connector_path()
            if path and self.platform not in path:
                # Защита рабочей базы: другая версия платформы может предложить перевести
                # файловую базу в новый формат, и остальные компьютеры не смогут её открыть.
                raise SourceError(
                    f"Зарегистрирован COM-коннектор не той версии:\n{path}\n\nБаза работает на "
                    f"платформе {self.platform}. Подключение остановлено, чтобы не повредить базу. "
                    f"Установите 64-битный клиент 1С {self.platform} и выполните от администратора:\n"
                    f'regsvr32 "C:\\Program Files\\1cv8\\{self.platform}\\bin\\comcntr.dll"')
        pythoncom.CoInitialize()  # запрос идёт в фоновом потоке - COM надо включить в нём
        try:
            connector = win32com.client.Dispatch("V83.COMConnector")
        except Exception:  # noqa: BLE001
            raise SourceError("Не найден COM-коннектор 1С (V83.COMConnector). Установите клиент 1С "
                              "той же версии, что на сервере (64-битный), и зарегистрируйте "
                              "comcntr.dll: regsvr32 \"C:\\Program Files\\1cv8\\<версия>\\bin\\comcntr.dll\"") from None
        try:
            return connector.Connect(self.connection_string())
        except Exception as e:  # noqa: BLE001
            text = str(e)
            if "прав" in text.lower():
                text += " - у пользователя нет права «Внешнее соединение» или чтения кассы"
            raise SourceError(f"1С отказала в подключении: {text}") from None

    def _filter(self, p: str = "") -> str:
        """Условие «наличные кассы (+ выбранные кассы/организация)».
        p - префикс таблицы: "ДС." в основном запросе, "" в параметрах виртуальной таблицы."""
        extra = ""
        if self.cashboxes:
            extra += f" И ВЫРАЗИТЬ({p}СчетДенежныхСредств КАК Справочник.Кассы).Наименование В (&Кассы)"
        if self.organization:
            extra += (f" И ВЫРАЗИТЬ({p}СчетДенежныхСредств КАК Справочник.Кассы)"
                      ".Организация.Наименование = &Организация")
        return CASH_FILTER.format(p=p, extra=extra)

    def _query(self, conn, text: str, **params):
        q = conn.NewObject("Запрос")
        q.Text = text
        for k, v in params.items():
            q.SetParameter(k, v)
        if self.cashboxes:
            arr = conn.NewObject("Массив")
            for c in self.cashboxes:
                arr.Add(c)
            q.SetParameter("Кассы", arr)
        if self.organization:
            q.SetParameter("Организация", self.organization)
        try:
            return q.Execute().Choose()
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Ошибка запроса к 1С: {e}") from None

    def list_cashboxes(self) -> list[tuple[str, str]]:
        """Кассы базы (для настроек): [(касса, организация)]."""
        self._check_settings()
        conn = self._connect()
        sel = self._query(conn, QUERY_CASHBOXES)
        out = []
        while sel.Next():
            out.append((str(getattr(sel, "Касса")), str(getattr(sel, "Организация"))))
        return out

    def _check_settings(self):
        if not (self.server and self.user):
            raise SourceError("Заполните в настройках путь к базе (или сервер 1С) и пользователя")
        if not self.is_file_base and not self.base:
            raise SourceError("Для серверной базы укажите имя базы на сервере. "
                              "Для файловой - путь к папке базы, например \\\\SERVER\\1C\\Dalion")

    def load(self, d1: date, d2: date) -> OneCData:
        self._check_settings()
        conn = self._connect()
        start = datetime(d1.year, d1.month, d1.day)
        end = datetime(d2.year, d2.month, d2.day, 23, 59, 59)
        doc_cases = "\n\t\t".join(
            f'КОГДА ДС.Регистратор ССЫЛКА Документ.{name} ТОГДА "{label}"'
            for name, label in DOC_LABELS.items())
        sel = self._query(conn, QUERY_OPENING.format(filter=self._filter()), Начало=start)
        opening = float(getattr(sel, "Остаток")) if sel.Next() else 0.0

        ops: list[CashOp] = []
        sel = self._query(conn, QUERY_OPS.format(doc_cases=doc_cases, filter=self._filter("ДС.")),
                          Начало=start, Конец=end)
        while sel.Next():
            label = str(getattr(sel, "ТипДокумента"))
            raw = str(getattr(sel, "Номер") or "").strip()
            is_in = int(getattr(sel, "ЭтоПриход")) == 1
            ops.append(CashOp(
                dt=_com_datetime(getattr(sel, "Период")), kind=IN if is_in else OUT,
                number=norm_number(raw), raw_number=raw, amount=float(getattr(sel, "Сумма")),
                counterparty=str(getattr(sel, "Контрагент") or ""),
                article=str(getattr(sel, "Статья") or ""),
                cashbox=str(getattr(sel, "Касса") or ""), doc_label=label))
        return OneCData((d1, d2), ops, opening, datetime.now(), self.name)


def make_source(settings) -> DataSource:
    if settings["source"] == "com":
        c = settings["com"]
        return ComSource(c["server"], c["base"], c["user"], c["password"],
                         c.get("cashboxes", ""), c.get("organization", ""), c.get("platform", ""))
    return DemoSource(settings["demo_file"])
