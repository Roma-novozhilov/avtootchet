"""«Проверить структуру CRM и звонков»: что в Далионе есть для витрины по работе с клиентами.

Нужно для отчёта «за день/неделю: заявки поступили и обработаны, кто и за сколько, задачи
выполнены и просрочены, звонки приняты и пропущены, кому перезвонили» (модуль «Простые звонки»).
Только чтение и только НАЗВАНИЯ и КОЛИЧЕСТВА: ни телефонов, ни клиентов, ни текстов, ни имён.
Распределение значений показывается только для перечислений и флагов (Да/Нет).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .process_probe import ProcessProbe
from .sources import ComSource, SourceError
from .stock_probe import _items, _prop

CRM_RE = re.compile(r"звон|лид|контакт|задач|обращ|заявк|напомин|crm|^пз_|сообщ|мессендж|событ|взаимод|"
                    r"интерес|воронк|сделк|перезвон|канал", re.I)
VALUES_RE = re.compile(r"статус|направлен|состоян|результат|выполн|важн|этап|пропущ|принят|вид|тип|источник", re.I)
# справочники с людьми: их значения не выводим, только число разных
PERSONAL_RE = re.compile(r"пользовател|контрагент|контакт|физ|сотрудник|автомоб|лид|клиент|user", re.I)
DATE_NAMES = ("Период", "ДатаВремяНачала", "ДатаНачала", "Дата", "ДатаСоздания")
KINDS = [  # (коллекция EN, RU, префикс в запросе, по дате ли документа)
    ("Documents", "Документы", "Документ", True),
    ("Tasks", "Задачи", "Задача", True),
    ("BusinessProcesses", "БизнесПроцессы", "БизнесПроцесс", True),
    ("InformationRegisters", "РегистрыСведений", "РегистрСведений", False),
    ("Catalogs", "Справочники", "Справочник", False),
]
MAX_ATTRS = 60


class CrmProbe(ProcessProbe):
    def _fields(self, md) -> list:
        out = []
        for en, ru in (("Dimensions", "Измерения"), ("Resources", "Ресурсы"), ("Attributes", "Реквизиты")):
            try:
                out += _items(_prop(md, en, ru))
            except AttributeError:
                continue
        return out

    def _date_filter(self, md, by_doc_date: bool, prefix: str) -> tuple[str, str]:
        """(условие ГДЕ, по какому полю) - чтобы считать «за период»."""
        if by_doc_date:
            return "Т.Дата МЕЖДУ &Начало И &Конец", "Дата"
        names = {self._name(f) for f in self._fields(md)}
        for n in DATE_NAMES:
            if n in names:
                return f"Т.{n} МЕЖДУ &Начало И &Конец", n
        if prefix != "РегистрСведений":
            return "", ""
        try:   # периодический регистр сведений - стандартное поле Период
            self._rows(f"ВЫБРАТЬ ПЕРВЫЕ 1 Т.Период КАК П ИЗ РегистрСведений.{self._name(md)} КАК Т", "П")
            return "Т.Период МЕЖДУ &Начало И &Конец", "Период"
        except Exception:  # noqa: BLE001
            return "", ""

    def objects(self) -> list[str]:
        md = self._md()
        out = []
        self.found = []
        for en, ru, prefix, by_doc in KINDS:
            try:
                coll = _items(_prop(md, en, ru))
            except AttributeError:
                continue
            for m in coll:
                if not CRM_RE.search(self._title(m)):
                    continue
                full = f"{prefix}.{self._name(m)}"
                where, field = self._date_filter(m, by_doc, prefix)
                line = f"[{full}] {self._title(m)}"
                try:
                    total = self._rows(f"ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Кол ИЗ {full} КАК Т", "Кол")[0][0]
                    line += f" — всего: {int(total)}"
                    if where:
                        per = self._rows(f"ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Кол ИЗ {full} КАК Т ГДЕ {where}", "Кол")[0][0]
                        line += f", за период (по «{field}»): {int(per)}"
                except Exception as e:  # noqa: BLE001
                    line += f" — ОШИБКА подсчёта: {e}"
                    total = 0
                out.append(line)
                if int(total or 0):
                    self.found.append((full, m, where))
        return out

    def details(self) -> list[str]:
        out = []
        for full, m, where in getattr(self, "found", []):
            out.append("")
            out.append(f"[{full}]")
            fields = self._fields(m)
            out.append("поля: " + ", ".join(f"{self._name(f)}: {self._type(f)}" for f in fields[:MAX_ATTRS]))
            try:
                for ts in _items(_prop(m, "TabularSections", "ТабличныеЧасти")):
                    cols = ", ".join(self._name(c) for c in _items(_prop(ts, "Attributes", "Реквизиты")))
                    out.append(f"ТЧ {self._name(ts)}: {cols}")
            except AttributeError:
                pass
            cond = f"ГДЕ {where}" if where else ""
            for f in fields[:MAX_ATTRS]:
                name, typ = self._name(f), self._type(f)
                kinds = self._ref_kinds(f)
                is_enum = any(k.startswith(("Перечисление.", "Enum.")) for k in kinds)
                is_bool = "булево" in typ.lower()
                is_date = "дата" in typ.lower()
                try:
                    plain_ref = kinds and VALUES_RE.search(name) and not any(PERSONAL_RE.search(k) for k in kinds)
                    if is_enum or is_bool or plain_ref:
                        rows = self._rows(f"ВЫБРАТЬ Т.{name} КАК Знач, КОЛИЧЕСТВО(*) КАК Кол ИЗ {full} КАК Т {cond} "
                                          f"СГРУППИРОВАТЬ ПО Т.{name} УПОРЯДОЧИТЬ ПО Кол УБЫВ", "Знач", "Кол")
                        vals = "; ".join(f"{self._s(v)}: {int(c)}" for v, c in rows[:12])
                        out.append(f"  {name}: {vals or '(нет)'}")
                    elif is_date:
                        rows = self._rows(f"ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Кол ИЗ {full} КАК Т "
                                          f"{cond + ' И' if cond else 'ГДЕ'} Т.{name} > ДАТАВРЕМЯ(1, 1, 2)", "Кол")
                        out.append(f"  {name}: заполнено {int(rows[0][0]) if rows else 0}")
                    elif kinds and any(k.startswith(("Справочник.", "Документ.", "Catalog.", "Document.")) for k in kinds):
                        rows = self._rows(f"ВЫБРАТЬ КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Т.{name}) КАК Разных ИЗ {full} КАК Т {cond}",
                                          "Разных")
                        out.append(f"  {name} → {', '.join(kinds[:3])}: разных значений {int(rows[0][0]) if rows else 0}")
                except Exception as e:  # noqa: BLE001
                    out.append(f"  {name}: ОШИБКА {e}")
        return out

    def run(self) -> str:
        self.src._check_settings()
        self.conn = self.src._connect()
        self.lines = [f"Структура CRM и звонков, период {self.d1:%d.%m.%Y}–{self.d2:%d.%m.%Y}",
                      "Только названия и количества — без телефонов, клиентов, текстов и имён."]
        self._section("1. Объекты CRM, заявок, задач и звонков: сколько записей", self.objects)
        self._section("2. Поля объектов и как они заполнены (значения — только перечисления и Да/Нет)",
                      self.details)
        return "\n".join(self.lines)


def run_crm_probe(src: ComSource, days: int = 30) -> str:
    if not isinstance(src, ComSource):
        raise SourceError("Проверка работает только с 1С:Далион через COM")
    d2 = date.today()
    return CrmProbe(src, d2 - timedelta(days=days - 1), d2).run()
