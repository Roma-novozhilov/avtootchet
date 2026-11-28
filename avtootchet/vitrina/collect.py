"""Сбор данных для витрины из Далиона (только чтение).

* Полный сбор - раз в день, обычно в 6:00: всё за N месяцев + история визитов клиентов с 2012 г.
  (короткий запрос: клиент + день визита) + карточки контрагентов и их телефоны.
* Дневное обновление (--collect-today, в 10, 13, 16, 19): только свежие звонки, задачи, оплаты
  (3 дня), заказ-наряды (30 дней) и новые карточки - они подменяют эти же строки в файле.

Каждый раздел читается отдельным запросом: если 1С отвергнет один, остальные соберутся,
а на сайте у раздела будет текст ошибки. Звонки, перезвоны и задачи считаются тем же кодом,
что выгрузка программы маркетолога.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from ..marketing.export import (CALL_COLS, CALLBACK_HOURS, Q_CALLS, Q_TASKS, TASK_COLS, TASK_TYPES, Directions,
                                build_calls, build_tasks, _dt, _int, _s)
from ..owner.revenue import classify_cashbox
from ..common.comdate import inline_dates
from .callfields import collect_call_fields
from ..owner.stock_probe import _call, _items, _prop
from .db import read_db, write_db

Q_ORDERS = """ВЫБРАТЬ
	З.Номер КАК Номер,
	З.Дата КАК Дата,
	З.ДатаСоздания КАК Создан,
	ПРЕДСТАВЛЕНИЕ(З.Автор) КАК Автор,
	ПРЕДСТАВЛЕНИЕ(З.ТипЗН) КАК Тип,
	ПРЕДСТАВЛЕНИЕ(З.СтатусЗН) КАК Статус,
	ПРЕДСТАВЛЕНИЕ(З.ИсточникРекламы) КАК Источник,
	З.Контрагент.Код КАК КлиентКод,
	ПРЕДСТАВЛЕНИЕ(З.Контрагент) КАК Клиент,
	ПРЕДСТАВЛЕНИЕ(З.Автомобиль) КАК Автомобиль,
	ПРЕДСТАВЛЕНИЕ(З.МастерПриемщик) КАК Мастер,
	З.ДатаПереведенВРаботу КАК ВРаботе,
	З.ДатаЗавершен КАК Завершен,
	З.ДатаОтгружен КАК Выдан,
	З.Повтор КАК Повтор,
	З.Проведен КАК Проведен,
	З.СуммаДокумента КАК Сумма,
	ЕСТЬNULL(У.С, 0) КАК Работы,
	ЕСТЬNULL(Т.С, 0) КАК Запчасти,
	ЕСТЬNULL(М.С, 0) КАК Материалы
ИЗ
	Документ.ЗаказНаряд КАК З
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.ЗаказНаряд.Услуги КАК Х
			ГДЕ Х.Ссылка.Дата МЕЖДУ &Начало И &Конец
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК У
		ПО (У.Ссылка = З.Ссылка)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.ЗаказНаряд.Товары КАК Х
			ГДЕ Х.Ссылка.Дата МЕЖДУ &Начало И &Конец
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК Т
		ПО (Т.Ссылка = З.Ссылка)
		ЛЕВОЕ СОЕДИНЕНИЕ (ВЫБРАТЬ Х.Ссылка КАК Ссылка, СУММА(Х.Всего) КАК С
			ИЗ Документ.ЗаказНаряд.Материалы КАК Х
			ГДЕ Х.Ссылка.Дата МЕЖДУ &Начало И &Конец
			СГРУППИРОВАТЬ ПО Х.Ссылка) КАК М
		ПО (М.Ссылка = З.Ссылка)
ГДЕ
	НЕ З.ПометкаУдаления
	И З.Дата МЕЖДУ &Начало И &Конец
