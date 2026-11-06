"""Залежавшиеся запчасти: что лежит на складе дольше N дней после прихода.

Как считается (Далион 1.3.6, только чтение):
* остаток на складах - регистр «Остатки товаров» (склад = справочник «Склады»);
  запчасти, выданные в заказ-наряд (склад = заказ-наряд, внутренняя накладная),
  идут отдельным списком: они уже не на полке, но и не проданы;
* к остатку подбираются приходные накладные этой позиции от новых к старым
  (продаются сначала старые партии - значит, на полке лежит остаток последних приходов);
* часть остатка, пришедшая N и больше дней назад, попадает в отчёт: дата прихода,
  номер накладной, поставщик, кто провёл, менеджер, сумма по цене прихода;
* строки приходов, заказанные под заказ-наряд (колонка «Заказ» в накладной), - отдельный лист;
* группы номенклатуры из настройки «Не учитывать группы» (масло, фильтры) исключаются:
  ищутся группы, в названии которых есть одно из слов.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..common.comdate import com_datetime, inline_dates

LOOKBACK_DAYS = 730          # дальше двух лет приходы не ищем
NO_RECEIPT = "приход не найден"


@dataclass
class Receipt:
    item: str
    dt: datetime
    number: str
    supplier: str
    author: str
    manager: str
    qty: float
    total: float
    order: str = ""          # под какой заказ-наряд заказано
    kind: str = ""           # вид операции накладной

    @property
    def price(self) -> float:
        return self.total / self.qty if self.qty else 0.0


@dataclass
class StockItem:
    key: str                 # уникальный ключ номенклатуры (ссылка или представление)
    name: str
    article: str
    group: str
    qty: float


@dataclass
class StaleRow:
    name: str
    article: str
    group: str
    qty: float               # сколько из этой партии лежит
    received: date | None    # None - приход не найден (старше двух лет или ввод остатков)
    days: int | None
    number: str
    supplier: str
    author: str
    manager: str
    price: float
    order: str = ""
    kind: str = ""

    @property
    def amount(self) -> float:
        return round(self.qty * self.price, 2)


@dataclass
class IssuedRow:
    """Запчасть выдана в заказ-наряд (внутренней накладной), заказ-наряд не закрыт."""
    order: str
    order_date: date | None
    days: int | None
    name: str
    article: str
    qty: float


@dataclass
class StockData:
    on: date
    min_days: int
    stale: list[StaleRow] = field(default_factory=list)       # лежат на складе, не под заказ
    ordered: list[StaleRow] = field(default_factory=list)     # пришли под заказ-наряд, лежат
    issued: list[IssuedRow] = field(default_factory=list)     # выданы в ЗН, ЗН не закрыт
    excluded_groups: list[str] = field(default_factory=list)
    items_in_stock: int = 0
    loaded_at: datetime = field(default_factory=datetime.now)

    @property
    def total(self) -> float:
        return round(sum(r.amount for r in self.stale), 2)


# ------------------------------------------------------------------ расчёт (без 1С)
def allocate(item: StockItem, receipts: list[Receipt], today: date, min_days: int) -> list[StaleRow]:
    """Остаток позиции раскладывается по приходам от новых к старым; возвращает части
    остатка, которые лежат min_days дней и больше."""
    out = []
    left = item.qty
    last_price = 0.0
    for r in sorted(receipts, key=lambda r: r.dt, reverse=True):
        if left <= 1e-9:
            break
        take = min(left, r.qty)
        left -= take
        last_price = last_price or r.price
        days = (today - r.dt.date()).days
        if days >= min_days:
            out.append(StaleRow(item.name, item.article, item.group, round(take, 3), r.dt.date(), days,
                                r.number, r.supplier, r.author, r.manager, round(r.price, 2), r.order, r.kind))
    if left > 1e-9:
        out.append(StaleRow(item.name, item.article, item.group, round(left, 3), None, None,
                            NO_RECEIPT, "", "", "", round(last_price, 2)))
    return out


def sort_rows(rows: list[StaleRow]) -> list[StaleRow]:
    """Сначала то, что лежит дольше всего (без даты - самое старое), затем дороже."""
    return sorted(rows, key=lambda r: (-(r.days if r.days is not None else 10 ** 6), -r.amount, r.name))


def group_words(text: str) -> list[str]:
    return [w.strip() for w in re.split(r"[,;]", text or "") if w.strip()]


# ------------------------------------------------------------------ запросы
Q_GROUPS = """ВЫБРАТЬ
	Г.Ссылка КАК Ссылка,
	Г.Наименование КАК Имя
