import { CATEGORIES, CATEGORY_ORDER, ONLY_GROUPS, PRESETS, runSearch, splitNames, truncate, computeStats } from "./engine.js";

const $ = (id) => document.getElementById(id);
const DEFAULT_REPO = "AbyzeR-1337/SearchMaster";
const RESULTS_BRANCH = "search-results";
const WORKFLOW = "search.yml";
const MIN_SCORE = 0.12;

// ------------------------------------------------------------------ storage (this device only)
const store = {
  get(k, d) { try { const v = localStorage.getItem("sm:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("sm:" + k, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem("sm:" + k); } catch {} },
};

function detectRepo() {
  const m = /^([\w-]+)\.github\.io$/i.exec(location.hostname);
  const seg = location.pathname.split("/").filter(Boolean)[0];
  return m && seg ? `${m[1]}/${seg}` : DEFAULT_REPO;
}

let settings = { repo: "", githubToken: "", youtubeKey: "", googleKey: "", googleCx: "", ...store.get("settings", {}) };
if (!settings.repo) settings.repo = detectRepo();

const view = { state: null, tab: store.get("tab", "all"), filter: "", showAll: false, report: null, aiText: "" };

// Pinned topics: one tap opens everything about a game. The first one is the user's game.
const DEFAULT_PINS = [{
  id: "seven-souls",
  title: "Seven Souls Online / Martial Empires",
  q: "Seven Souls Online / Martial Empires / 7Souls / 세븐소울즈",
  note: "MMORPG студии CRS Space (позже Neowiz CRS), 2009–2010. В Европе — Martial Empires (gamigo), в Корее — 세븐소울즈, в России — Seven Souls.",
  mode: "deep", exact: true, only: "", langs: "ru,en,ko", preset: "all",
}];
let pins = store.get("pins", null) || DEFAULT_PINS;
let cloudIndex = [];

// ------------------------------------------------------------------ DOM helpers
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") n.textContent = v;
    else if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c);
  return n;
}
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
const link = (href, text, cls) => (safeUrl(href) ? el("a", { href, target: "_blank", rel: "noopener", class: cls, text }) : el("span", { text }));

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

const fmtDate = (d) => {
  if (!d) return "";
  const s = String(d);
  return /^\d{4}-\d\d-\d\d/.test(s) ? s.slice(0, 10).split("-").reverse().join(".") : s;
};
const fmtNum = (n) => (n >= 10000 ? `${Math.round(n / 1000)}k` : String(n));

// ------------------------------------------------------------------ search
let runCounter = 0;

function currentOptions() {
  const mode = document.querySelector('input[name="mode"]:checked')?.value || "normal";
  const preset = $("preset").value;
  const langs = $("langs").value.split(/[,\s]+/).map((s) => s.trim().toLowerCase()).filter((s) => /^[a-z-]{2,10}$/.test(s));
  const only = ONLY_GROUPS[$("only").value] ? $("only").value : "";
  return { mode, preset, langs: langs.length ? langs : ["ru", "en"], exact: $("exact").checked, only };
}

function applyOptions(o) {
  const radio = document.querySelector(`input[name="mode"][value="${o.mode || "normal"}"]`);
  if (radio) radio.checked = true;
  $("exact").checked = Boolean(o.exact);
  $("only").value = ONLY_GROUPS[o.only] ? o.only : "";
  if (o.langs) $("langs").value = o.langs;
  if (o.preset && PRESETS[o.preset]) $("preset").value = o.preset;
}

function setUrl(params) {
  const u = new URL(location.href);
  u.search = "";
  for (const [k, v] of Object.entries(params)) if (v) u.searchParams.set(k, v);
  history.replaceState(null, "", u);
}

async function startSearch(query) {
  query = query.trim();
  if (!query) return;
  const runId = ++runCounter;
  const opts = currentOptions();
  $("q").value = query;
  view.report = null;
  view.aiText = "";
  view.state = null;
  setUrl({ q: query, mode: opts.mode !== "normal" ? opts.mode : "", src: opts.preset !== "all" ? opts.preset : "",
    exact: opts.exact ? "1" : "", only: opts.only, langs: opts.langs.join(",") !== "ru,en" ? opts.langs.join(",") : "" });
  document.title = `${query} — SearchMaster`;
  $("hero").hidden = true;
  $("pinsBox").hidden = true;
  $("reportBanner").hidden = true;
  $("layout").hidden = false;
  $("progress").hidden = false;
  $("goBtn").disabled = true;
  $("results").replaceChildren(el("div", { class: "empty", text: "Ищу по всем источникам…" }));
  try {
    await runSearch(query, { mode: opts.mode, sources: PRESETS[opts.preset] || PRESETS.all, langs: opts.langs, settings,
      exact: opts.exact, only: opts.only }, (state) => {
      if (runId !== runCounter) return;
      view.state = state;
      renderAll();
    });
  } catch (e) {
    toast(`Поиск прервался: ${e.message}`);
  } finally {
    if (runId === runCounter) {
      $("progress").hidden = true;
      $("goBtn").disabled = false;
    }
  }
}

// ------------------------------------------------------------------ rendering
function renderAll() {
  const s = view.state;
  if (!s) return;
  renderSubject(s.subject);
  renderSearchInfo(s);
  renderAI();
  renderTerms(s.terms || []);
  renderTabsAndResults();
  renderSources(s.statuses || {});
}

function renderSubject(subj) {
  const box = $("subject");
  if (!subj || (!subj.extract && !subj.facts?.length && !subj.description)) {
    box.hidden = true;
    return;
  }
  const cover = subj.steamAppId ? `https://cdn.akamai.steamstatic.com/steam/apps/${subj.steamAppId}/header.jpg` : subj.image;
  const contain = cover && !subj.steamAppId && /\.(svg|png)|FilePath/i.test(cover);
  const extract = el("p", { class: "extract clamp", text: subj.extract || "" });
  const more = (subj.extract || "").length > 420
    ? el("button", { class: "link-btn", type: "button", text: "читать дальше", onclick: (e) => { extract.classList.remove("clamp"); e.target.remove(); } })
    : null;
  const facts = el("dl", { class: "facts" }, ...(subj.facts || []).flatMap((f) => [el("dt", { text: f.label }), el("dd", { text: f.value })]));
  const links = [];
  if (subj.wikiUrl) links.push(link(subj.wikiUrl, "Википедия"));
  for (const l of subj.links || []) links.push(link(l.url, l.label));
  box.replaceChildren(
    cover ? el("img", { class: "cover" + (contain ? " contain" : ""), src: cover, alt: "", referrerpolicy: "no-referrer", loading: "lazy" }) : el("div"),
    el("div", {},
      el("div", { class: "eyebrow", text: subj.isGame ? "Главное · игра" : "Главное" }),
      el("h2", { text: subj.title }),
      subj.description ? el("div", { class: "desc", text: subj.description }) : null,
      subj.extract ? extract : null, more,
      subj.facts?.length ? facts : null,
      links.length ? el("div", { class: "links" }, links) : null,
      subj.aliases?.length ? el("div", { class: "aliases", text: "Также известно как: " + subj.aliases.join(" · ") }) : null,
    ),
  );
  box.hidden = false;
}

function renderSearchInfo(s) {
  const box = $("searchInfo");
  const names = s.names || splitNames(s.query || "").names;
  const parts = [];
  if (names.length > 1) parts.push(el("span", {}, "Ищу по названиям: ", el("b", { text: names.join(" · ") })));
  if (s.exact) parts.push(el("span", { class: "flag", text: "точно" }));
  if (s.only?.length) {
    const only = Array.isArray(s.only) ? s.only : [s.only];
    parts.push(el("span", { class: "flag", text: "только: " + only.map((o) => ONLY_GROUPS[o]?.label || o).join(", ") }));
  }
  box.replaceChildren(...parts);
  box.hidden = !parts.length;
}

// ------------------------------------------------------------------ pinned topics
function savePins() { store.set("pins", pins); }

function renderPins() {
  const box = $("pins");
  if (!pins.length) {
    box.replaceChildren(el("li", { class: "muted", text: "Здесь появятся темы, которые вы закрепите кнопкой «Закрепить в „Мои темы“» после поиска." }));
    return;
  }
  box.replaceChildren(...pins.map((p) => {
    const report = cloudIndex.find((it) => it.query === p.q);
    const meta = [{ quick: "быстро", normal: "обычно", deep: "глубоко" }[p.mode] || p.mode, p.exact ? "точно" : "",
      p.only ? `только ${ONLY_GROUPS[p.only]?.label || p.only}` : "", p.langs || ""].filter(Boolean).join(" · ");
    return el("li", { class: "pin" },
      el("div", {},
        el("div", { class: "pin-title", text: p.title }),
        p.note ? el("div", { class: "pin-note", text: p.note }) : null,
        el("div", { class: "pin-meta", text: `${splitNames(p.q).names.join(" · ")} — ${meta}` })),
      el("div", { class: "pin-actions" },
        el("button", { class: "btn btn-primary small", type: "button", text: "Показать всё", onclick: () => runPin(p) }),
        report ? el("button", { class: "btn btn-ghost small", type: "button", text: "Облачный отчёт", onclick: () => openReport(report.path) }) : null,
        el("button", { class: "link-btn", type: "button", text: "убрать", onclick: () => { pins = pins.filter((x) => x !== p); savePins(); renderPins(); } })));
  }));
}

function runPin(p) {
  applyOptions(p);
  startSearch(p.q);
}

function pinCurrent() {
  const s = view.state;
  const q = s?.query || $("q").value.trim();
  if (!q) return;
  const o = currentOptions();
  if (pins.some((p) => p.q === q && p.mode === o.mode && p.exact === o.exact && p.only === o.only)) {
    toast("Эта тема уже закреплена");
    return;
  }
  pins.unshift({ id: String(Date.now()), title: truncate(q, 80), q, note: s?.subject?.description || "", mode: o.mode,
    exact: o.exact, only: o.only, langs: o.langs.join(","), preset: o.preset });
  savePins();
  renderPins();
  toast("Закреплено. Тема появится на главном экране в «Мои темы»");
}

function renderMd(md) {
  const frag = document.createDocumentFragment();
  let list = null;
  const inline = (text, parent) => {
    // [label](https://…) links and **bold**; everything else is plain text
    const re = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      parent.append(text.slice(last, m.index));
      if (m[2]) parent.append(link(m[2], m[1]));
      else parent.append(el("strong", { text: m[3] }));
      last = re.lastIndex;
    }
    parent.append(text.slice(last));
    return parent;
  };
  for (const line of md.split("\n")) {
    const li = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line);
    if (li) {
      if (!list) frag.append((list = el("ul")));
      list.append(inline(li[1], el("li")));
      continue;
    }
    list = null;
    const h = /^#{1,4}\s+(.*)$/.exec(line);
    if (h) frag.append(inline(h[1], el("h3")));
    else if (line.trim()) frag.append(inline(line, el("p")));
  }
  return frag;
}

