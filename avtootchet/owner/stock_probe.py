"""«Проверить структуру склада»: как в этой базе Далиона устроены приход и расход запчастей.

Нужен, чтобы построить отчёт «что закупили и не продали» под настоящую базу.
Только чтение и только НАЗВАНИЯ (документов, реквизитов, регистров) и КОЛИЧЕСТВА:
ни сумм, ни номенклатуры, ни клиентов, ни фамилий в отчёт не попадает.
Каждая проверка независима: ошибка одной попадает в текст, остальные выполняются.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .probe import Probe
from .sources import ComSource, SourceError

# документы, которые могут двигать товар (по имени или синониму)
DOC_RE = re.compile(r"приход|поступ|расход|списан|перемещ|оприход|возврат|заказ|инвентар|"
                    r"пересорт|комплект|реализ|накладн|чек", re.I)
SKIP_RE = re.compile(r"денег|денеж|ДС$|касс|зарплат|аванс", re.I)
# реквизиты шапки, по которым ищем «менеджера», склад и связь с заказом
HEAD_RE = re.compile(r"ответствен|автор|менеджер|сотрудник|пользоват|склад|основан|заказ|"
                     r"контрагент|поставщик|подразделен|вид", re.I)
ARTICLE_OBJ_RE = re.compile(r"артикул|каталож|кросс|аналог|штрих|номерпроизв|производител", re.I)
GOODS_RE = re.compile(r"номенклатур|товар|запчаст", re.I)
MAX_ATTRS = 40


def _prop(obj, *names):
    """Свойство объекта 1С по английскому или русскому имени (НЕ вызывать: COM-объекты вызываемы)."""
    for n in names:
        try:
            return getattr(obj, n)
        except Exception:  # noqa: BLE001
            continue
    raise AttributeError(names[0])


def _call(obj, *names, args=()):
    for n in names:
        try:
            f = getattr(obj, n)
        except Exception:  # noqa: BLE001
            continue
        return f(*args)
    raise AttributeError(names[0])


def _find(coll, name: str):
    """Объект метаданных по имени: Find(), а если его нет - перебором."""
    try:
        return _call(coll, "Find", "Найти", args=(name,))
    except AttributeError:
        for o in _items(coll):
            if str(_prop(o, "Name", "Имя")) == name:
                return o
    raise AttributeError(name)


def _items(coll) -> list:
    n = int(_call(coll, "Count", "Количество"))
    return [_call(coll, "Get", "Получить", args=(i,)) for i in range(n)]


class StockProbe(Probe):
    # ---------------------------------------------------------------- метаданные
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

    def _type(self, attr) -> str:
        try:
            return self._s(_prop(attr, "Type", "Тип"))
        except AttributeError:
            return "?"

    def _attrs(self, coll, limit: int = MAX_ATTRS) -> str:
        items = _items(coll)
        text = ", ".join(f"{self._name(a)}: {self._type(a)}" for a in items[:limit])
        return text + (f", … ещё {len(items) - limit}" if len(items) > limit else "")

    def goods_documents(self) -> list:
        docs = [d for d in _items(_prop(self._md(), "Documents", "Документы"))
                if DOC_RE.search(self._title(d)) and not SKIP_RE.search(self._name(d))]
        self.doc_names = [self._name(d) for d in docs]
        self.doc_meta = {self._name(d): d for d in docs}
        out = []
        for d in docs:
            out.append(f"[{self._title(d)}]")
            out.append(f"  шапка: {self._attrs(_prop(d, 'Attributes', 'Реквизиты'))}")
            for ts in _items(_prop(d, "TabularSections", "ТабличныеЧасти")):
                out.append(f"  ТЧ {self._name(ts)}: {self._attrs(_prop(ts, 'Attributes', 'Реквизиты'))}")
        return out

    def registers(self) -> list:
        regs = _items(_prop(self._md(), "AccumulationRegisters", "РегистрыНакопления"))
        self.goods_regs = []
        out = []
        for r in regs:
            dims = _items(_prop(r, "Dimensions", "Измерения"))
            if any(GOODS_RE.search(self._name(d)) for d in dims):
                self.goods_regs.append(self._name(r))
            try:
                kind = self._s(_prop(r, "RegisterType", "ВидРегистра"))
            except AttributeError:
                kind = "?"
            out.append(f"[{self._title(r)}] вид: {kind}")
            out.append(f"  измерения: {self._attrs(_prop(r, 'Dimensions', 'Измерения'))}")
            out.append(f"  ресурсы: {self._attrs(_prop(r, 'Resources', 'Ресурсы'))}")
            out.append(f"  реквизиты: {self._attrs(_prop(r, 'Attributes', 'Реквизиты'))}")
        return out

    # ---------------------------------------------------------------- количества
    def register_moves(self) -> list:
        out = []
        for reg in getattr(self, "goods_regs", []):
            for text in (Q_MOVES_BALANCE.format(reg=reg), Q_MOVES_TURNOVER.format(reg=reg)):
                try:
                    rows = self._rows(text, "Тип", "Движение", "Кол")
                    break
                except Exception:  # noqa: BLE001  - у регистра оборотов нет ВидДвижения
                    rows = None
            if rows is None:
                out.append(f"[{reg}] не удалось прочитать")
                continue
            out.append(f"[{reg}]")
            out.extend(f"  {self._s(t)} | {m}: {int(n)}" for t, m, n in rows)
        return out

    def documents_fill(self) -> list:
        out = []
        for name in getattr(self, "doc_names", []):
            d = self.doc_meta[name]
            try:
                total = self._rows(Q_COUNT.format(doc=name), "Кол")
            except Exception as e:  # noqa: BLE001
                out.append(f"[{name}] ОШИБКА: {e}")
                continue
            n = int(total[0][0]) if total else 0
            if not n:
                continue
            out.append(f"[{name}] проведено за период: {n}")
            for a in _items(_prop(d, "Attributes", "Реквизиты")):
                an = self._name(a)
                if not HEAD_RE.search(an):
                    continue
                try:
                    rows = self._rows(Q_DISTINCT.format(doc=name, attr=an), "Разных")
                    out.append(f"  {an}: разных значений {int(rows[0][0]) if rows else 0}")
                except Exception as e:  # noqa: BLE001
                    out.append(f"  {an}: ОШИБКА: {e}")
        return out

    def nomenclature(self) -> list:
        cat = _find(_prop(self._md(), "Catalogs", "Справочники"), "Номенклатура")
        out = [f"шапка: {self._attrs(_prop(cat, 'Attributes', 'Реквизиты'), 500)}"]
        try:
            out.append(f"длина кода: {int(_prop(cat, 'CodeLength', 'ДлинаКода'))}, "
                       f"тип кода: {self._s(_prop(cat, 'CodeType', 'ТипКода'))}")
        except (AttributeError, TypeError, ValueError):
            pass
        for ts in _items(_prop(cat, "TabularSections", "ТабличныеЧасти")):
            out.append(f"ТЧ {self._name(ts)}: {self._attrs(_prop(ts, 'Attributes', 'Реквизиты'))}")
        out.append("заполненность строковых реквизитов (сколько позиций, не групп, с непустым значением):")
        for a in _items(_prop(cat, "Attributes", "Реквизиты")):
            if "строк" not in self._type(a).lower():
                continue
            an = self._name(a)
            try:
                rows = self._rows(Q_FILLED.format(attr=an), "Кол")
                out.append(f"  {an}: {int(rows[0][0]) if rows else 0}")
            except Exception as e:  # noqa: BLE001
                out.append(f"  {an}: ОШИБКА: {e}")
        return out

    def article_objects(self) -> list:
        out = []
        for coll, label in (("Catalogs", "справочник"), ("InformationRegisters", "регистр сведений")):
            ru = {"Catalogs": "Справочники", "InformationRegisters": "РегистрыСведений"}[coll]
            for o in _items(_prop(self._md(), coll, ru)):
                if not ARTICLE_OBJ_RE.search(self._title(o)):
                    continue
                parts = []
                for kind, en, ruk in (("измерения", "Dimensions", "Измерения"), ("ресурсы", "Resources", "Ресурсы"),
                                      ("реквизиты", "Attributes", "Реквизиты")):
                    try:
                        parts.append(f"{kind}: {self._attrs(_prop(o, en, ruk))}")
                    except AttributeError:
                        continue
                out.append(f"[{label} {self._title(o)}] " + "; ".join(parts))
        return out

    def run(self) -> str:
        self.src._check_settings()
        self.conn = self.src._connect()
        self.lines = [f"Структура склада Далиона, количества за {self.d1:%d.%m.%Y}–{self.d2:%d.%m.%Y}",
                      "Только названия документов и реквизитов и количества — без сумм, "
                      "номенклатуры, клиентов и фамилий."]
        self._section("1. Документы движения товаров: реквизиты шапки и табличных частей",
                      self.goods_documents)
        self._section("2. Регистры накопления: измерения, ресурсы, реквизиты", self.registers)
        self._section("3. Регистры с номенклатурой: какие документы их двигают (записей)",
                      self.register_moves)
        self._section("4. Документы за период: сколько проведено и сколько разных значений "
                      "в реквизитах «ответственный / склад / заказ / основание»", self.documents_fill)
        self._section("5. Справочник «Номенклатура»: где хранится артикул", self.nomenclature)
        self._section("6. Справочники и регистры сведений про артикулы, каталожные номера, аналоги",
                      self.article_objects)
        return "\n".join(self.lines)


Q_MOVES_BALANCE = """ВЫБРАТЬ
	ТИПЗНАЧЕНИЯ(Р.Регистратор) КАК Тип,
	ВЫБОР КОГДА Р.ВидДвижения = ЗНАЧЕНИЕ(ВидДвиженияНакопления.Приход) ТОГДА "приход" ИНАЧЕ "расход" КОНЕЦ КАК Движение,
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	РегистрНакопления.{reg} КАК Р
ГДЕ
	Р.Активность
	И Р.Период МЕЖДУ &Начало И &Конец
