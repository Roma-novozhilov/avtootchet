"""Отчёт «Анализ бизнес-процесса» в PDF, оформление по ГОСТ 7.32-2017.

* А4, поля: левое 30 мм, правое 15 мм, верхнее и нижнее 20 мм;
* шрифт Times New Roman (если его нет - метрически совместимый Liberation Serif), 14 пт,
  полуторный интервал, абзацный отступ 1,25 см, выравнивание по ширине; весь текст чёрный;
* структурные элементы (РЕФЕРАТ, СОДЕРЖАНИЕ, ВВЕДЕНИЕ, ЗАКЛЮЧЕНИЕ, …) - прописными
  полужирными по центру, каждый и каждый раздел - с новой страницы;
* страницы нумеруются арабскими цифрами внизу по центру, титульный лист считается, но
  номер на нём не ставится;
* «Таблица N — Название» над таблицей слева, «Рисунок N — Название» под рисунком по центру.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.shapes import Drawing, Line, Path as GPath, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from .process import (CREATED, DONE, NOT_STARTED, PAID, SHIPPED, STAGES, WORK, ProcessData, fmt_hours)

BLACK = colors.black
GRAY = colors.Color(0.55, 0.55, 0.55)
LIGHT = colors.Color(0.85, 0.85, 0.85)
LEFT, RIGHT, TOP, BOTTOM = 30 * mm, 15 * mm, 20 * mm, 20 * mm
WIDTH = A4[0] - LEFT - RIGHT
MAP_MIN_SHARE = 0.05      # переход рисуется на схеме, если он есть хотя бы у 5 % ЗН
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# ---------------------------------------------------------------------------- шрифт
FONT, FONT_B, FONT_I = "GostSerif", "GostSerif-Bold", "GostSerif-Italic"
_CANDIDATES = [
    (r"C:\Windows\Fonts\times.ttf", r"C:\Windows\Fonts\timesbd.ttf", r"C:\Windows\Fonts\timesi.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
]


def register_fonts() -> str:
    """Зарегистрировать Times New Roman (или замену). Возвращает имя использованного файла."""
    if FONT in pdfmetrics.getRegisteredFontNames():
        return ""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    cands = [tuple(p.replace(r"C:\Windows", windir) for p in c) for c in _CANDIDATES[:1]] + _CANDIDATES
    for reg, bold, ital in cands:
        if Path(reg).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont(FONT, reg))
            pdfmetrics.registerFont(TTFont(FONT_B, bold))
            pdfmetrics.registerFont(TTFont(FONT_I, ital if Path(ital).exists() else reg))
            pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_B, italic=FONT_I, boldItalic=FONT_B)
            return reg
    raise RuntimeError("Не найден шрифт Times New Roman (C:\\Windows\\Fonts\\times.ttf)")


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName=FONT, fontSize=14, leading=21, textColor=BLACK)
    s = {
        "body": ParagraphStyle("body", **base, alignment=TA_JUSTIFY, firstLineIndent=12.5 * mm),
        "list": ParagraphStyle("list", **base, alignment=TA_JUSTIFY, firstLineIndent=12.5 * mm),
        "plain": ParagraphStyle("plain", **base, alignment=TA_LEFT),
        "center": ParagraphStyle("center", **base, alignment=TA_CENTER),
        "struct": ParagraphStyle("struct", **{**base, "fontName": FONT_B}, alignment=TA_CENTER, spaceAfter=21),
        "h1": ParagraphStyle("h1", **{**base, "fontName": FONT_B}, firstLineIndent=12.5 * mm, spaceAfter=14,
                             keepWithNext=1),
        "h2": ParagraphStyle("h2", **{**base, "fontName": FONT_B}, firstLineIndent=12.5 * mm, spaceBefore=10,
                             spaceAfter=7, keepWithNext=1),
        "tcap": ParagraphStyle("tcap", **base, alignment=TA_LEFT, spaceBefore=7, keepWithNext=1),
        "fcap": ParagraphStyle("fcap", **base, alignment=TA_CENTER, spaceBefore=4, spaceAfter=10),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=12, leading=14, textColor=BLACK),
        "cellc": ParagraphStyle("cellc", fontName=FONT, fontSize=12, leading=14, textColor=BLACK,
                                alignment=TA_CENTER),
        "cellh": ParagraphStyle("cellh", fontName=FONT, fontSize=12, leading=14, textColor=BLACK,
                                alignment=TA_CENTER),
        "title": ParagraphStyle("title", fontName=FONT_B, fontSize=16, leading=24, alignment=TA_CENTER,
                                textColor=BLACK),
        "toc0": ParagraphStyle("toc0", fontName=FONT, fontSize=14, leading=21, textColor=BLACK),
        "toc1": ParagraphStyle("toc1", fontName=FONT, fontSize=14, leading=21, leftIndent=7 * mm, textColor=BLACK),
    }
    return s


def _n(v) -> str:
    return f"{v:,}".replace(",", " ")


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f} %".replace(".", ",") if b else "—"


def _esc(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------- документ
class _Doc(BaseDocTemplate):
    def __init__(self, buf, info: dict, **kw):
        super().__init__(buf, pagesize=A4, leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP,
                         bottomMargin=BOTTOM, **kw)
        self.info = info
        frame = Frame(LEFT, BOTTOM, WIDTH, A4[1] - TOP - BOTTOM, id="f", leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate("title", [frame], onPage=self._count),
                               PageTemplate("body", [frame], onPage=self._number)])
        self._keys = 0

    def beforeDocument(self):
        self._keys = 0          # каждый проход вёрстки - те же ключи закладок, иначе содержание «не сходится»

    def _count(self, canv, doc):
        self.info["pages"] = max(self.info.get("pages", 0), doc.page)

    def _number(self, canv, doc):
        self._count(canv, doc)
        canv.setFont(FONT, 12)
        canv.setFillColor(BLACK)
        canv.drawCentredString(LEFT + WIDTH / 2, 10 * mm, str(doc.page))

    def afterFlowable(self, flowable):
        level = getattr(flowable, "toc_level", None)
        if level is None:
            return
        self._keys += 1
        key = f"h{self._keys}"
        self.canv.bookmarkPage(key)
        self.notify("TOCEntry", (level, flowable.toc_text, self.page, key))


class _Late(Flowable):
    """Абзац, текст которого известен только после вёрстки (число страниц в реферате)."""

    def __init__(self, make, style):
        super().__init__()
        self.make, self.style, self._p = make, style, None

    def wrap(self, aw, ah):
        self._p = Paragraph(self.make(), self.style)
        return self._p.wrap(aw, ah)

    def drawOn(self, canv, x, y, _sW=0):
        self._p.drawOn(canv, x, y, _sW)

    def split(self, aw, ah):
        return []


class Builder:
    def __init__(self, data: ProcessData, org: str, city: str):
        register_fonts()
        self.d, self.org, self.city = data, org, city
        self.st = _styles()
        self.story: list = []
        self.n_tab = self.n_fig = 0
        self.sec = 0
        self.sub = 0
        self.info = {"pages": 0}

    # ------------------------------------------------------------ примитивы
    def p(self, text: str, style: str = "body"):
        self.story.append(Paragraph(text, self.st[style]))

    def bullets(self, items: list[str]):
        # ГОСТ 7.32: перечисление через дефис с абзацного отступа
        for i, t in enumerate(items):
            end = "." if i == len(items) - 1 else ";"
            self.story.append(Paragraph(f"– {t}{end}", self.st["list"]))

    def struct(self, title: str, toc: bool = True):
        self.story.append(PageBreak())
        para = Paragraph(title.upper(), self.st["struct"])
        if toc:
            para.toc_level, para.toc_text = 0, title.upper()
        self.story.append(para)

    def h1(self, title: str):
        self.sec += 1
        self.sub = 0
        self.story.append(PageBreak())
        para = Paragraph(f"{self.sec} {title}", self.st["h1"])
        para.toc_level, para.toc_text = 0, f"{self.sec} {title}"
        self.story.append(para)

    def h2(self, title: str):
        self.sub += 1
        para = Paragraph(f"{self.sec}.{self.sub} {title}", self.st["h2"])
        para.toc_level, para.toc_text = 1, f"{self.sec}.{self.sub} {title}"
        self.story.append(para)

    def table(self, caption: str, head: list[str], rows: list[list], widths: list[float],
              align: list[str] | None = None) -> int:
        self.n_tab += 1
        align = align or ["l"] + ["c"] * (len(head) - 1)
        st = self.st
        data = [[Paragraph(_esc(h), st["cellh"]) for h in head]]
        for r in rows:
            data.append([Paragraph(_esc(v), st["cellc" if a == "c" else "cell"]) for v, a in zip(r, align)])
        total = sum(widths)
        t = Table(data, colWidths=[WIDTH * w / total for w in widths], repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, BLACK),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        cap = Paragraph(f"Таблица {self.n_tab} — {_esc(caption)}", st["tcap"])
        if len(rows) <= 12:
            self.story.append(KeepTogether([cap, t]))
        else:
            self.story += [CondPageBreak(60 * mm), cap, t]
        self.story.append(Spacer(1, 6))
        return self.n_tab

    def figure(self, drawing: Drawing, caption: str) -> int:
        self.n_fig += 1
        cap = Paragraph(f"Рисунок {self.n_fig} — {_esc(caption)}", self.st["fcap"])
        self.story.append(KeepTogether([Spacer(1, 4), drawing, cap]))
        return self.n_fig

    # ------------------------------------------------------------ рисунки
    def process_map(self) -> Drawing:
        d = self.d
        order = d.step_order()
        counts = d.step_counts()
        n = len(d.cases) or 1
        rows_tmp = {s: i for i, s in enumerate(order)}
        # на схеме - только заметные переходы (все переходы есть в таблице); дуг не больше 4 с каждой стороны
        trans, side = [], {"r": 0, "l": 0}
        for t in sorted(d.transitions(), key=lambda t: -t[2]):
            a, b, cnt = t[0], t[1], t[2]
            if cnt < max(3, MAP_MIN_SHARE * n):
                continue
            if b != NOT_STARTED and a in rows_tmp and b in rows_tmp and rows_tmp[b] != rows_tmp[a] + 1:
                k = "r" if rows_tmp[b] > rows_tmp[a] else "l"
                if side[k] >= 4:
                    continue
                side[k] += 1
            trans.append(t)
        bw, bh, gap = 56 * mm, 11 * mm, 10 * mm
        cx = 82 * mm
        rows = {s: i for i, s in enumerate(order)}
        height = len(order) * (bh + gap) + 6 * mm
        dr = Drawing(WIDTH, height)

        def top(i):
            return height - 3 * mm - i * (bh + gap)

        def box(x, y, w, label, cnt, dashed=False):
            r = Rect(x, y - bh, w, bh, fillColor=colors.white, strokeColor=BLACK, strokeWidth=1,
                     strokeDashArray=[3, 2] if dashed else None, rx=2 * mm, ry=2 * mm)
            dr.add(r)
            dr.add(String(x + w / 2, y - 4.6 * mm, label, fontName=FONT_B, fontSize=10,
                          textAnchor="middle", fillColor=BLACK))
            dr.add(String(x + w / 2, y - 8.8 * mm, f"{_n(cnt)} ЗН ({_pct(cnt, n)})", fontName=FONT,
                          fontSize=9, textAnchor="middle", fillColor=BLACK))

        for s, i in rows.items():
            box(cx - bw / 2, top(i), bw, s, counts.get(s, 0))
        if counts.get(NOT_STARTED):
            box(2 * mm, top(0), 40 * mm, NOT_STARTED, counts[NOT_STARTED], dashed=True)

        def arrow(x, y, dx, dy):
            import math  # noqa: PLC0415
            ln = math.hypot(dx, dy) or 1
            ux, uy = dx / ln, dy / ln
            s = 2.2 * mm
            dr.add(Polygon([x, y, x - ux * s - uy * s * 0.45, y - uy * s + ux * s * 0.45,
                            x - ux * s + uy * s * 0.45, y - uy * s - ux * s * 0.45],
                           fillColor=BLACK, strokeColor=BLACK, strokeWidth=0.3))

        def label(x, y, text, anchor="start"):
            w = stringWidth(text, FONT, 8)
            x0 = x if anchor == "start" else (x - w if anchor == "end" else x - w / 2)
            dr.add(Rect(x0 - 1, y - 2, w + 2, 9, fillColor=colors.white, strokeColor=None))
            dr.add(String(x, y, text, fontName=FONT, fontSize=8, textAnchor=anchor, fillColor=BLACK))

        right_k = left_k = 0
        for a, b, cnt, med in sorted(trans, key=lambda t: -t[2]):
            w = 0.6 + 2.2 * cnt / n
            txt = f"{_n(cnt)} · {fmt_hours(med)}" if med is not None else _n(cnt)
            if b == NOT_STARTED:
                if a not in rows:
                    continue
                y = top(rows[a]) - bh / 2
                if rows[a] == 0:          # из «Создан» - прямо влево
                    dr.add(Line(cx - bw / 2, y, 43 * mm, y, strokeColor=BLACK, strokeWidth=w,
                                strokeDashArray=[3, 2]))
                    arrow(42 * mm, y, -1, 0)
                else:                     # из нижнего шага - вверх к нижней стороне «Не начат»
                    xs = 22 * mm
                    dr.add(Line(cx - bw / 2, y, xs, y, strokeColor=BLACK, strokeWidth=w, strokeDashArray=[3, 2]))
                    dr.add(Line(xs, y, xs, top(0) - bh - 1, strokeColor=BLACK, strokeWidth=w,
                                strokeDashArray=[3, 2]))
                    arrow(xs, top(0) - bh, 0, 1)
                label(22 * mm, top(0) - bh - 5 * mm, txt, anchor="middle")
                continue
            if a not in rows or b not in rows:
                continue
            ia, ib = rows[a], rows[b]
            if ib == ia + 1:
                y1, y2 = top(ia) - bh, top(ib)
                dr.add(Line(cx, y1, cx, y2 + 1, strokeColor=BLACK, strokeWidth=w))
                arrow(cx, y2, 0, -1)
                label(cx + 2 * mm, (y1 + y2) / 2 - 1.5, txt)
            elif ib > ia:
                off = 12 * mm + right_k * 9 * mm
                right_k += 1
                x0 = cx + bw / 2
                y1, y2 = top(ia) - bh / 2, top(ib) - bh / 2
                pth = GPath(strokeColor=BLACK, strokeWidth=w, fillColor=None)
                pth.moveTo(x0, y1)
                pth.curveTo(x0 + off, y1, x0 + off, y2, x0 + 1, y2)
                dr.add(pth)
                arrow(x0, y2, -1, 0)
                label(x0 + off * 0.75 + 1, (y1 + y2) / 2, txt)
            else:
                off = 10 * mm + left_k * 9 * mm
                left_k += 1
                x0 = cx - bw / 2
                y1, y2 = top(ia) - bh / 2 - 2, top(ib) - bh / 2 + 2
                pth = GPath(strokeColor=BLACK, strokeWidth=w, fillColor=None, strokeDashArray=[3, 2])
                pth.moveTo(x0, y1)
                pth.curveTo(x0 - off, y1, x0 - off, y2, x0 - 1, y2)
                dr.add(pth)
                arrow(x0, y2, 1, 0)
                label(x0 - off * 0.75 - 1, (y1 + y2) / 2, txt, anchor="end")
        return dr

    def bars(self, labels: list[str], values: list[float], value_fmt, horizontal: bool,
             height: float = 70 * mm) -> Drawing:
        dr = Drawing(WIDTH, height)
        ch = HorizontalBarChart() if horizontal else VerticalBarChart()
        if horizontal:
            ch.x, ch.y, ch.width, ch.height = 78 * mm, 8 * mm, WIDTH - 98 * mm, height - 12 * mm
        else:
            ch.x, ch.y, ch.width, ch.height = 14 * mm, 12 * mm, WIDTH - 20 * mm, height - 18 * mm
        if horizontal:       # у горизонтальной диаграммы первая категория - внизу; переворачиваем
            labels, values = labels[::-1], values[::-1]
        ch.data = [values]
        ch.categoryAxis.categoryNames = labels
        ch.bars[0].fillColor = LIGHT
        ch.bars[0].strokeColor = BLACK
        ch.bars[0].strokeWidth = 0.5
        ax_lab = ch.categoryAxis.labels
        ax_lab.fontName, ax_lab.fontSize, ax_lab.fillColor = FONT, 10, BLACK
        if horizontal:
            ax_lab.textAnchor, ax_lab.boxAnchor = "end", "e"
            ax_lab.dx = -2
        ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = FONT, 9
        ch.valueAxis.labels.fillColor = BLACK
        ch.valueAxis.strokeColor = ch.categoryAxis.strokeColor = BLACK
        ch.valueAxis.valueMin = 0
        ch.valueAxis.visibleGrid = True
        ch.valueAxis.gridStrokeColor = LIGHT
        ch.barLabelFormat = value_fmt
        ch.barLabels.fontName, ch.barLabels.fontSize, ch.barLabels.fillColor = FONT, 9, BLACK
        ch.barLabels.nudge = 7 if not horizontal else 14
        dr.add(ch)
        return dr

    # ------------------------------------------------------------ разделы
    def build(self) -> bytes:
        d = self.d
        n = len(d.cases)
        st = self.st
        period = f"{d.d1:%d.%m.%Y} – {d.d2:%d.%m.%Y}"

        # ---- титульный лист
        self.story.append(Paragraph(_esc(self.org), st["center"]))
        self.story.append(Spacer(1, 70 * mm))
        self.story.append(Paragraph("ОТЧЁТ", st["title"]))
        self.story.append(Paragraph("об анализе бизнес-процесса обслуживания автомобилей<br/>"
                                    "по заказ-нарядам", st["title"]))
        self.story.append(Spacer(1, 10 * mm))
        self.story.append(Paragraph(f"по данным информационной базы «1С:Далион»<br/>за период {period}",
                                    st["center"]))
        self.story.append(Spacer(1, 75 * mm))
        self.story.append(Paragraph("Отчёт сформирован программой «АвтоОтчёт» "
                                    f"{d.on:%d.%m.%Y}", st["center"]))
        self.story.append(Spacer(1, 8 * mm))
        self.story.append(Paragraph(f"{_esc(self.city)} {d.on:%Y}", st["center"]))
        self.story.append(NextPageTemplate("body"))

        # ---- реферат
        self.struct("Реферат", toc=False)

        def referat():
            return (f"Отчёт {self.info['pages'] or '__'} с., {self.n_fig} рис., {self.n_tab} табл., "
                    "3 источн., 1 прил.")
        self.story.append(_Late(referat, st["body"]))
        self.p("ЗАКАЗ-НАРЯД, БИЗНЕС-ПРОЦЕСС, ЖУРНАЛ СОБЫТИЙ, ДЛИТЕЛЬНОСТЬ ЭТАПОВ, ПЛАНОВЫЕ СРОКИ, "
               "УЗКИЕ МЕСТА, НАГРУЗКА, АВТОСЕРВИС")
        self.p(f"Объект исследования — процесс обслуживания автомобилей по заказ-нарядам в "
               f"{_esc(self.org)}.")
        self.p("Цель работы — по данным учётной системы, без опроса сотрудников, восстановить "
               "фактическую схему процесса, измерить длительность этапов и найти узкие места.")
        self.p("Метод — анализ процессов по журналу событий (process mining): каждый заказ-наряд "
               "рассматривается как отдельный случай, шаги процесса и их время берутся из реквизитов "
               "заказ-наряда и связанных с ним документов.")
        self.p(f"Проанализировано {_n(n)} заказ-нарядов за период {period}. Результаты: схема процесса с "
               "частотами и временем переходов, длительности этапов, соблюдение плановых сроков, "
               "перечень зависших заказ-нарядов, распределение нагрузки и показатели качества.")

        # ---- содержание
        self.struct("Содержание", toc=False)
        toc = TableOfContents(dotsMinLevel=0)
        toc.levelStyles = [st["toc0"], st["toc1"]]
        self.story.append(toc)

        # ---- термины
        self.struct("Термины, определения и сокращения")
        for term, text in (
            ("ЗН", "заказ-наряд — документ «1С:Далион» на обслуживание одного автомобиля"),
            ("Случай", "один заказ-наряд со всеми его шагами"),
            ("Шаг (событие)", "отметка о действии с заказ-нарядом и время этого действия"),
            ("Вариант процесса", "последовательность шагов, по которой прошёл заказ-наряд"),
            ("Переход", "непосредственное следование одного шага за другим в варианте процесса"),
            ("Медиана", "значение, меньше которого половина наблюдений; в отличие от среднего "
                        "не искажается единичными очень долгими случаями"),
            ("90-й процентиль", "значение, которое не превышают 90 % наблюдений"),
        ):
            self.p(f"{term} — {text}.")

        # ---- введение
        self.struct("Введение")
        self.p("Сотрудники отмечают каждый шаг работы с автомобилем в «1С:Далион»: создают "
               "заказ-наряд, переводят его в работу, получают и выдают запчасти, завершают работы, "
               "принимают оплату и выдают автомобиль. Каждая такая отметка сохраняется с датой, "
               "временем и пользователем. Вместе они образуют журнал событий, по которому можно "
               "восстановить, как процесс идёт на самом деле, а не как он описан.")
        self.p("Исходные данные прочитаны из информационной базы только на чтение; в базу ничего не "
               "записывалось. Использованы реквизиты заказ-наряда (дата создания, дата перевода в работу, "
               "дата завершения, дата отгрузки и пользователи, выполнившие эти действия, плановые даты "
               "начала и завершения, статус, тип, пост, мастер-приёмщик, отметка «Повтор», оценка, "
               "причина отказа) и связанные документы: приходные накладные, выданные под заказ-наряд, "
               "внутренние накладные на выдачу запчастей, документы оплаты и рекомендации.")
        self.p("Ограничения метода:")
        self.bullets([
            "действия, которые не отмечаются в учётной системе (звонки, ожидание клиента, перегон "
            "автомобиля), в журнале событий не видны",
            "время шага — это время его отметки в системе; если документ оформлен позже, чем "
            "произошло событие, длительность этапа будет искажена",
            "при работе нескольких сотрудников под одной учётной записью действия не различаются "
            "по исполнителям",
        ])
        if d.notes:
            self.p("Не удалось прочитать: " + "; ".join(_esc(x) for x in d.notes) + ".")

        # ---- 1 исходные данные
        self.h1("Исходные данные")
        self.p(f"За период {period} в базе {_n(n)} заказ-нарядов (без помеченных на удаление). "
               f"Распределение по типам и статусам приведено в таблицах {self.n_tab + 1} и {self.n_tab + 2}.")
        self.table("Заказ-наряды по типам", ["Тип ЗН", "Количество", "Доля"],
                   [[k, _n(v), _pct(v, n)] for k, v in d.by(lambda c: c.kind)], [3, 1.2, 1])
        self.table("Заказ-наряды по статусам на дату отчёта", ["Статус", "Количество", "Доля"],
                   [[k, _n(v), _pct(v, n)] for k, v in d.by(lambda c: c.status)], [3, 1.2, 1])
        self.p(f"Полнота данных — сколько заказ-нарядов имеют отметку каждого шага — приведена в "
               f"таблице {self.n_tab + 1}.")
        self.table("Наличие шагов в заказ-нарядах", ["Шаг", "Есть в ЗН", "Доля"],
                   [[s, _n(v), _pct(v, n)] for s, v in d.filled()], [3, 1.2, 1])

        # ---- 2 модель процесса
        self.h1("Фактическая схема процесса")
        self.p(f"Схема процесса, восстановленная по журналу событий, показана на рисунке {self.n_fig + 1}. "
               "Прямоугольники — шаги процесса с числом заказ-нарядов, прошедших через шаг. Стрелки — "
               "переходы: число заказ-нарядов и медиана времени перехода. Сплошные стрелки справа — "
               "переходы с пропуском шагов, пунктирные слева — переходы в обратном порядке (например, "
               "запчасти выданы раньше перевода в работу). Чтобы схема читалась, на ней показаны переходы, "
               "которые есть не меньше чем у 5 % заказ-нарядов; все переходы приведены в таблице "
               f"{self.n_tab + 2}.")
        self.figure(self.process_map(), "Фактическая схема процесса обслуживания по заказ-наряду")
        vc = d.variant_count()
        top = d.variants(10)
        covered = sum(v for _, v in top)
        self.p(f"Заказ-наряды прошли по {_n(vc)} различным вариантам процесса. Десять самых частых "
               f"вариантов охватывают {_pct(covered, n)} заказ-нарядов (таблица {self.n_tab + 1}).")
        self.table("Самые частые варианты процесса", ["№", "Последовательность шагов", "ЗН", "Доля"],
                   [[str(i + 1), " → ".join(v), _n(c), _pct(c, n)] for i, (v, c) in enumerate(top)],
                   [0.4, 5, 0.8, 0.9], ["c", "l", "c", "c"])
        trans = d.transitions()
        self.p(f"Все переходы между шагами с медианным временем перехода — в таблице {self.n_tab + 1}.")
        self.table("Переходы между шагами", ["Из шага", "В шаг", "ЗН", "Медиана"],
                   [[a, b, _n(c), fmt_hours(m)] for a, b, c, m in sorted(trans, key=lambda t: -t[2])],
                   [2.2, 2.2, 0.8, 1.1], ["l", "l", "c", "c"])

        # ---- 3 длительности
        self.h1("Длительность этапов")
        rows, chart_l, chart_v = [], [], []
        bad_total = 0
        for title, a, b in STAGES:
            s, bad = d.stage(a, b)
            bad_total += bad
            rows.append([title, _n(s.n) if s else "0", fmt_hours(s.median if s else None),
                         fmt_hours(s.mean if s else None), fmt_hours(s.p90 if s else None)])
            if s and title != STAGES[3][0]:
                chart_l.append(title.split(" (")[0])
                chart_v.append(round(s.median, 2))
        self.p("Длительность этапа — время между двумя шагами одного заказ-наряда. Для каждого этапа "
               "приведены число заказ-нарядов, медиана, среднее и 90-й процентиль (таблица "
               f"{self.n_tab + 1}). Среднее больше медианы, когда есть отдельные очень долгие случаи.")
        self.table("Длительность этапов, все заказ-наряды", ["Этап", "ЗН", "Медиана", "Среднее", "90 %"],
                   rows, [3.2, 0.8, 1, 1, 1])
        if bad_total:
            self.p(f"Не учтено {_n(bad_total)} значений, где второй шаг отмечен раньше первого — это "
                   "ошибки или исправления в учёте.")
        if chart_v:
            self.figure(self.bars(chart_l, chart_v, lambda v: fmt_hours(v), True, 18 * mm + 13 * mm * len(chart_v)),
                        "Медианная длительность этапов, ч")
        kinds = d.main_kinds()
        if len(kinds) > 1:
            self.h2("Длительность этапов по типам заказ-нарядов")
            head = ["Этап"] + kinds
            body = []
            for title, a, b in STAGES:
                row = [title]
                for k in kinds:
                    s, _ = d.stage(a, b, k)
                    row.append(fmt_hours(s.median if s else None))
                body.append(row)
            self.p(f"Медианы этапов по основным типам заказ-нарядов — в таблице {self.n_tab + 1}.")
            self.table("Медиана длительности этапов по типам ЗН", head, body, [3.2] + [1.2] * len(kinds))
        before, same, after = d.payment_vs_shipping()
        tot = before + same + after
        if tot:
            self.h2("Оплата относительно выдачи автомобиля")
            self.p(f"Из {_n(tot)} заказ-нарядов, где есть и оплата, и выдача: оплачено до выдачи — "
                   f"{_n(before)} ({_pct(before, tot)}), в момент выдачи (±1 ч) — {_n(same)} "
                   f"({_pct(same, tot)}), после выдачи — {_n(after)} ({_pct(after, tot)}).")

        # ---- 4 план / факт
        self.h1("Соблюдение плановых сроков")
        rows = []
        for title, which in (("Начало работ", "start"), ("Завершение работ", "end")):
            s, ok, total = d.plan_fact(which)
            rows.append([title, _n(total), f"{_n(ok)} ({_pct(ok, total)})",
                         fmt_hours(s.median if s else None), fmt_hours(s.p90 if s else None)])
        self.p("В заказ-наряде указываются плановые даты начала и завершения работ. Фактические даты "
               "сравниваются с плановыми: «в срок» — не позже плана более чем на 1 час; опоздание "
               f"считается только в сторону задержки (таблица {self.n_tab + 1}).")
        self.table("Плановые и фактические сроки", ["Событие", "ЗН", "В срок", "Медиана опоздания", "90 %"],
                   rows, [2, 0.8, 1.5, 1.4, 1])

        # ---- 5 зависшие
        self.h1("Узкие места и зависшие заказ-наряды")
        stuck = d.stuck()
        groups: dict[str, list] = {}
        for c, where, since, days in stuck:
            groups.setdefault(where, []).append(days)
        self.p(f"Зависшим считается заказ-наряд, который на {d.on:%d.%m.%Y} находится на одном шаге "
               f"{d.stuck_days} дней и дольше и не отгружен (не закрыт). Таких заказ-нарядов "
               f"{_n(len(stuck))}; сводка — в таблице {self.n_tab + 1}, полный перечень — в приложении А.")
        if groups:
            self.table("Зависшие заказ-наряды по шагам", ["Где висит", "ЗН", "Медиана, дн.", "Максимум, дн."],
                       [[k, _n(len(v)), _n(sorted(v)[len(v) // 2]), _n(max(v))] for k, v in groups.items()],
                       [2.6, 0.8, 1.2, 1.2])
        worst = max(((t, a, b, d.stage(a, b)[0]) for t, a, b in STAGES[:3]),
                    key=lambda x: x[3].median if x[3] else -1)
        if worst[3]:
            self.p(f"Самый долгий по медиане этап — «{worst[0].lower()}»: {fmt_hours(worst[3].median)}, "
                   f"у 10 % заказ-нарядов — {fmt_hours(worst[3].p90)} и дольше.")

        # ---- 6 нагрузка
        self.h1("Нагрузка")
        self.h2("По дням недели и часам")
        wd = d.weekday_load()
        self.p(f"Распределение созданных заказ-нарядов по дням недели — на рисунке {self.n_fig + 1}, "
               f"по часам — на рисунке {self.n_fig + 2}.")
        self.figure(self.bars(WEEKDAYS, wd, lambda v: _n(int(v)), False), "Созданные заказ-наряды по дням недели")
        hl = d.hour_load()
        if hl:
            self.figure(self.bars([f"{h}" for h in hl], list(hl.values()), lambda v: _n(int(v)), False),
                        "Созданные заказ-наряды по часам суток")
        self.h2("По сотрудникам")
        self.p("Сколько раз каждый пользователь «1С:Далион» выполнял шаг процесса — в таблице "
               f"{self.n_tab + 1}. Если сотрудники работают под общей учётной записью, их действия "
               "здесь объединены.")
        people = sorted({p for s in (CREATED, WORK, DONE, SHIPPED) for p, _ in d.who(s)})
        cnt = {s: dict(d.who(s)) for s in (CREATED, WORK, DONE, SHIPPED)}
        rows = [[p] + [_n(cnt[s].get(p, 0)) for s in (CREATED, WORK, DONE, SHIPPED)] for p in people]
        rows.sort(key=lambda r: -sum(int(x.replace(" ", "")) for x in r[1:]))
        self.table("Шаги процесса по пользователям", ["Пользователь", "Создал", "Перевёл в работу",
                                                      "Завершил", "Выдал"], rows, [2.4, 1, 1.2, 1, 1])
        posts = [p for p in d.posts() if p[0] != "(не указан)" or len(d.posts()) == 1]
        if posts:
            self.h2("По постам")
            self.p(f"Число заказ-нарядов и медиана времени ремонта по постам — в таблице {self.n_tab + 1}.")
            self.table("Заказ-наряды по постам", ["Пост", "ЗН", "Медиана ремонта"],
                       [[p, _n(c), fmt_hours(s.median if s else None)] for p, c, s in posts], [3, 1, 1.4])

        # ---- 7 качество и продажи
        self.h1("Рекомендации, повторные обращения и отказы")
        with_rec, recs, from_rec = d.recommendations()
        rep = d.repeats()
        rn, ravg = d.ratings()
        self.table("Показатели качества и продаж", ["Показатель", "Значение"], [
            ["ЗН, по которым выданы рекомендации", f"{_n(with_rec)} ({_pct(with_rec, n)})"],
            ["Всего рекомендаций", _n(recs)],
            ["ЗН, созданные по рекомендации", _n(from_rec)],
            ["Рекомендаций, перешедших в ЗН", _pct(from_rec, recs) if recs else "—"],
            ["ЗН с отметкой «Повтор»", f"{_n(rep)} ({_pct(rep, n)})"],
            ["ЗН с оценкой клиента", _n(rn)],
            ["Средняя оценка", f"{ravg:.2f}".replace(".", ",") if ravg else "—"],
        ], [3.5, 1.5])
        ref = d.refusals()
        if ref:
            total_ref = sum(v for _, v in ref)
            self.p(f"Заказ-нарядов, оставшихся предварительными (работы не начаты): {_n(total_ref)} "
                   f"({_pct(total_ref, n)}). Причины — в таблице {self.n_tab + 1}.")
            self.table("Причины, по которым работы не начаты", ["Причина", "ЗН", "Доля"],
                       [[k, _n(v), _pct(v, total_ref)] for k, v in ref], [3, 1, 1])

        # ---- заключение
        self.struct("Заключение")
        self.p("По результатам анализа:")
        self.bullets(self._findings(stuck, worst))
        self.p("Предлагается:")
        self.bullets(self._proposals(stuck))

        # ---- источники
        self.struct("Список использованных источников")
        for i, src in enumerate([
            "ГОСТ 7.32–2017. Система стандартов по информации, библиотечному и издательскому делу. Отчёт "
            "о научно-исследовательской работе. Структура и правила оформления. — М.: Стандартинформ, 2017.",
            "Aalst W. M. P. van der. Process Mining: Data Science in Action. — 2nd ed. — Berlin: Springer, "
            "2016. — 467 p.",
            f"Информационная база «1С:Далион: Управление автосервисом» {_esc(self.org)}: документы "
            f"«Заказ-наряд» и связанные с ними за период {period}.",
        ], 1):
            self.p(f"{i} {src}")

        # ---- приложение А
        self.story.append(PageBreak())
        app_title = Paragraph("ПРИЛОЖЕНИЕ А<br/>(справочное)<br/>Перечень зависших заказ-нарядов", st["struct"])
        app_title.toc_level, app_title.toc_text = 0, "ПРИЛОЖЕНИЕ А Перечень зависших заказ-нарядов"
        self.story.append(app_title)
        if stuck:
            self.table(f"Заказ-наряды, висящие на шаге {d.stuck_days} дней и дольше на {d.on:%d.%m.%Y}",
                       ["Номер ЗН", "Тип", "Где висит", "С какого числа", "Дней", "Мастер-приёмщик"],
                       [[c.number, c.kind, w, f"{s:%d.%m.%Y}", _n(days), c.master] for c, w, s, days in stuck],
                       [1.4, 1.3, 2.1, 1.2, 0.7, 1.6], ["l", "l", "l", "c", "c", "l"])
        else:
            self.p("Зависших заказ-нарядов нет.")

        buf = BytesIO()
        doc = _Doc(buf, self.info, title="Анализ бизнес-процесса", author="АвтоОтчёт")
        # реферат ссылается на итоговое число страниц, содержание - на номера: вёрстка в несколько проходов
        doc.multiBuild(self.story, maxPasses=6)
        return buf.getvalue()

    # ------------------------------------------------------------ выводы
    def _findings(self, stuck, worst) -> list[str]:
        d = self.d
        n = len(d.cases)
        out = [f"за период {d.d1:%d.%m.%Y} – {d.d2:%d.%m.%Y} обработано {_n(n)} заказ-нарядов, они прошли по "
               f"{_n(d.variant_count())} вариантам процесса"]
        top = d.variants(1)
        if top:
            v, c = top[0]
            out.append(f"самый частый вариант — «{' → '.join(v)}» ({_pct(c, n)} заказ-нарядов)")
        if worst[3]:
            out.append(f"самый долгий этап по медиане — «{worst[0].lower()}» ({fmt_hours(worst[3].median)})")
        s, _ = d.stage(DONE, SHIPPED)
        if s:
            long = sum(1 for c in d.cases if (h := _gap(c, DONE, SHIPPED)) is not None and h > 24)
            out.append(f"после завершения работ автомобиль выдаётся в среднем (медиана) через "
                       f"{fmt_hours(s.median)}; дольше суток ждали выдачи {_n(long)} заказ-нарядов")
        for title, which in (("начала", "start"), ("завершения", "end")):
            st, ok, total = d.plan_fact(which)
            if total:
                out.append(f"плановую дату {title} работ соблюдают в {_pct(ok, total)} заказ-нарядов")
        out.append(f"на {d.on:%d.%m.%Y} зависших заказ-нарядов ({d.stuck_days}+ дней на одном шаге) — "
                   f"{_n(len(stuck))}")
        wd = d.weekday_load()
        if sum(wd):
            out.append(f"больше всего заказ-нарядов создаётся в {_day_name(wd.index(max(wd)))} "
                       f"({_n(max(wd))} за период)")
        with_rec, recs, from_rec = d.recommendations()
        if recs:
            out.append(f"из {_n(recs)} рекомендаций в новые заказ-наряды перешло {_n(from_rec)} "
                       f"({_pct(from_rec, recs)})")
        return out

    def _proposals(self, stuck) -> list[str]:
        d = self.d
        out = []
        if stuck:
            out.append("еженедельно разбирать перечень зависших заказ-нарядов (приложение А): закрыть "
                       "выполненные, отменить неактуальные предварительные")
        st, ok, total = d.plan_fact("end")
        if total and ok / total < 0.7:
            out.append("ставить плановую дату завершения с учётом фактической длительности ремонта "
                       "(раздел 3), чтобы клиенту называли реальный срок")
        s, _ = d.stage(DONE, SHIPPED)
        if s and s.median > 4:
            out.append("сообщать клиенту о готовности автомобиля сразу после завершения работ — это "
                       "сократит этап ожидания выдачи")
        if d.refusals():
            out.append("заполнять причину отказа во всех предварительных заказ-нарядах, которые не "
                       "перешли в работу")
        out.append("каждому сотруднику работать в «1С:Далион» под своей учётной записью, чтобы нагрузку "
                   "и сроки можно было оценивать по исполнителям")
        out.append("повторять анализ раз в квартал и сравнивать показатели с этим отчётом")
        return out


def _gap(c, a, b):
    ta, tb = c.at(a), c.at(b)
    return (tb - ta).total_seconds() / 3600 if ta and tb else None


def _day_name(i: int) -> str:
    return ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"][i]


def build_process_pdf(data: ProcessData, org: str = "Авто Центр Минеево", city: str = "Иваново") -> bytes:
    return Builder(data, org, city).build()


def process_filename(data: ProcessData) -> str:
    return f"Анализ процессов {data.d1:%d.%m.%Y}-{data.d2:%d.%m.%Y}.pdf"


__all__ = ["build_process_pdf", "process_filename", "register_fonts", "PAID", "WORK"]