function renderAI() {
  const box = $("ai");
  if (!view.aiText) { box.hidden = true; return; }
  $("aiBody").replaceChildren(renderMd(view.aiText));
  box.hidden = false;
}

function renderTerms(terms) {
  const box = $("termsBox");
  if (!terms.length) { box.hidden = true; return; }
  const base = view.state?.query || "";
  $("terms").replaceChildren(...terms.slice(0, 18).map(([t]) =>
    el("button", { class: "chip", type: "button", text: t, title: `Искать «${base} ${t}»`, onclick: () => startSearch(`${base} ${t}`) })));
  box.hidden = false;
}

function pool() {
  const all = view.state?.results || [];
  return view.showAll ? all : all.filter((r) => r.score >= MIN_SCORE);
}

function filtered(list) {
  const q = view.filter.trim().toLowerCase();
  if (!q) return list;
  return list.filter((r) => `${r.title} ${r.snippet} ${r.url} ${r.meta?.site || ""}`.toLowerCase().includes(q));
}

function renderTabsAndResults() {
  const items = filtered(pool());
  const counts = {};
  for (const r of items) counts[r.category] = (counts[r.category] || 0) + 1;
  const tabs = [["all", "Всё", items.length], ...CATEGORY_ORDER.filter((c) => counts[c]).map((c) => [c, CATEGORIES[c], counts[c]])];
  if (!tabs.some(([k]) => k === view.tab)) view.tab = "all";
  $("tabs").replaceChildren(...tabs.map(([k, label, n]) => el("button", {
    class: "tab", type: "button", "aria-pressed": String(k === view.tab),
    onclick: () => { view.tab = k; store.set("tab", k); renderTabsAndResults(); },
  }, label, el("span", { class: "n", text: String(n) }))));

  const box = $("results");
  if (!items.length) {
    const done = view.state?.done !== false;
    const hint = view.state?.exact ? "Ничего не найдено с точным названием. Снимите галочку «Точно» или добавьте другие названия через « / »."
      : "Ничего не найдено. Попробуйте другое написание или включите «нерелевантные».";
    box.replaceChildren(el("div", { class: "empty", text: done ? hint : "Ищу по всем источникам…" }));
    return;
  }
  const cats = view.tab === "all" ? CATEGORY_ORDER : [view.tab];
  const groups = [];
  for (const cat of cats) {
    const group = items.filter((r) => r.category === cat);
    if (!group.length) continue;
    const limit = view.tab === "all" ? (cat === "image" ? 18 : 6) : 400;
    const body = cat === "image" ? gallery(group.slice(0, limit)) : el("div", { class: "list" }, group.slice(0, limit).map(card));
    const more = group.length > limit
      ? el("button", { class: "btn btn-ghost small more", type: "button", text: `Показать все ${group.length}`,
          onclick: () => { view.tab = cat; store.set("tab", cat); renderTabsAndResults(); $("tabs").scrollIntoView({ block: "start", behavior: "smooth" }); } })
      : null;
    groups.push(el("section", { class: "group" }, el("h3", {}, CATEGORIES[cat], el("span", { class: "mono", text: String(group.length) })), body, more));
  }
  box.replaceChildren(...groups);
}