УПОРЯДОЧИТЬ ПО
	З.Дата"""
ORDER_COLS = ("Номер", "Дата", "Создан", "Автор", "Тип", "Статус", "Источник", "КлиентКод", "Клиент",
              "Автомобиль", "Мастер", "ВРаботе", "Завершен", "Выдан", "Повтор", "Проведен", "Сумма", "Работы",
              "Запчасти", "Материалы")

# Записи и отказы: все ЗН периода, включая помеченные на удаление (удалённая предварительная
# запись = отменённая запись). Отдельным запросом - если какого-то поля нет, основной сбор цел.
Q_ORDER_EXTRA = """ВЫБРАТЬ
	З.Номер КАК Номер,
	З.Дата КАК Дата,
	З.ПометкаУдаления КАК Удален,
	З.ПланируемаяДатаНачалаРабот КАК План,
	ПРЕДСТАВЛЕНИЕ(З.ПричинаОтказа) КАК Отказ,
	ПРЕДСТАВЛЕНИЕ(З.Менеджер) КАК Менеджер,
	З.ДатаСоздания КАК Создан,
	ПРЕДСТАВЛЕНИЕ(З.Автор) КАК Автор,
	ПРЕДСТАВЛЕНИЕ(З.ТипЗН) КАК Тип,
	ПРЕДСТАВЛЕНИЕ(З.СтатусЗН) КАК Статус,
	ПРЕДСТАВЛЕНИЕ(З.ИсточникРекламы) КАК Источник,
	З.Контрагент.Код КАК КлиентКод,
	ПРЕДСТАВЛЕНИЕ(З.Контрагент) КАК Клиент,
	ПРЕДСТАВЛЕНИЕ(З.Автомобиль) КАК Автомобиль,
	ПРЕДСТАВЛЕНИЕ(З.МастерПриемщик) КАК Мастер
ИЗ
	Документ.ЗаказНаряд КАК З
ГДЕ
	З.Дата МЕЖДУ &Начало И &Конец"""
EXTRA_COLS = ("Номер", "Дата", "Удален", "План", "Отказ", "Менеджер", "Создан", "Автор", "Тип", "Статус",
              "Источник", "КлиентКод", "Клиент", "Автомобиль", "Мастер")

# Поля шапки ЗН для отдельных строк «Финансов» (гарантия, наши машины): «Вид», «Плательщик», «Договор».
# Их имена в метаданных Далиона заранее не известны - ищутся по синониму среди реквизитов документа.
HEAD_FIELDS = (("kind", "Вид", ("вид",), r"Вид(ЗН|ЗаказНаряда|ЗаказаНаряда|Ремонта|Работ)"),
               ("payer", "Плательщик", ("плательщик",), r"Плательщик"),
               ("contract", "Договор", ("договор", "договор контрагента", "договор взаиморасчетов"),
                r"Договор(Контрагента|Взаиморасчетов)?"))


def q_order_head(attrs: dict) -> tuple[str, tuple]:
    """Запрос: номер ЗН + представления найденных полей шапки."""
    cols = ["Номер"] + [alias for key, alias, *_ in HEAD_FIELDS if attrs.get(key)]
    fields = ",\n\t".join(["З.Номер КАК Номер"] + [f"ПРЕДСТАВЛЕНИЕ(З.{attrs[key]}) КАК {alias}"
                                                     for key, alias, *_ in HEAD_FIELDS if attrs.get(key)])
    return f"""ВЫБРАТЬ
\t{fields}
ИЗ
\tДокумент.ЗаказНаряд КАК З
ГДЕ
\tНЕ З.ПометкаУдаления
\tИ З.Дата МЕЖДУ &Начало И &Конец""", tuple(cols)

# Остатки по кассам и расчётным счетам на момент сбора (регистр ДенежныеСредства). Только для владельца.
Q_BALANCES = """ВЫБРАТЬ
	ПРЕДСТАВЛЕНИЕ(О.СчетДенежныхСредств) КАК Счет,
	ВЫБОР КОГДА О.СчетДенежныхСредств ССЫЛКА Справочник.Кассы ТОГДА "Касса" ИНАЧЕ "Расчётный счёт" КОНЕЦ КАК Вид,
	ЕСТЬNULL(ВЫРАЗИТЬ(О.СчетДенежныхСредств КАК Справочник.Кассы).Организация.Наименование, "") КАК Организация,
	ЕСТЬNULL(ВЫРАЗИТЬ(О.СчетДенежныхСредств КАК Справочник.Кассы).ДляОплатПоКартам, ЛОЖЬ) КАК Карты,
	ПРЕДСТАВЛЕНИЕ(О.Валюта) КАК Валюта,
	О.СуммаОстаток КАК Остаток
