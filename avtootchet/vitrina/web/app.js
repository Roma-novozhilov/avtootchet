"use strict";
// Витрина ACMineevo: рисует то, что посчитал сервер (avtootchet/vitrina/metrics.py).

const $ = (s) => document.querySelector(s);
const state = { tab: "home", from: "", to: "", user: "", line: "", role: "", meta: null };
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const nf = new Intl.NumberFormat("ru-RU");
const mf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const num = (v) => (v === null || v === undefined || v === "" ? "—" : nf.format(v));
const money = (v) => (v === null || v === undefined || v === "" ? "—" : mf.format(Math.round(v)) + " ₽");
const shortMoney = (v) => (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1).replace(".", ",") + " млн" : Math.abs(v) >= 1e3 ? Math.round(v / 1e3) + " тыс" : String(Math.round(v)));
const secs = (v) => (v === null || v === undefined ? "—" : v >= 60 ? `${Math.floor(v / 60)} мин ${Math.round(v % 60)} с` : `${Math.round(v)} с`);
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const ddmm = (s) => `${s.slice(8, 10)}.${s.slice(5, 7)}`;
const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

// ------------------------------------------------------------------ сеть
async function api(path, params = {}) {
  const q = new URLSearchParams({ from: state.from, to: state.to, user: state.user, line: state.line, ...params });
  const r = await fetch(`${path}?${q}`, { credentials: "same-origin" });
  if (r.status === 401) { showLogin(); throw new Error("login"); }
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `Ошибка ${r.status}`);
  return data;
}

function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#password").focus(); }

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("#password").value }), credentials: "same-origin" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { $("#login-error").textContent = data.error || "Не удалось войти"; return; }
  $("#password").value = "";
  start();
});
$("#logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  showLogin();
});

// ------------------------------------------------------------------ фильтры и адрес
function setPreset(p) {
  const today = new Date(); let a, b = today;
  if (p === "month") a = new Date(today.getFullYear(), today.getMonth(), 1);
  else if (p === "prev") { a = new Date(today.getFullYear(), today.getMonth() - 1, 1); b = new Date(today.getFullYear(), today.getMonth(), 0); }
  else { a = new Date(today); a.setDate(a.getDate() - (Number(p) - 1)); }
  state.from = iso(a); state.to = iso(b); state.preset = p;
}
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  for (const k of ["tab", "from", "to", "user", "line", "preset"]) if (h.get(k) !== null) state[k] = h.get(k);
}
function writeHash() {
  const h = new URLSearchParams();
  for (const k of ["tab", "from", "to", "user", "line", "preset"]) if (state[k]) h.set(k, state[k]);
  history.replaceState(null, "", "#" + h);
}
function syncControls() {
  $("#from").value = state.from; $("#to").value = state.to; $("#user").value = state.user; $("#line").value = state.line;
  document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.dataset.preset === state.preset));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === state.tab));
}
document.querySelectorAll("#presets button").forEach((b) => b.addEventListener("click", () => { setPreset(b.dataset.preset); refresh(); }));
for (const id of ["from", "to"]) $("#" + id).addEventListener("change", (e) => { state[id] = e.target.value; state.preset = ""; refresh(); });
for (const id of ["user", "line"]) $("#" + id).addEventListener("change", (e) => { state[id] = e.target.value; refresh(); });
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.tab; refresh(); }));

function fillSelect(sel, values) {
  const cur = sel.value;
  sel.innerHTML = '<option value="">все</option>' + values.map((v) => `<option>${esc(v)}</option>`).join("");
  sel.value = values.includes(cur) ? cur : "";
}

// ------------------------------------------------------------------ подсказка
const tip = $("#tip");
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(x + 14, innerWidth - w - 8) + "px";
  tip.style.top = Math.max(8, y - h - 12) + "px";
}
function hideTip() { tip.hidden = true; }
document.addEventListener("mouseover", (e) => {
  const t = e.target.closest("[data-tip]");
  if (t) { const r = t.getBoundingClientRect(); showTip(t.dataset.tip, r.left + r.width / 2, r.top); }
});
document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tip]")) hideTip(); });