function metaLine(r) {
  const m = r.meta || {}, bits = [];
  bits.push(m.site || r.sources.join(", "));
  if (r.sources.length > 1) bits.push(`${r.sources.length} источника`);
  if (m.stars) bits.push(`★ ${fmtNum(m.stars)}`);
  if (m.language) bits.push(m.language);
  if (m.downloads) bits.push(`↓ ${fmtNum(m.downloads)}`);
  if (m.points) bits.push(`▲ ${m.points}`);
  if (m.comments) bits.push(`${m.comments} комм.`);
  if (m.license) bits.push(m.license);
  if (r.date) bits.push(fmtDate(r.date));
  if (r.author) bits.push(truncate(r.author, 40));
  if (r.local_path) bits.push("файл в архиве запуска");
  return bits.filter(Boolean).join(" · ");
}

function card(r) {
  const thumb = safeUrl(r.thumbnail);
  const text = r.meta?.passage || r.snippet;
  const body = el("div", {},
    el("div", { class: "t" }, link(r.url, r.title)),
    el("div", { class: "m", text: metaLine(r) }),
    text ? el("div", { class: "s", text: truncate(text, 320) }) : null);
  const cls = "card" + (thumb ? "" : " noimg") + (r.score < MIN_SCORE ? " low" : "");
  return el("article", { class: cls },
    thumb ? el("img", { src: thumb, alt: "", loading: "lazy", referrerpolicy: "no-referrer", onerror: (e) => e.target.remove() }) : null, body);
}