ИЗ
	РегистрНакопления.ДенежныеСредства.Остатки(&Конец) КАК О"""
BAL_COLS = ("Счет", "Вид", "Организация", "Карты", "Валюта", "Остаток")


def order_head_attrs(conn) -> dict:
    """Имена реквизитов «Вид», «Плательщик», «Договор» у документа ЗаказНаряд: {"kind": имя, ...}.
    Чего нет - того нет в словаре."""
    try:
        md = _prop(conn, "Metadata", "Метаданные")
        doc = _call(_prop(md, "Documents", "Документы"), "Find", "Найти", args=("ЗаказНаряд",))
    except AttributeError:
        return {}
    if doc is None:
        return {}
    by_syn, by_name = {}, {}
    for a in _items(_prop(doc, "Attributes", "Реквизиты")):
        name = str(_prop(a, "Name", "Имя"))
        try:
            syn = str(_prop(a, "Synonym", "Синоним")).strip().lower()
        except AttributeError:
            syn = ""
        for key, _alias, syns, pattern in HEAD_FIELDS:
            if syn in syns and key not in by_syn:
                by_syn[key] = name
            if re.fullmatch(pattern, name, re.I) and key not in by_name:
                by_name[key] = name
    return {**by_name, **by_syn}


def order_kind_attr(conn) -> str:
    return order_head_attrs(conn).get("kind", "")


def balances_table(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        amount = _num(r["Остаток"])
        if abs(amount) < 0.005:
            continue
        kind = _s(r["Вид"])
        if kind == "Касса" and r["Карты"]:
            kind = "Касса (карты)"
        out.append({"account": _s(r["Счет"]), "kind": kind, "org": _s(r["Организация"]),
                    "currency": _s(r["Валюта"]), "amount": round(amount, 2)})
    return sorted(out, key=lambda b: (b["kind"] != "Касса", b["kind"], b["org"], b["account"]))


# История визитов за всё время: клиент + день + тип ЗН. Визит - завершённый или выданный ЗН
# («Закрыт на затраты», предварительные и удалённые не считаются; внутренние убираются в Python).
# Повтор в тот же день сливается в один визит сам - группировка по дню.
Q_VISITS = """ВЫБРАТЬ
	З.Контрагент.Код КАК КлиентКод,
	НАЧАЛОПЕРИОДА(З.Дата, ДЕНЬ) КАК День,
	ПРЕДСТАВЛЕНИЕ(З.ТипЗН) КАК Тип
ИЗ
	Документ.ЗаказНаряд КАК З
ГДЕ
	НЕ З.ПометкаУдаления
	И З.СтатусЗН В (ЗНАЧЕНИЕ(Перечисление.СтатусыЗаказНаряда.Завершен), ЗНАЧЕНИЕ(Перечисление.СтатусыЗаказНаряда.Отгружен))
СГРУППИРОВАТЬ ПО
	З.Контрагент.Код,
	НАЧАЛОПЕРИОДА(З.Дата, ДЕНЬ),
	З.ТипЗН"""
VISIT_COLS = ("КлиентКод", "День", "Тип")

# Карточки контрагентов: когда и кем заведены. Телефоны - отдельно (только цифры номера).
Q_CLIENTS = """ВЫБРАТЬ
	К.Код КАК Код,
	К.Наименование КАК Наименование,
	К.ДатаСоздания КАК Создан,
	ПРЕДСТАВЛЕНИЕ(К.Автор) КАК Автор,
	ПРЕДСТАВЛЕНИЕ(К.ВидКонтрагента) КАК Вид,
	ПРЕДСТАВЛЕНИЕ(К.ИсточникРекламы) КАК Источник,
	К.ПометкаУдаления КАК Удален