ИЗ
	Справочник.Номенклатура КАК Г
ГДЕ
	Г.ЭтоГруппа
	И ({cond})"""

Q_STOCK = """ВЫБРАТЬ
	О.Номенклатура КАК Ном,
	ПРЕДСТАВЛЕНИЕ(О.Номенклатура) КАК Имя,
	{article} КАК Артикул,
	ПРЕДСТАВЛЕНИЕ(О.Номенклатура.Родитель) КАК Группа,
	СУММА(О.КоличествоОстаток) КАК Кол
ИЗ
	РегистрНакопления.ОстаткиТоваров.Остатки(, Склад ССЫЛКА Справочник.Склады
		{excl}) КАК О
СГРУППИРОВАТЬ ПО
	О.Номенклатура
ИМЕЮЩИЕ
	СУММА(О.КоличествоОстаток) > 0"""

Q_ISSUED = """ВЫБРАТЬ
	ПРЕДСТАВЛЕНИЕ(О.Склад) КАК ЗН,
	О.Склад.Дата КАК ДатаЗН,
	ПРЕДСТАВЛЕНИЕ(О.Номенклатура) КАК Имя,
	{article} КАК Артикул,
	СУММА(О.КоличествоОстаток) КАК Кол
ИЗ
	РегистрНакопления.ОстаткиТоваров.Остатки(, Склад ССЫЛКА Документ.ЗаказНаряд
		{excl}) КАК О
СГРУППИРОВАТЬ ПО
	О.Склад,
	О.Номенклатура
ИМЕЮЩИЕ
	СУММА(О.КоличествоОстаток) > 0"""

Q_RECEIPTS = """ВЫБРАТЬ
	Т.Номенклатура КАК Ном,
	Т.Ссылка.Дата КАК Дата,
	Т.Ссылка.Номер КАК Номер,
	ПРЕДСТАВЛЕНИЕ(Т.Ссылка.Контрагент) КАК Поставщик,
	ПРЕДСТАВЛЕНИЕ(Т.Ссылка.Автор) КАК Автор,
	ПРЕДСТАВЛЕНИЕ(Т.Ссылка.Менеджер) КАК Менеджер,
	ПРЕДСТАВЛЕНИЕ(Т.Ссылка.ВидОперации) КАК Вид,
	Т.Количество КАК Кол,
	Т.Всего КАК Всего,
	ВЫБОР
		КОГДА Т.ЗаказПокупателя ССЫЛКА Документ.ЗаказНаряд
			ТОГДА ПРЕДСТАВЛЕНИЕ(Т.ЗаказПокупателя)
		ИНАЧЕ ""
	КОНЕЦ КАК ПодЗаказ
ИЗ
	Документ.ПриходнаяНакладная.Товары КАК Т
ГДЕ
	Т.Ссылка.Проведен
	И Т.Ссылка.Дата >= &С
	И Т.Номенклатура В (&Номенклатура)
	И Т.Количество > 0