СГРУППИРОВАТЬ ПО
	ТИПЗНАЧЕНИЯ(Р.Регистратор),
	ВЫБОР КОГДА Р.ВидДвижения = ЗНАЧЕНИЕ(ВидДвиженияНакопления.Приход) ТОГДА "приход" ИНАЧЕ "расход" КОНЕЦ"""

Q_MOVES_TURNOVER = """ВЫБРАТЬ
	ТИПЗНАЧЕНИЯ(Р.Регистратор) КАК Тип,
	"обороты" КАК Движение,
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	РегистрНакопления.{reg} КАК Р
ГДЕ
	Р.Активность
	И Р.Период МЕЖДУ &Начало И &Конец
СГРУППИРОВАТЬ ПО
	ТИПЗНАЧЕНИЯ(Р.Регистратор)"""

Q_FILLED = """ВЫБРАТЬ
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	Справочник.Номенклатура КАК Н
ГДЕ
	НЕ Н.ЭтоГруппа
	И ВЫРАЗИТЬ(Н.{attr} КАК СТРОКА(150)) <> ""
"""

Q_COUNT = """ВЫБРАТЬ
	КОЛИЧЕСТВО(*) КАК Кол
ИЗ
	Документ.{doc} КАК Д
ГДЕ
	Д.Проведен
	И Д.Дата МЕЖДУ &Начало И &Конец"""

Q_DISTINCT = """ВЫБРАТЬ
	КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Д.{attr}) КАК Разных
ИЗ
	Документ.{doc} КАК Д
ГДЕ
	Д.Проведен
	И Д.Дата МЕЖДУ &Начало И &Конец"""


def run_stock_probe(src: ComSource, days: int = 31) -> str:
    if not isinstance(src, ComSource):
        raise SourceError("Проверка структуры работает только с 1С:Далион через COM")
    d2 = date.today()
    return StockProbe(src, d2 - timedelta(days=days - 1), d2).run()