function gallery(items) {
  return el("div", { class: "gallery" }, items.map((r) => {
    const src = safeUrl(r.thumbnail) || safeUrl(r.meta?.file_url);
    if (!src) return null;
    return el("button", { type: "button", title: r.title, onclick: () => openLightbox(r) },
      el("img", { src, alt: r.title, loading: "lazy", referrerpolicy: "no-referrer",
        onerror: (e) => e.target.closest("button")?.remove() }), el("span", { text: r.title }));
  }));
}

function openLightbox(r) {
  $("lbImg").src = safeUrl(r.meta?.file_url) || safeUrl(r.thumbnail) || "";
  $("lbImg").alt = r.title;
  const a = $("lbLink");
  a.href = safeUrl(r.url) || "#";
  a.textContent = `${r.title} — открыть источник`;
  $("lightbox").hidden = false;
  $("lbClose").focus();
}
const closeLightbox = () => { $("lightbox").hidden = true; $("lbImg").removeAttribute("src"); };

function renderSources(statuses) {
  const list = Object.values(statuses);
  const order = { running: 0, waiting: 1, ok: 2, empty: 3, error: 4, skipped: 5 };
  list.sort((a, b) => (order[a.state] ?? 9) - (order[b.state] ?? 9) || b.count - a.count);
  $("sources").replaceChildren(...list.map((s) => el("li", { class: `st-${s.state}` },
    el("span", { class: "dot", "aria-hidden": "true" }),
    el("span", { text: s.title }),
    el("span", { class: "cnt", text: s.state === "running" || s.state === "waiting" ? "…" : s.state === "skipped" ? "выкл" : String(s.count) }),
    s.message && (s.state === "error" || s.state === "skipped") ? el("span", { class: "msg", text: s.message }) : null)));
  const st = view.state;
  const working = list.filter((s) => s.state === "ok").length;
  const secs = st?.finishedAt && st?.startedAt ? ` · ${((st.finishedAt - st.startedAt) / 1000).toFixed(1)} с` : "";
  $("sourcesSummary").textContent = st?.done === false ? "ищу…" : `${working} с результатами${secs}`;
}