ИЗ
	Справочник.Контрагенты КАК К"""
# у старых карточек дата создания может быть пустой - поэтому отбор только при дневном обновлении
Q_CLIENTS_NEW = Q_CLIENTS + """
ГДЕ
	К.ДатаСоздания >= &НачалоКарточек"""
CLIENT_COLS = ("Код", "Наименование", "Создан", "Автор", "Вид", "Источник", "Удален")
Q_PHONES = """ВЫБРАТЬ
	Т.Ссылка.Код КАК Код,
	Т.Телефон КАК Телефон
ИЗ
	Справочник.Контрагенты.ТелефоныДляБыстрогоПоиска КАК Т"""
PHONE_COLS = ("Код", "Телефон")

# Оплаты клиентов в кассы по ЗН и расходным накладным минус возвраты - так же, как «Выручка»
# в программе владельца (расчётные счета и поступления без основания туда не входят).
Q_PAYMENTS = """ВЫБРАТЬ
	1 КАК Знак,
	П.Дата КАК Дата,
	П.Номер КАК Номер,
	П.СуммаДокумента КАК Сумма,
	П.СчетДенежныхСредств КАК Касса,
	П.Подразделение КАК Подразделение,
	П.Контрагент КАК Контрагент,
	П.ДокументОснование КАК Основание
ПОМЕСТИТЬ ВТ_П
ИЗ
	Документ.ПриходДенег КАК П
ГДЕ
	П.Проведен
	И П.Дата МЕЖДУ &Начало И &Конец
	И П.СчетДенежныхСредств ССЫЛКА Справочник.Кассы
	И (ТИПЗНАЧЕНИЯ(П.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
		ИЛИ ТИПЗНАЧЕНИЯ(П.ДокументОснование) = ТИП(Документ.РасходнаяНакладная))

ОБЪЕДИНИТЬ ВСЕ

ВЫБРАТЬ
	-1,
	Р.Дата,
	Р.Номер,
	Р.СуммаДокумента,
	Р.СчетДенежныхСредств,
	Р.Подразделение,
	Р.Контрагент,
	Р.ДокументОснование
ИЗ
	Документ.РасходДенег КАК Р
ГДЕ
	Р.Проведен
	И Р.Дата МЕЖДУ &Начало И &Конец
	И Р.СчетДенежныхСредств ССЫЛКА Справочник.Кассы
	И (ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗаказНаряд)
		ИЛИ ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.РасходнаяНакладная))
;
ВЫБРАТЬ
	П.Знак КАК Знак,
	П.Дата КАК Дата,
	П.Номер КАК Номер,
	П.Сумма КАК Сумма,
	ПРЕДСТАВЛЕНИЕ(П.Касса) КАК Касса,
	ПРЕДСТАВЛЕНИЕ(П.Подразделение) КАК Подразделение,
	ЕСТЬNULL(П.Контрагент.Код, "") КАК КлиентКод,
	ПРЕДСТАВЛЕНИЕ(П.Контрагент) КАК Клиент,
	ВЫБОР
		КОГДА ТИПЗНАЧЕНИЯ(П.Основание) = ТИП(Документ.ЗаказНаряд) ТОГДА "ЗН"
		ИНАЧЕ "РН"
	КОНЕЦ КАК ВидОснования,
	ЕСТЬNULL(ЗН.Номер, ЕСТЬNULL(РН.Номер, "")) КАК НомерОснования