УПОРЯДОЧИТЬ ПО
	Дата УБЫВ"""

ARTICLE = "О.Номенклатура.Артикул"


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def load_stock(conn, today: date, min_days: int = 14, exclude: list[str] | None = None) -> StockData:
    """conn - подключение COM к Далиону."""
    def run(text: str, cols: tuple[str, ...], **params):
        q = conn.NewObject("Запрос")
        q.Text, rest = inline_dates(text, params)
        for k, v in rest.items():
            q.SetParameter(k, v)
        sel = q.Execute().Choose()
        rows = []
        while sel.Next():
            rows.append({c: getattr(sel, c) for c in cols})
        return rows

    def array(values):
        a = conn.NewObject("Массив")
        for v in values:
            a.Add(v)
        return a

    def with_article(text: str, cols: tuple[str, ...], **params):
        """Артикул есть не во всех базах: если реквизита нет - без него."""
        cond = "И НЕ Номенклатура В ИЕРАРХИИ (&Исключить)" if groups else ""
        try:
            return run(text.format(article=ARTICLE, excl=cond), cols, **params)
        except Exception:  # noqa: BLE001
            return run(text.format(article='""', excl=cond), cols, **params)

    data = StockData(today, min_days)

    # группы-исключения: в названии есть одно из слов
    words = group_words(", ".join(exclude or []))
    groups = []
    if words:
        cond = " ИЛИ ".join(f"Г.Наименование ПОДОБНО &С{i}" for i in range(len(words)))
        params = {f"С{i}": f"%{w}%" for i, w in enumerate(words)}
        for r in run(Q_GROUPS.format(cond=cond), ("Ссылка", "Имя"), **params):
            groups.append(r["Ссылка"])
            data.excluded_groups.append(_str(r["Имя"]))
    excl = array(groups)

    stock = with_article(Q_STOCK, ("Ном", "Имя", "Артикул", "Группа", "Кол"), Исключить=excl)
    items = {}
    refs = []
    for r in stock:
        key = _ref_key(conn, r["Ном"])
        items[key] = StockItem(key, _str(r["Имя"]), _str(r["Артикул"]), _str(r["Группа"]), _num(r["Кол"]))
        refs.append((key, r["Ном"]))
    data.items_in_stock = len(items)

    # приходы этих позиций; ключ сопоставляем по ссылке через представление 1С
    by_ref = {}
    if refs:
        since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), datetime.min.time())
        for r in run(Q_RECEIPTS, ("Ном", "Дата", "Номер", "Поставщик", "Автор", "Менеджер", "Вид",
                                  "Кол", "Всего", "ПодЗаказ"),
                     С=since, Номенклатура=array(ref for _, ref in refs)):
            rec = Receipt("", com_datetime(r["Дата"]), _str(r["Номер"]), _str(r["Поставщик"]),
                          _str(r["Автор"]), _str(r["Менеджер"]), _num(r["Кол"]), _num(r["Всего"]),
                          _str(r["ПодЗаказ"]), _str(r["Вид"]))
            by_ref.setdefault(_ref_key(conn, r["Ном"]), []).append(rec)

    for key, _ref in refs:
        for row in allocate(items[key], by_ref.get(key, []), today, min_days):
            (data.ordered if row.order else data.stale).append(row)
    data.stale, data.ordered = sort_rows(data.stale), sort_rows(data.ordered)

    for r in with_article(Q_ISSUED, ("ЗН", "ДатаЗН", "Имя", "Артикул", "Кол"), Исключить=excl):
        d = com_datetime(r["ДатаЗН"]).date() if r["ДатаЗН"] and hasattr(r["ДатаЗН"], "year") else None
        days = (today - d).days if d else None
        if days is None or days >= min_days:
            data.issued.append(IssuedRow(_str(r["ЗН"]), d, days, _str(r["Имя"]), _str(r["Артикул"]),
                                         _num(r["Кол"])))
    data.issued.sort(key=lambda r: (-(r.days or 0), r.order, r.name))
    return data


def _ref_key(conn, ref) -> str:
    """Ключ ссылки 1С для сопоставления: уникальный идентификатор, а если его не получить -
    сама ссылка (в тестах это строка)."""
    try:
        return str(conn.XMLString(ref))
    except Exception:  # noqa: BLE001
        return str(ref)