// ------------------------------------------------------------------ export & share
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function exportJSON() {
  const s = view.state;
  if (!s) return;
  const data = { query: s.query, subject: s.subject ? { ...s.subject, results: undefined } : null, aliases: s.aliases, terms: s.terms,
    results: pool().map(({ url, title, category, snippet, thumbnail, date, author, meta, score, sources }) =>
      ({ url, title, category, snippet, thumbnail, date, author, meta, score, sources })) };
  download(`searchmaster-${s.query.replace(/[^\p{L}\p{N}]+/gu, "-")}.json`, JSON.stringify(data, null, 1), "application/json");
}

function exportCSV() {
  const s = view.state;
  if (!s) return;
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const rows = [["score", "category", "title", "url", "sources", "date", "author", "snippet"]];
  for (const r of pool()) rows.push([r.score, r.category, r.title, r.url, r.sources.join("|"), r.date, r.author, truncate(r.snippet, 300)]);
  download(`searchmaster-${s.query.replace(/[^\p{L}\p{N}]+/gu, "-")}.csv`, "﻿" + rows.map((r) => r.map(esc).join(",")).join("\n"), "text/csv");
}

async function share() {
  const url = location.href;
  try {
    await navigator.clipboard.writeText(url);
    toast("Ссылка на этот поиск скопирована");
  } catch {
    prompt("Скопируйте ссылку:", url);
  }
}

// ------------------------------------------------------------------ GitHub (cloud search & reports)
function ghHeaders(raw = false) {
  const h = { Accept: raw ? "application/vnd.github.raw+json" : "application/vnd.github+json" };
  if (settings.githubToken) h.Authorization = `Bearer ${settings.githubToken}`;
  return h;
}

async function ghFile(path) {
  const api = `https://api.github.com/repos/${settings.repo}/contents/${path}?ref=${RESULTS_BRANCH}`;
  try {
    const r = await fetch(api, { headers: ghHeaders(true) });
    if (r.ok) return r.json();
    if (r.status === 404) return null;
  } catch {}
  // fallback without API limits (cached for a few minutes by GitHub)
  const r = await fetch(`https://raw.githubusercontent.com/${settings.repo}/${RESULTS_BRANCH}/${path}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GitHub ответил ${r.status}`);
  return r.json();
}