ИЗ
	ВТ_П КАК П
		ЛЕВОЕ СОЕДИНЕНИЕ Документ.ЗаказНаряд КАК ЗН
		ПО (ЗН.Ссылка = П.Основание)
		ЛЕВОЕ СОЕДИНЕНИЕ Документ.РасходнаяНакладная КАК РН
		ПО (РН.Ссылка = П.Основание)"""
PAY_COLS = ("Знак", "Дата", "Номер", "Сумма", "Касса", "Подразделение", "КлиентКод", "Клиент", "ВидОснования",
            "НомерОснования")

# Оплаты по ЗН всеми способами (кассы и расчётный счёт) минус возвраты - только для «выдан, но не
# оплачен»: безналичная оплата юрлиц в выручку касс не входит, но долгом не является.
Q_ORDER_PAID = """ВЫБРАТЬ
	1 КАК Знак,
	ВЫРАЗИТЬ(П.ДокументОснование КАК Документ.ЗаказНаряд).Номер КАК Номер,
	П.СуммаДокумента КАК Сумма
ИЗ
	Документ.ПриходДенег КАК П
ГДЕ
	П.Проведен
	И П.Дата МЕЖДУ &Начало И &КонецСЗапасом
	И ТИПЗНАЧЕНИЯ(П.ДокументОснование) = ТИП(Документ.ЗаказНаряд)

ОБЪЕДИНИТЬ ВСЕ

ВЫБРАТЬ
	-1,
	ВЫРАЗИТЬ(Р.ДокументОснование КАК Документ.ЗаказНаряд).Номер,
	Р.СуммаДокумента
ИЗ
	Документ.РасходДенег КАК Р
ГДЕ
	Р.Проведен
	И Р.Дата МЕЖДУ &Начало И &КонецСЗапасом
	И ТИПЗНАЧЕНИЯ(Р.ДокументОснование) = ТИП(Документ.ЗаказНаряд)"""
PAID_COLS = ("Знак", "Номер", "Сумма")

Q_WORKS = """ВЫБРАТЬ
	НАЧАЛОПЕРИОДА(У.Ссылка.Дата, ДЕНЬ) КАК День,
	ПРЕДСТАВЛЕНИЕ(У.Работа) КАК Работа,
	КОЛИЧЕСТВО(РАЗЛИЧНЫЕ У.Ссылка) КАК ЗН,
	СУММА(У.Всего) КАК Сумма
ИЗ
	Документ.ЗаказНаряд.Услуги КАК У
ГДЕ
	У.Ссылка.Проведен
	И У.Ссылка.Дата МЕЖДУ &Начало И &Конец