// ------------------------------------------------------------------ графики
const CHARTS = new Map();
function lineChart(days, series, opts = {}) {
  // series: [{name, values, color}]; одна ось Y. Рисуется после вставки - по ширине карточки.
  const id = "c" + Math.random().toString(36).slice(2);
  CHARTS.set(id, { days, series, opts });
  const legend = series.length > 1 ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
  return `${legend}<div class="lc" id="${id}"></div>`;
}
function drawCharts() {
  for (const [id, spec] of CHARTS) {
    const box = document.getElementById(id);
    if (!box) { CHARTS.delete(id); continue; }
    drawLine(box, spec);
  }
}
addEventListener("resize", () => { clearTimeout(drawCharts.t); drawCharts.t = setTimeout(drawCharts, 150); });
function drawLine(box, { days, series, opts }) {
  const W = Math.max(260, box.clientWidth), H = W < 420 ? 190 : 220, L = opts.money ? 54 : 38, R = 10, T = 10, B = 24;
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const step = niceStep(max / 4), top = Math.ceil(max / step) * step;
  const x = (i) => L + (days.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (days.length - 1));
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const fmt = opts.money ? shortMoney : (v) => nf.format(v);
  let g = "";
  for (let v = 0; v <= top + 1e-9; v += step) g += `<line class="gridline" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;
  const every = Math.max(1, Math.ceil(days.length / Math.max(3, Math.floor(W / 70))));
  days.forEach((d, i) => { if (i % every === 0) g += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${ddmm(d)}</text>`; });
  const paths = series.map((s) => `<polyline fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"/>`).join("");
  box.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "")}">${g}<line class="axis" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>${paths}<line class="xhair" y1="${T}" y2="${H - B}" x1="0" x2="0" visibility="hidden"/><g class="dots"></g><rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
  const svg = box.firstChild, line = svg.querySelector(".xhair"), dots = svg.querySelector(".dots");
  svg.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), px = e.clientX - r.left;
    const i = Math.max(0, Math.min(days.length - 1, Math.round(((px - L) / (W - L - R)) * (days.length - 1))));
    line.setAttribute("x1", x(i)); line.setAttribute("x2", x(i)); line.setAttribute("visibility", "visible");
    dots.innerHTML = series.map((s) => `<circle cx="${x(i)}" cy="${y(s.values[i])}" r="4.5" fill="${s.color}" stroke="var(--surface-1)" stroke-width="2"/>`).join("");
    const d = days[i], wd = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"][new Date(d).getDay()];
    showTip(`<b>${wd}, ${ddmm(d)}.${d.slice(0, 4)}</b>` + series.map((s) => `<div class="row"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><b>${opts.money ? money(s.values[i]) : num(s.values[i])}</b></div>`).join(""), e.clientX, e.clientY);
  });
  svg.addEventListener("mouseleave", () => { hideTip(); line.setAttribute("visibility", "hidden"); dots.innerHTML = ""; });
}
function niceStep(raw) {
  if (raw <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
function bars(items, opts = {}) {
  // items: [{label, value, bad, tip}]
  const max = Math.max(1, ...items.map((i) => Math.abs(i.value || 0)));
  const fmt = opts.money ? money : opts.fmt || num;
  return `<div class="bars">${items.map((i) => `<div class="bar${i.bad ? " bad" : ""}" data-tip="${esc(i.tip || `${esc(i.label)}: <b>${fmt(i.value)}</b>`)}"><span class="lbl">${i.bad ? "⚠ " : ""}${esc(i.label)}</span><span class="track"><span class="fill" style="width:${(100 * Math.abs(i.value || 0)) / max}%"></span></span><span class="val">${fmt(i.value)}${i.extra ? ` <span class="muted">${esc(i.extra)}</span>` : ""}</span></div>`).join("")}</div>`;
}
function heatmap(t, key) {
  const m = t[key], max = Math.max(1, ...m.flat());
  const lvl = (v) => (v === 0 ? 0 : 1 + Math.min(6, Math.floor((v / max) * 6.999)));
  let h = '<div class="heat"><span></span>';
  for (let i = 0; i < 24; i++) h += `<span class="h">${i % 3 === 0 ? i : ""}</span>`;
  t.weekdays.forEach((w, wi) => {
    h += `<span class="w">${w}</span>`;
    for (let i = 0; i < 24; i++) {
      const v = m[wi][i], miss = t.heat_missed[wi][i], all = t.heat[wi][i];
      const tipText = `<b>${w}, ${i}:00–${i + 1}:00</b><div class="row"><span>входящих</span><b>${all}</b></div><div class="row"><span>пропущено</span><b>${miss}${all ? ` (${Math.round((100 * miss) / all)} %)` : ""}</b></div>`;
      h += `<span class="c" style="background:var(--seq-${lvl(v)})" data-tip="${esc(tipText)}"></span>`;
    }
  });
  h += "</div><div class=\"heat-legend\">меньше " + [1, 2, 3, 4, 5, 6, 7].map((i) => `<i style="background:var(--seq-${i})"></i>`).join("") + " больше</div>";
  return h;
}

// ------------------------------------------------------------------ таблицы
function table(cols, rows, opts = {}) {
  // cols: [{key, title, num, fmt}]
  const id = "t" + Math.random().toString(36).slice(2);
  setTimeout(() => {
    const el = document.getElementById(id); if (!el) return;
    el.querySelectorAll("th").forEach((th, ci) => th.addEventListener("click", () => {
      const k = cols[ci].key, dir = th.dataset.dir === "asc" ? "desc" : "asc";
      rows.sort((a, b) => { const x = a[k] ?? "", y = b[k] ?? ""; const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "ru"); return dir === "asc" ? c : -c; });
      el.closest(".tbl-wrap").outerHTML = table(cols, rows, opts);
    }));
    if (opts.onRow) el.querySelectorAll("tr[data-i]").forEach((tr) => tr.addEventListener("click", () => opts.onRow(rows[Number(tr.dataset.i)])));
  }, 0);
  const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.title)}</th>`).join("");
  const body = rows.map((r, i) => `<tr data-i="${i}" class="${opts.onRow ? "click" : ""}">${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.html(r) : esc(c.fmt ? c.fmt(r[c.key], r) : r[c.key] ?? "")}</td>`).join("")}</tr>`).join("");
  return `<div class="tbl-wrap"><table id="${id}"><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}" class="muted">Нет данных за период</td></tr>`}</tbody></table></div>`;
}
const CALL_COLS = [
  { key: "time", title: "Когда" }, { key: "line", title: "Линия" }, { key: "dir", title: "Тип" },
  { key: "client", title: "Клиент", fmt: (v) => v || "— нет карточки —" }, { key: "phone", title: "Номер" },
  { key: "dur", title: "Длит., с", num: true }, { key: "user", title: "Менеджер" },
  { key: "callback", title: "Перезвон", html: (r) => (r.callback === "нет" ? '<span class="tag bad">не перезвонили</span>' : r.callback === "да" ? `через ${num(Math.round(r.callback_min))} мин` : "") },
];
const orderCols = () => [
  { key: "number", title: "№ ЗН" }, { key: "created", title: "Создан" }, { key: "status", title: "Статус" },
  { key: "client", title: "Клиент" }, { key: "car", title: "Автомобиль" }, { key: "author", title: "Создал" },
  { key: "master", title: "Мастер" }, { key: "source", title: "Источник", fmt: (v) => v || "—" }, { key: "issued", title: "Выдан" },
  ...(state.role === "owner" ? [{ key: "amount", title: "Сумма", num: true, fmt: money }] : []),
];
const UNPAID_EXTRA = [{ key: "paid", title: "Оплачено", num: true, fmt: money }, { key: "debt", title: "Долг", num: true, fmt: money }];

// ------------------------------------------------------------------ боковая панель (списки и путь клиента)
const drawer = { stack: [], rows: [], cols: [], title: "" };
$("#drawer-close").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("#drawer-back").addEventListener("click", () => { drawer.stack.pop(); const prev = drawer.stack.pop(); if (prev) prev(); });
$("#drawer-search").addEventListener("input", renderDrawerList);
$("#drawer-csv").addEventListener("click", () => {
  const q = $("#drawer-search").value.toLowerCase();
  const rows = drawer.rows.filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q));
  const lines = [drawer.cols.map((c) => c.title).join(";")].concat(rows.map((r) => drawer.cols.map((c) => `"${String(r[c.key] ?? "").replace(/"/g, '""')}"`).join(";")));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${drawer.title}.csv`; a.click();
});
function closeDrawer() { $("#drawer").hidden = true; drawer.stack = []; hideTip(); }
function openDrawer(title, fn) {
  drawer.stack.push(fn); $("#drawer").hidden = false; $("#drawer-title").textContent = title;
  $("#drawer-back").hidden = drawer.stack.length < 2; $("#drawer-body").innerHTML = '<p class="muted">Загружаю…</p>';
}
async function openList(key) {
  const fn = () => openList(key);
  openDrawer("…", fn);
  try {
    const d = await api("/api/list", { key });
    $("#drawer-title").textContent = `${d.title} — ${num(d.rows.length)}`;
    drawer.title = d.title; drawer.rows = d.rows; $("#drawer-tools").hidden = false; $("#drawer-search").value = "";
    if (d.kind === "calls") drawer.cols = CALL_COLS;
    else if (d.kind === "orders") drawer.cols = orderCols().concat(key === "unpaid" ? UNPAID_EXTRA : []);
    else if (d.kind === "tasks") drawer.cols = [{ key: "created", title: "Создана" }, { key: "type", title: "Тип" }, { key: "executor", title: "Исполнитель" }, { key: "days", title: "Дней открыта", num: true }];
    else drawer.cols = [{ key: "phone", title: "Номер" }, { key: "client", title: "Клиент", fmt: (v) => v || "— нет карточки —" }, { key: "calls", title: "Звонков", num: true }, { key: "missed", title: "Из них пропущено", num: true }, { key: "first", title: "Первый" }, { key: "last", title: "Последний" }];
    drawer.kind = d.kind;
    renderDrawerList();
  } catch (e) { if (e.message !== "login") $("#drawer-body").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}
function renderDrawerList() {
  const q = $("#drawer-search").value.toLowerCase();
  const rows = drawer.rows.filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q));
  const clickable = drawer.kind !== "tasks";
  $("#drawer-body").innerHTML = (drawer.kind !== "tasks" ? '<p class="muted">Нажмите на строку — откроется история клиента: звонки, заказ-наряды, оплаты.</p>' : "") +
    table(drawer.cols, rows.slice(0, 1000), clickable ? { onRow: (r) => openClient(r.client_code, r.id) } : {}) +
    (rows.length > 1000 ? `<p class="muted">Показаны первые 1000 из ${num(rows.length)} — уточните поиск или скачайте CSV.</p>` : "");
}
async function openClient(code, call) {
  if (!code && !call) return;
  openDrawer("История клиента", () => openClient(code, call));
  $("#drawer-tools").hidden = true;
  try {
    const d = await api("/api/client", { code: code || "", call: call || "" });
    $("#drawer-title").textContent = d.client + (d.phone ? ` · ${d.phone}` : "");
    const icon = { call: "☎", order: "🔧", payment: "₽" };
    const what = (e) => {
      if (e.kind === "call") return `<b>${esc(e.dir)} звонок</b>${e.missed ? ' <span class="tag bad">пропущен</span>' : ` · ${secs(e.dur)}`}${e.callback === "нет" ? ' <span class="tag bad">не перезвонили</span>' : ""}<div class="muted">${esc(e.line)} · ${esc(e.user)}</div>`;
      if (e.kind === "order") return `<b>Заказ-наряд ${esc(e.number)}</b> · ${esc(e.status)}${e.amount !== undefined ? ` · ${money(e.amount)}` : ""}<div class="muted">${esc(e.car)} · мастер ${esc(e.master)} · создал ${esc(e.author)}${e.issued ? ` · выдан ${esc(e.issued)}` : ""}${e.source ? ` · источник: ${esc(e.source)}` : ""}</div>`;
      return `<b>Оплата ${money(e.amount)}</b> · ${esc(e.method)}<div class="muted">по ${esc(e.basis)}</div>`;
    };
    $("#drawer-body").innerHTML = `<p class="muted">За период данных витрины: звонков ${num(d.calls)}, заказ-нарядов ${num(d.orders)}. Новое — сверху.</p><ul class="timeline">${d.events.map((e) => `<li><span class="when">${esc(e.time || e.created)}</span><span>${icon[e.kind]}</span><span class="what">${what(e)}</span></li>`).join("") || '<li class="muted">Событий нет</li>'}</ul>`;
  } catch (e) { if (e.message !== "login") $("#drawer-body").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ разделы
function kpiCard(k) {
  const v = k.money ? money(k.value) : k.pct ? (k.value === null ? "—" : `${num(k.value)} %`) : num(k.value);
  let delta = "";
  if (k.prev !== null && k.prev !== undefined && k.value !== null && k.value !== undefined && k.prev !== 0) {
    const p = Math.round((100 * (k.value - k.prev)) / Math.abs(k.prev));
    const cls = p === 0 || (!k.bad_up && !k.good_up) ? "" : (p > 0) === Boolean(k.bad_up) ? (p > 0 ? "up-bad" : "down-bad") : p > 0 ? "up-good" : "down-good";
    delta = `<b class="${cls}">${p > 0 ? "▲" : p < 0 ? "▼" : "="} ${Math.abs(p)} %</b> к прошлому периоду (${k.money ? money(k.prev) : num(k.prev)})`;
  }
  const tag = k.list ? "button" : "div";
  return `<${tag} class="kpi${k.list ? " click" : ""}" ${k.list ? `data-list="${k.list}"` : ""}><div class="t">${esc(k.title)}</div><div class="v">${v}</div><div class="d">${k.note ? esc(k.note) + "<br>" : ""}${delta}</div></${tag}>`;
}
function problemCard(p) {
  return `<button class="problem" data-list="${p.key}" title="${esc(p.hint)}"><span class="n ${p.count ? "" : "zero"}">${num(p.count)}</span><span><div class="name">${esc(p.title)}</div><div class="hint">${esc(p.hint)}</div>${p.sum !== undefined ? `<div class="money">на ${money(p.sum)}</div>` : ""}</span></button>`;
}
function bindLists(root) { root.querySelectorAll("[data-list]").forEach((b) => b.addEventListener("click", () => openList(b.dataset.list))); }

async function renderHome(v) {
  const d = await api("/api/home");
  const s = d.series;
  v.innerHTML = `
    <div class="kpis">${d.kpis.map(kpiCard).join("")}</div>
    <div class="grid">
      <div class="card"><h3>Звонки по дням</h3><p class="sub">Входящие и пропущенные из них</p>${lineChart(d.days, [{ name: "Входящие", values: s["Входящие"], color: COLORS[0] }, { name: "Пропущенные", values: s["Пропущенные"], color: COLORS[1] }], { label: "Звонки по дням" })}</div>
      ${d.revenue ? `<div class="card"><h3>Выручка по дням</h3><p class="sub">Оплаты в кассы по ЗН и расходным накладным, минус возвраты</p>${lineChart(d.days, [{ name: "Выручка", values: d.revenue, color: COLORS[0] }], { money: true, label: "Выручка по дням" })}</div>` : ""}
      <div class="card"><h3>Новые заказ-наряды по дням</h3><p class="sub">По дате создания</p>${lineChart(d.days, [{ name: "Новые ЗН", values: s["Новые ЗН"], color: COLORS[0] }], { label: "Новые ЗН по дням" })}</div>
    </div>
    <div class="card"><h3>Где теряем клиентов и деньги</h3><p class="sub">Нажмите — откроется список конкретных случаев</p><div class="problems">${d.problems.map(problemCard).join("")}</div></div>`;
  bindLists(v);
}
async function renderProblems(v) {
  const d = await api("/api/home");
  v.innerHTML = `<div class="card"><h3>Проблемные места за период</h3><p class="sub">Каждая карточка открывает список: звонок, клиент, заказ-наряд. Из списка — история клиента.</p><div class="problems">${d.problems.map(problemCard).join("")}</div></div>
    <div class="card"><h3>Как считается</h3><p class="sub">Пропущенный — входящий длительностью 0 с. «Перезвонили» — в течение 72 ч был исходящий на этот номер или принятый входящий с него. Обращение без ЗН — разговор от 30 с, после которого за 14 дней у клиента не появился заказ-наряд, а машина в тот момент не была в ремонте: это повод проверить, а не готовый вывод.</p></div>`;
  bindLists(v);
}
async function renderTelephony(v) {
  const t = await api("/api/telephony");
  const stats = [
    ["Входящих", num(t.incoming)], ["Медиана до перезвона", t.callback_median === null ? "—" : `${num(t.callback_median)} мин`],
    ["Ожидание ответа (сред.)", t.wait_avg === null ? "нет данных" : secs(t.wait_avg)],
    ["Разговор: входящий", secs(t.talk_in_avg)], ["Разговор: исходящий", secs(t.talk_out_avg)],
    ["От известных клиентов", t.known_pct === null ? "—" : `${num(t.known_pct)} %`],
  ];
  v.innerHTML = `
    <div class="card"><div class="stats">${stats.map(([a, b]) => `<div class="stat"><div class="v">${b}</div><div class="t">${a}</div></div>`).join("")}
      <button class="kpi click" data-list="repeat"><div class="t">Повторные обращения</div><div class="v">${num(t.repeat)}</div><div class="d">номеров звонили 2+ раз за неделю; ${num(t.repeat_missed_first)} — после пропущенного</div></button></div></div>
    <div class="grid">
      <div class="card"><h3>Нагрузка: входящие по дням недели и часам</h3><p class="sub">Чем темнее — тем больше звонков. Наведите на ячейку — сколько пропущено.</p>${heatmap(t, "heat")}</div>
      <div class="card"><h3>Пропущенные по дням недели и часам</h3><p class="sub">Где именно не успевают ответить — главный довод «нужен ли ещё человек на телефон»</p>${heatmap(t, "heat_missed")}</div>
    </div>
    <div class="grid">
      <div class="card"><h3>Пропущенные по часам</h3><p class="sub">Доля пропущенных от входящих в этот час (часы, где было 5+ звонков)</p>${bars(t.hours.filter((h) => h.incoming >= 5).map((h) => ({ label: `${h.hour}:00–${h.hour + 1}:00`, value: Math.round((100 * h.missed) / h.incoming), extra: `${h.missed} из ${h.incoming}` })), { fmt: (x) => `${x} %` })}</div>
      <div class="card"><h3>Скорость перезвона</h3><p class="sub">Через сколько перезвонили после пропущенного</p>${bars(t.callback.map((c) => ({ label: c.label, value: c.count, bad: c.bad })))}</div>
    </div>
    <div class="card"><h3>По линиям</h3><p class="sub">Названия линий задаются в настройках программы (например, «21 = Авито»)</p>${table([{ key: "line", title: "Линия" }, { key: "incoming", title: "Входящих", num: true }, { key: "missed", title: "Пропущено", num: true }, { key: "missed_pct", title: "% пропущ.", num: true, fmt: (x) => (x === null ? "—" : num(x) + " %") }, { key: "no_callback", title: "Не перезвонили", num: true }, { key: "outgoing", title: "Исходящих", num: true }], t.lines)}</div>`;
  bindLists(v);
}
async function renderManagers(v) {
  const d = await api("/api/managers");
  const pctf = (x) => (x === null || x === undefined ? "—" : num(x) + " %");
  v.innerHTML = `
    <div class="card"><h3>Менеджеры: звонки, заказ-наряды, задачи</h3><p class="sub">Звонок записан на пользователя Далиона, у которого он прошёл. Сейчас звонки есть у: ${esc(d.phone_users.join(", ") || "—")}. Конверсия — доля разговоров с клиентами (от 30 с), после которых за 14 дней появился ЗН.</p>
    ${table([{ key: "name", title: "Менеджер" }, { key: "answered", title: "Принял", num: true }, { key: "missed", title: "Пропустил", num: true }, { key: "missed_pct", title: "% пропущ.", num: true, fmt: pctf }, { key: "no_callback", title: "Не перезвонил", num: true }, { key: "outgoing", title: "Исходящих", num: true }, { key: "talk_avg", title: "Ср. разговор", num: true, fmt: secs }, { key: "conversion", title: "Конверсия в ЗН", num: true, fmt: pctf }, { key: "orders", title: "Создал ЗН", num: true }, { key: "no_source", title: "ЗН без источника", num: true }, { key: "tasks_done", title: "Задач выполнено", num: true }, { key: "tasks_open", title: "Задач открыто", num: true }], d.managers)}</div>
    <div class="card"><h3>Мастера-приёмщики</h3><p class="sub">ЗН по мастеру в заказ-наряде${state.role === "owner" ? "; сумма и средний чек — по выданным за период" : ""}</p>
    ${table([{ key: "name", title: "Мастер" }, { key: "orders", title: "Новых ЗН", num: true }, { key: "issued", title: "Выдано", num: true }, ...(state.role === "owner" ? [{ key: "sum", title: "Сумма выданных", num: true, fmt: money }, { key: "avg_check", title: "Средний чек", num: true, fmt: money }] : [])], d.masters)}</div>
    <div class="card"><h3>Качество разговоров</h3><p class="sub">Следующий этап: в «Простых звонках» есть поле с путём к записи разговора. Когда проверим, что записи сохраняются, можно будет расшифровывать их на сервере (без облака) и оценивать по чек-листу.</p></div>`;
}
async function renderFinance(v) {
  const f = await api("/api/finance");
  const mlabel = (m) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(2, 4)}`;
  v.innerHTML = `
    <div class="kpis">${kpiCard({ title: "Выручка за период", value: f.revenue, money: true })}${kpiCard({ title: "Выданы, не оплачены полностью", value: f.unpaid_sum, money: true, note: `${num(f.unpaid_count)} ЗН`, list: "unpaid" })}${kpiCard({ title: "ЗН с источником рекламы", value: f.source_filled_pct, pct: true, note: "доля новых ЗН — без этого не понять, какая площадка приносит деньги" })}</div>
    <div class="card"><h3>Выручка по дням</h3>${lineChart(f.days, [{ name: "Выручка", values: f.daily, color: COLORS[0] }], { money: true, label: "Выручка по дням" })}</div>
    <div class="grid">
      <div class="card"><h3>Способы оплаты</h3>${bars(f.by_method.map((m) => ({ label: m.name, value: m.amount })), { money: true })}</div>
      <div class="card"><h3>Направления</h3>${bars(f.by_direction.map((m) => ({ label: m.name, value: m.amount })), { money: true })}</div>
      <div class="card"><h3>Из чего состоят выданные ЗН</h3>${bars(f.structure.map((m) => ({ label: m.name, value: m.amount })), { money: true })}</div>
    </div>
    <div class="grid">
      <div class="card"><h3>Работы, которые приносят больше всего</h3><p class="sub">Сумма работ в проведённых ЗН за период, топ-20</p>${bars(f.top_works.map((w) => ({ label: w.work, value: w.amount, extra: `${w.orders} ЗН` })), { money: true })}</div>
      <div class="card"><h3>Источники рекламы</h3><p class="sub">Сумма — по выданным за период ЗН. «Не указан» — источник в ЗН не заполнили</p>${table([{ key: "source", title: "Источник" }, { key: "orders", title: "Новых ЗН", num: true }, { key: "issued", title: "Выдано", num: true }, { key: "amount", title: "Сумма", num: true, fmt: money }], f.sources)}</div>
    </div>
    <div class="card"><h3>По месяцам</h3><p class="sub">Все месяцы, что есть в данных витрины (без фильтра периода)</p>${table([{ key: "month", title: "Месяц", fmt: (m) => mlabel(m) }, { key: "revenue", title: "Выручка", num: true, fmt: money }, { key: "issued", title: "Выдано ЗН", num: true }, { key: "avg_check", title: "Средний чек", num: true, fmt: money }], f.months)}</div>`;
  bindLists(v);
}
const RENDER = { home: renderHome, problems: renderProblems, telephony: renderTelephony, managers: renderManagers, finance: renderFinance };

async function refresh() {
  if (state.tab === "finance" && state.role !== "owner") state.tab = "home";
  syncControls(); writeHash();
  const v = $("#view");
  v.innerHTML = '<p class="muted">Загружаю…</p>';
  try { await RENDER[state.tab](v); drawCharts(); }
  catch (e) { if (e.message !== "login") v.innerHTML = `<div class="card"><p class="error">${esc(e.message)}</p></div>`; }
}

async function start() {
  try {
    const m = await api("/api/meta");
    state.role = m.role; state.meta = m.meta;
    $("#login").hidden = true; $("#app").hidden = false;
    document.querySelectorAll("[data-owner]").forEach((b) => (b.hidden = m.role !== "owner"));
    fillSelect($("#user"), m.options.users); fillSelect($("#line"), m.options.lines);
    const at = m.meta.collected_at || "";
    $("#fresh").textContent = at ? `данные на ${at.slice(8, 10)}.${at.slice(5, 7)} ${at.slice(11, 16)}` : "";
    const notes = [];
    if (m.meta.demo) notes.push('<div class="note">Демо-режим: выдуманные клиенты и цифры — только чтобы показать, как выглядит витрина.</div>');
    const errs = Object.entries(m.meta.errors || {});
    if (errs.length) notes.push(`<div class="note bad">Не загрузились разделы: ${errs.map(([k, e]) => `<b>${esc(k)}</b> (${esc(e).slice(0, 160)})`).join("; ")}</div>`);
    if (at && Date.now() - new Date(at.replace(" ", "T")).getTime() > 36 * 3600e3) notes.push('<div class="note bad">Данные старше суток: утренний сбор, похоже, не выполнился.</div>');
    $("#banner").innerHTML = notes.join("");
    refresh();
  } catch (e) {
    if (e.message !== "login") { $("#app").hidden = false; $("#view").innerHTML = `<div class="card"><p class="error">${esc(e.message)}</p></div>`; }
  }
}

readHash();
if (!state.from || !state.to) setPreset(state.preset || "30");
start();