async function loadHistory() {
  const box = $("history");
  box.replaceChildren(el("li", { class: "muted", text: "Загружаю…" }));
  let index;
  try {
    index = await ghFile("index.json");
  } catch (e) {
    box.replaceChildren(el("li", { class: "muted", text: `Не удалось загрузить список: ${e.message}` }));
    return [];
  }
  cloudIndex = index || [];
  renderPins();
  if (!index?.length) {
    box.replaceChildren(el("li", { class: "muted", text: "Пока нет облачных отчётов. Запустите полный поиск — отчёт появится здесь." }));
    return [];
  }
  box.replaceChildren(...index.slice(0, 40).map((it) => el("li", {},
    el("button", { type: "button", text: it.query, onclick: () => openReport(it.path) }),
    el("span", { class: "mono", text: [fmtDate((it.created || "").slice(0, 10)), it.mode, `${it.total} рез.`, it.downloaded ? `${it.downloaded} файлов` : ""].filter(Boolean).join(" · ") }))));
  return index;
}

function subjectFromReport(results) {
  const card = results.find((r) => r.meta?.game_card);
  const summary = results.find((r) => r.meta?.summary);
  if (!card && !summary) return null;
  const m = card?.meta || {};
  const facts = [["developers", "Разработчик"], ["publishers", "Издатель"], ["release_date", "Дата выхода"], ["genres", "Жанр"],
    ["platforms", "Платформы"], ["metacritic", "Metacritic"], ["price", "Цена"]]
    .filter(([k]) => m[k] && (!Array.isArray(m[k]) || m[k].length))
    .map(([k, label]) => ({ label, value: Array.isArray(m[k]) ? m[k].join(", ") : String(m[k]) }));
  const links = [];
  if (card) links.push({ label: "Steam", url: card.url });
  if (m.website) links.push({ label: "Официальный сайт", url: m.website });
  return {
    title: m.name || summary?.title || card?.title, description: summary?.meta?.description || "",
    extract: summary?.snippet || card?.snippet || "", image: card?.thumbnail || summary?.thumbnail,
    steamAppId: null, wikiUrl: summary?.url || null, facts, links, aliases: [], isGame: Boolean(card),
  };
}

async function openReport(path) {
  let data;
  try {
    data = await ghFile(`${path}/results.json`);
  } catch (e) {
    toast(`Не удалось открыть отчёт: ${e.message}`);
    return;
  }
  if (!data) { toast("Отчёт не найден"); return; }
  ++runCounter; // stop rendering any live search
  const results = (data.results || []).map((r) => ({ ...r, sources: r.sources?.length ? r.sources : [r.source], meta: r.meta || {} }));
  const statuses = Object.fromEntries((data.sources || []).map((s) => [s.name, { ...s }]));
  view.state = { query: data.query, names: data.names, exact: data.exact, only: data.only, results, statuses,
    subject: subjectFromReport(results), terms: data.terms || [], done: true,
    stats: computeStats(results.filter((r) => r.score >= MIN_SCORE)) };
  view.aiText = data.ai_summary_linked || data.ai_summary || "";
  view.report = { path, data };
  $("q").value = data.query;
  setUrl({ report: path });
  document.title = `${data.query} — отчёт SearchMaster`;
  $("hero").hidden = true;
  $("pinsBox").hidden = true;
  $("layout").hidden = false;
  $("progress").hidden = true;
  renderAll();
  const links = data.links || {};
  const branchUrl = `https://github.com/${settings.repo}/blob/${RESULTS_BRANCH}/${path}/report.md`;
  $("reportBanner").replaceChildren(
    el("span", {}, el("b", { text: "Облачный отчёт" }), ` · ${fmtDate((data.created || "").slice(0, 10))} · режим ${data.mode} · ${results.filter((r) => r.score >= MIN_SCORE).length} результатов`),
    link(branchUrl, "report.md на GitHub"),
    links.run ? link(links.run, "архив с файлами (артефакт запуска)") : null,
    el("button", { class: "link-btn", type: "button", text: "Новый поиск в браузере", onclick: () => startSearch(data.query) }),
  );
  $("reportBanner").hidden = false;
  window.scrollTo({ top: 0 });
}