СГРУППИРОВАТЬ ПО
	НАЧАЛОПЕРИОДА(У.Ссылка.Дата, ДЕНЬ),
	У.Работа"""
WORK_COLS = ("День", "Работа", "ЗН", "Сумма")


def _iso(v) -> str:
    t = _dt(v)
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else ""


def _num(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------- строки -> таблицы
def calls_table(rows: list[dict], dirs: Directions, end: datetime) -> list[dict]:
    built = build_calls(rows, dirs, end, phones=True)
    raw = {_s(r["УИД"]): r for r in rows}
    out = []
    for c in built:
        r = raw.get(c["id"], {})
        start, finish = _dt(r.get("Начало")), _dt(r.get("Окончание"))
        # «Окончание − Начало» больше разговора - значит, это время от звонка до ответа
        wait = ""
        if start and finish and c["длительность_с"] > 0:
            w = (finish - start).total_seconds() - c["длительность_с"]
            wait = w if 0 < w < 600 else ""
        out.append({"id": c["id"], "start": c["начало"], "end": c["окончание"],
                    "dir": {"входящий": "in", "исходящий": "out"}.get(c["направление"], ""),
                    "dur": c["длительность_с"], "wait": wait, "missed": c["пропущен"] == "да",
                    "callback": c["перезвонили"], "callback_min": c["перезвон_через_мин"],
                    "user": c["менеджер"], "client_code": c["клиент_код"], "client": c["клиент"],
                    "line": c["линия"], "phone": c.get("телефон", "")})
    return out


def orders_table(rows: list[dict]) -> list[dict]:
    return [{"number": _s(r["Номер"]), "date": _iso(r["Дата"]), "created": _iso(r["Создан"]) or _iso(r["Дата"]),
             "author": _s(r["Автор"]), "type": _s(r["Тип"]), "status": _s(r["Статус"]),
             "source": _s(r["Источник"]), "client_code": _s(r["КлиентКод"]), "client": _s(r["Клиент"]),
             "car": _s(r["Автомобиль"]), "master": _s(r["Мастер"]), "in_work": _iso(r["ВРаботе"]),
             "done": _iso(r["Завершен"]), "issued": _iso(r["Выдан"]), "repeat": "да" if r["Повтор"] else "",
             "posted": "да" if r["Проведен"] else "", "amount": _num(r["Сумма"]), "works": _num(r["Работы"]),
             "parts": _num(r["Запчасти"]), "materials": _num(r["Материалы"])} for r in rows]


def order_extras(orders: list[dict], rows: list[dict]) -> list[dict]:
    """Дописывает к ЗН плановую дату, причину отказа, менеджера; удалённые ЗН - отдельными строками."""
    by_num = {o["number"]: o for o in orders}
    out = list(orders)
    for r in rows:
        num = _s(r["Номер"])
        o = by_num.get(num)
        extra = {"plan": _iso(r["План"]), "refusal": _s(r["Отказ"]), "manager": _s(r["Менеджер"])}
        if o is not None:
            o.update(extra)
        elif r["Удален"]:
            out.append({"number": num, "date": _iso(r["Дата"]), "created": _iso(r["Создан"]) or _iso(r["Дата"]),
                        "author": _s(r["Автор"]), "type": _s(r["Тип"]), "status": _s(r["Статус"]),
                        "source": _s(r["Источник"]), "client_code": _s(r["КлиентКод"]), "client": _s(r["Клиент"]),
                        "car": _s(r["Автомобиль"]), "master": _s(r["Мастер"]), "in_work": "", "done": "",
                        "issued": "", "repeat": "", "posted": "", "amount": 0, "works": 0, "parts": 0,
                        "materials": 0, "deleted": "да", **extra})
    return out


def paid_table(rows: list[dict]) -> list[dict]:
    by: dict[str, float] = {}
    for r in rows:
        num = _s(r["Номер"])
        if num:
            by[num] = by.get(num, 0.0) + _num(r["Сумма"]) * (1 if _int(r["Знак"]) > 0 else -1)
    return [{"number": k, "amount": round(v, 2)} for k, v in by.items()]


def visits_table(rows: list[dict]) -> list[dict]:
    return [{"client_code": _s(r["КлиентКод"]), "day": _iso(r["День"])[:10], "type": _s(r["Тип"])}
            for r in rows if _s(r["КлиентКод"]) and _iso(r["День"])]


def clients_table(rows: list[dict]) -> list[dict]:
    return [{"code": _s(r["Код"]), "name": _s(r["Наименование"]), "created": _iso(r["Создан"]),
             "author": _s(r["Автор"]), "kind": _s(r["Вид"]), "source": _s(r["Источник"]),
             "deleted": "да" if r["Удален"] else ""} for r in rows if _s(r["Код"])]


def phones_table(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        code, digits = _s(r["Код"]), re.sub(r"\D", "", _s(r["Телефон"]))[-10:]
        if code and len(digits) >= 6 and (code, digits) not in seen:
            seen.add((code, digits))
            out.append({"code": code, "phone": digits})
    return out


def payments_table(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        box = _s(r["Касса"])
        direction, method = classify_cashbox(box)
        if not direction:
            continue                       # «Вывод ДС» - не выручка
        out.append({"date": _iso(r["Дата"]), "number": _s(r["Номер"]),
                    "amount": _num(r["Сумма"]) * (1 if _int(r["Знак"]) > 0 else -1),
                    "cashbox": box, "method": method or "Прочее", "direction": direction,
                    "client_code": _s(r["КлиентКод"]), "client": _s(r["Клиент"]),
                    "basis_kind": _s(r["ВидОснования"]), "basis_number": _s(r["НомерОснования"])})
    return out


def works_table(rows: list[dict]) -> list[dict]:
    return [{"day": _iso(r["День"])[:10], "work": _s(r["Работа"]) or "(без названия)", "orders": _int(r["ЗН"]),
             "amount": _num(r["Сумма"])} for r in rows]


def tasks_table(rows: list[dict], now: datetime) -> list[dict]:
    return [{"created": t["создана"], "type": t["тип"], "is_task": t["это_задача"] == "да",
             "executor": t["исполнитель"], "done": t["выполнено"] == "да", "done_at": t["выполнена"],
             "hours_to_done": t["часов_до_выполнения"]} for t in build_tasks(rows, now)]


# ---------------------------------------------------------------------------- 1С
EPOCH = datetime(2000, 1, 1)
# дневное обновление: что перечитывается и с какой глубины (дней назад, с полуночи)
LIGHT_DAYS = {"calls": 3, "orders": 30, "payments": 3, "tasks": 3, "clients": 3}
DATE_KEY = {"calls": "start", "orders": "date", "payments": "date", "tasks": "created", "works": "day"}


def collect(conn, months: int, dirs: Directions, now: datetime | None = None,
            light: bool = False) -> tuple[dict, dict]:
    """Полный сбор (light=False) или дневное обновление (light=True - только свежие разделы)."""
    now = now or datetime.now()
    d1 = (now - timedelta(days=round(months * 30.5))).date()
    full_start = datetime(d1.year, d1.month, 1)          # с начала месяца - чтобы месяцы были полными
    end = now
    midnight = datetime(now.year, now.month, now.day)

    def since(key):
        return midnight - timedelta(days=LIGHT_DAYS[key]) if light else full_start

    def run(text, cols, start):
        q = conn.NewObject("Запрос")
        q.Text, _ = inline_dates(text, {"Начало": start, "Конец": end, "НачалоКарточек": start,
                                        "КонецСЗапасом": end + timedelta(hours=CALLBACK_HOURS)})
        sel = q.Execute().Choose()
        rows = []
        while sel.Next():
            rows.append({c: getattr(sel, c) for c in cols})
        return rows

    tables: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def orders():
        rows = orders_table(run(Q_ORDERS, ORDER_COLS, since("orders")))
        try:
            attrs = order_head_attrs(conn)
            if attrs:
                q, cols = q_order_head(attrs)
                head = {_s(r["Номер"]): r for r in run(q, cols, since("orders"))}
                for o in rows:
                    h = head.get(o["number"]) or {}
                    for key, alias, *_ in HEAD_FIELDS:
                        o[key] = _s(h.get(alias)) if attrs.get(key) else ""
        except Exception as e:  # noqa: BLE001 - без вида ЗН всё остальное работает
            errors["Вид, плательщик, договор ЗН"] = str(e)
        try:
            return order_extras(rows, run(Q_ORDER_EXTRA, EXTRA_COLS, since("orders")))
        except Exception as e:  # noqa: BLE001 - без записей/отказов ЗН всё равно нужны
            errors["Записи и отказы"] = str(e)
            return rows

    sections = [
        ("calls", "Звонки", lambda: calls_table(run(Q_CALLS, CALL_COLS, since("calls")), dirs, end)),
        ("orders", "Заказ-наряды", orders),
        ("payments", "Оплаты", lambda: payments_table(run(Q_PAYMENTS, PAY_COLS, since("payments")))),
        ("tasks", "Задачи", lambda: tasks_table(run(Q_TASKS, TASK_COLS, since("tasks")), now)),
        ("clients", "Карточки клиентов", lambda: clients_table(run(Q_CLIENTS_NEW if light else Q_CLIENTS,
                                                                   CLIENT_COLS, since("clients")))),
        ("balances", "Остатки касс и счетов", lambda: balances_table(run(Q_BALANCES, BAL_COLS, end))),
    ]
    if not light:
        sections += [
            ("works", "Работы", lambda: works_table(run(Q_WORKS, WORK_COLS, full_start))),
            ("paid", "Оплаты по ЗН (все способы)", lambda: paid_table(run(Q_ORDER_PAID, PAID_COLS, full_start))),
            ("visits", "История визитов", lambda: visits_table(run(Q_VISITS, VISIT_COLS, EPOCH))),
            ("phones", "Телефоны клиентов", lambda: phones_table(run(Q_PHONES, PHONE_COLS, EPOCH))),
            ("call_fields", "Поля звонков", lambda: collect_call_fields(conn, midnight - timedelta(days=30), end)),
        ]
    for key, title, fn in sections:
        try:
            tables[key] = fn()
        except Exception as e:  # noqa: BLE001 - один раздел не должен ронять остальные
            tables[key] = []
            errors[title] = str(e)
    meta = {"collected_at": now.strftime("%Y-%m-%d %H:%M:%S"), "from": full_start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"), "errors": errors, "demo": False,
            "counts": {k: len(v) for k, v in tables.items()}}
    if "Остатки касс и счетов" not in errors:
        meta["balances_at"] = now.strftime("%Y-%m-%d %H:%M")
    if light:
        meta["cutoffs"] = {k: since(k).strftime("%Y-%m-%d %H:%M:%S") for k in LIGHT_DAYS}
    return tables, meta


def merge_light(old: dict, old_meta: dict, new: dict, new_meta: dict) -> tuple[dict, dict]:
    """Свежие строки дневного обновления подменяют те же строки в файле полного сбора.
    Раздел, который при обновлении не прочитался, остаётся как был утром."""
    tables = {k: list(v) for k, v in old.items()}
    failed = set(new_meta.get("errors", {}))
    titles = {"calls": "Звонки", "orders": "Заказ-наряды", "payments": "Оплаты", "tasks": "Задачи",
              "clients": "Карточки клиентов"}
    for key, cutoff in new_meta.get("cutoffs", {}).items():
        if titles.get(key) in failed or key not in new:
            continue
        if key == "clients":
            fresh = {c["code"] for c in new[key]}
            tables[key] = [c for c in tables.get(key, []) if c["code"] not in fresh] + new[key]
            continue
        dk = DATE_KEY[key]
        tables[key] = [r for r in tables.get(key, []) if (r.get(dk) or "") < cutoff] + new[key]
    if "balances" in new and "Остатки касс и счетов" not in failed:   # остатки - всегда на момент сбора
        tables["balances"] = new["balances"]
    meta = dict(old_meta)
    meta["updated_at"] = new_meta["collected_at"]
    meta["to"] = new_meta["to"]
    if new_meta.get("balances_at"):
        meta["balances_at"] = new_meta["balances_at"]
    refreshed = set(titles.values()) | {"Записи и отказы", "Остатки касс и счетов", "Вид, плательщик, договор ЗН"}
    # ошибки утренних разделов, которые днём не перечитываются, остаются; остальные - по свежему разу
    meta["errors"] = {**{k: v for k, v in old_meta.get("errors", {}).items() if k not in refreshed},
                      **new_meta.get("errors", {})}
    meta["counts"] = {k: len(v) for k, v in tables.items()}
    return tables, meta


def collect_to_file(conn, path: Path, months: int, dirs: Directions, light: bool = False) -> dict:
    if light and Path(path).exists():
        old, old_meta = read_db(path)
        new, new_meta = collect(conn, months, dirs, light=True)
        tables, meta = merge_light(old, old_meta, new, new_meta)
    else:                                   # файла ещё нет - обновлять нечего, делаем полный сбор
        tables, meta = collect(conn, months, dirs)
    write_db(path, tables, meta)
    return meta


__all__ = ["collect", "collect_to_file", "merge_light", "TASK_TYPES", "date"]