let defaultBranch = null;
async function getDefaultBranch() {
  if (defaultBranch) return defaultBranch;
  const r = await fetch(`https://api.github.com/repos/${settings.repo}`, { headers: ghHeaders() });
  if (!r.ok) throw Object.assign(new Error(`репозиторий ${settings.repo} недоступен (${r.status})`), { status: r.status });
  defaultBranch = (await r.json()).default_branch || "main";
  return defaultBranch;
}

function cloudStep(text, cls = "") {
  $("cloudStatus").append(el("div", { class: "step " + cls }, text));
}

function issueUrl(query, mode, preset, exact = false, only = "") {
  const u = new URL(`https://github.com/${settings.repo}/issues/new`);
  u.searchParams.set("template", "search.yml");
  u.searchParams.set("title", `🔍 ${query}`);
  u.searchParams.set("query", query);
  u.searchParams.set("mode", mode);
  u.searchParams.set("sources", preset === "media" || preset === "code" || preset === "text" || preset === "game" ? preset : "all");
  u.searchParams.set("exact", exact ? "да" : "нет");
  u.searchParams.set("only", only || "all");
  return u.href;
}

async function startCloud() {
  const query = $("q").value.trim() || view.state?.query;
  if (!query) { toast("Сначала введите запрос"); return; }
  const { mode, preset, langs, exact, only } = currentOptions();
  const status = $("cloudStatus");
  status.replaceChildren();
  if (!settings.githubToken) {
    cloudStep(el("span", {}, "Без токена запуск идёт через issue: откроется GitHub с заполненной формой, нажмите «Create». ",
      link(issueUrl(query, mode, preset, exact, only), "Открыть форму на GitHub →")));
    cloudStep(el("span", { class: "muted" }, "Отчёт придёт комментарием в issue и появится в «Облачных отчётах». Чтобы запускать прямо отсюда, добавьте токен в настройках."));
    window.open(issueUrl(query, mode, preset, exact, only), "_blank", "noopener");
    return;
  }
  const btn = $("cloudBtn");
  btn.disabled = true;
  const started = Date.now();
  try {
    const ref = await getDefaultBranch();
    const r = await fetch(`https://api.github.com/repos/${settings.repo}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      headers: { ...ghHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ ref, inputs: { query, mode, sources: preset, langs: langs.join(","),
        download: $("cloudDownload").checked ? "true" : "false", code_zips: $("cloudZips").value,
        exact: exact ? "true" : "false", only: only || "all" } }),
    });
    if (r.status === 401) throw new Error("GitHub не принял токен. Проверьте его в настройках.");
    if (r.status === 403) throw new Error("У токена нет права «Actions: Read and write» для этого репозитория.");
    if (r.status === 404) throw new Error(`Workflow не найден в ветке ${ref}. Нужно слить изменения в ${ref} или проверить репозиторий в настройках.`);
    if (!r.ok) throw new Error(`GitHub ответил ${r.status}: ${(await r.text()).slice(0, 200)}`);
    cloudStep(`Запущено: «${query}», режим ${mode}. Жду, пока GitHub возьмёт задачу…`);
    await trackRun(started, query);
  } catch (e) {
    cloudStep(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

async function trackRun(started, query) {
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  let run = null;
  const line = el("div", { class: "step" }, "…");
  $("cloudStatus").append(line);
  for (let i = 0; i < 180; i++) {
    await sleep(i < 3 ? 4000 : 8000);
    const r = await fetch(`https://api.github.com/repos/${settings.repo}/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=5`, { headers: ghHeaders() });
    if (!r.ok) continue;
    const runs = (await r.json()).workflow_runs || [];
    run = runs.find((x) => new Date(x.created_at).getTime() >= started - 15000) || run;
    if (!run) { line.textContent = "Ожидание запуска…"; continue; }
    const names = { queued: "в очереди", in_progress: "идёт поиск", completed: "завершено", waiting: "ожидание", pending: "ожидание" };
    const mins = Math.max(0, Math.round((Date.now() - started) / 60000));
    line.replaceChildren(`Статус: ${names[run.status] || run.status} · ${mins} мин · `, link(run.html_url, "открыть запуск"));
    if (run.status === "completed") break;
  }
  if (!run || run.status !== "completed") { cloudStep("Слежу слишком долго — откройте запуск по ссылке выше.", "err"); return; }
  if (run.conclusion !== "success") { cloudStep(`Запуск завершился с ошибкой (${run.conclusion}). Подробности по ссылке выше.`, "err"); return; }
  cloudStep("Готово. Загружаю отчёт…");
  for (let i = 0; i < 5; i++) {
    const index = await loadHistory();
    const hit = index.find((it) => it.query === query && new Date(it.created).getTime() >= started - 120000);
    if (hit) { await openReport(hit.path); return; }
    await sleep(5000);
  }
  cloudStep("Отчёт готов, но список ещё не обновился. Нажмите «обновить» в «Облачных отчётах» через минуту.");
}

// ------------------------------------------------------------------ settings dialog
function openSettings() {
  $("setRepo").value = settings.repo;
  $("setToken").value = settings.githubToken;
  $("setYoutube").value = settings.youtubeKey;
  $("setGoogleKey").value = settings.googleKey;
  $("setGoogleCx").value = settings.googleCx;
  $("settings").showModal();
}

function saveSettings() {
  const repo = $("setRepo").value.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\/+$/, "");
  settings = {
    repo: /^[\w.-]+\/[\w.-]+$/.test(repo) ? repo : detectRepo(),
    githubToken: $("setToken").value.trim(),
    youtubeKey: $("setYoutube").value.trim(),
    googleKey: $("setGoogleKey").value.trim(),
    googleCx: $("setGoogleCx").value.trim(),
  };
  store.set("settings", settings);
  defaultBranch = null;
  updateRepoLink();
  toast("Настройки сохранены на этом устройстве");
  loadHistory();
}

function updateRepoLink() {
  $("repoLink").href = `https://github.com/${settings.repo}`;
}

// ------------------------------------------------------------------ wiring
function init() {
  updateRepoLink();
  $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); startSearch($("q").value); });
  $("examples").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-q]");
    if (!b) return;
    if (b.dataset.preset) $("preset").value = b.dataset.preset;
    startSearch(b.dataset.q);
  });
  $("filter").addEventListener("input", (e) => { view.filter = e.target.value; renderTabsAndResults(); });
  $("showAll").addEventListener("change", (e) => { view.showAll = e.target.checked; renderTabsAndResults(); });
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsForm").addEventListener("submit", (e) => { if (e.submitter?.value === "save") saveSettings(); });
  $("clearSettings").addEventListener("click", () => {
    store.del("settings");
    settings = { repo: detectRepo(), githubToken: "", youtubeKey: "", googleKey: "", googleCx: "" };
    $("settings").close();
    updateRepoLink();
    toast("Настройки и ключи удалены с этого устройства");
  });
  $("jsonBtn").addEventListener("click", exportJSON);
  $("csvBtn").addEventListener("click", exportCSV);
  $("shareBtn").addEventListener("click", share);
  $("pinBtn").addEventListener("click", pinCurrent);
  $("cloudBtn").addEventListener("click", startCloud);
  $("historyReload").addEventListener("click", loadHistory);
  $("lbClose").addEventListener("click", closeLightbox);
  $("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeLightbox(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("lightbox").hidden) closeLightbox(); });

  const p = new URLSearchParams(location.search);
  if (p.get("mode") && document.querySelector(`input[name="mode"][value="${CSS.escape(p.get("mode"))}"]`)) {
    document.querySelector(`input[name="mode"][value="${CSS.escape(p.get("mode"))}"]`).checked = true;
  }
  if (p.get("src") && PRESETS[p.get("src")]) $("preset").value = p.get("src");
  if (p.get("exact") === "1") $("exact").checked = true;
  if (ONLY_GROUPS[p.get("only")]) $("only").value = p.get("only");
  if (p.get("langs") && /^[a-z,-]+$/.test(p.get("langs"))) $("langs").value = p.get("langs");
  renderPins();
  loadHistory();
  if (p.get("report")) openReport(p.get("report"));
  else if (p.get("q")) startSearch(p.get("q"));

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

init();
