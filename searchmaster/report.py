"""Reports: JSON (everything), Markdown (readable), HTML (interactive, self-contained)."""

from __future__ import annotations

import dataclasses
import html
import json
import re
from pathlib import Path
from typing import Any

from .models import ALL_KINDS, KIND_LABELS_RU

_SECRET_FIELDS = {"github_token", "brave_api_key", "google_api_key", "google_cse_id", "youtube_api_key",
                  "stackexchange_key", "searxng_url"}


def _config_dict(cfg) -> dict[str, Any]:
    d = dataclasses.asdict(cfg)
    for k in _SECRET_FIELDS:
        if d.get(k):
            d[k] = "***"
    return d


def build_payload(run, content_limit: int = 4000) -> dict[str, Any]:
    analysis = dict(run.analysis)
    analysis.pop("highlights", None)
    return {
        "topic": run.config.query,
        "aliases": run.plan.aliases,
        "started": run.started,
        "finished": run.finished,
        "config": _config_dict(run.config),
        "rounds": run.rounds,
        "notes": run.notes,
        "http": run.http_stats,
        "sources": run.source_status,
        "analysis": analysis,
        "ai_expansion": run.ai_expansion,
        "ai_summary": run.ai_summary,
        "downloads": run.downloads,
        "results": [r.to_dict(content_limit=content_limit) for r in run.results],
    }


def write_reports(run) -> dict[str, Path]:
    out = run.out_dir
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    payload = build_payload(run)
    if "json" in run.config.formats:
        p = out / "results.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        written["json"] = p
    if "md" in run.config.formats:
        p = out / "report.md"
        p.write_text(render_markdown(payload), encoding="utf-8")
        written["md"] = p
    if "html" in run.config.formats:
        p = out / "report.html"
        p.write_text(render_html(build_payload(run, content_limit=1500)), encoding="utf-8")
        written["html"] = p
    return written


# ----------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------

def _pairs(items, fmt="{0} ({1})", limit=15) -> str:
    items = list(items or [])[:limit]
    return ", ".join(fmt.format(*i) if isinstance(i, (list, tuple)) else str(i) for i in items) or "—"


def render_markdown(p: dict[str, Any]) -> str:
    a = p["analysis"]
    lines = [f"# SearchMaster: {p['topic']}", ""]
    lines.append(f"*Запуск: {p['started']} → {p['finished']}. Найдено релевантных: **{a['total']}** "
                 f"(отфильтровано шума: {a.get('filtered_out', 0)}), доменов: {a.get('domains_count', 0)}.*")
    if p.get("aliases"):
        lines.append(f"\nСинонимы/альтернативные названия: {', '.join(p['aliases'])}")
    lines.append("")
    if p.get("ai_summary"):
        lines += ["## Аналитическая сводка (ИИ)", "", p["ai_summary"], ""]
    lines += ["## Сводка по теме", ""]
    lines.append(f"- **Типы находок:** {_pairs([(KIND_LABELS_RU.get(k, k), v) for k, v in a['by_kind'].items()], limit=20)}")
    if a.get("developers"):
        lines.append(f"- **Разработчики:** {_pairs(a['developers'], '{0}')}")
    if a.get("publishers"):
        lines.append(f"- **Издатели:** {_pairs(a['publishers'], '{0}')}")
    lines.append(f"- **Движки (упоминания):** {_pairs(a['engines'])}")
    if a.get("repo_engines"):
        lines.append(f"- **Движки/технологии по файлам репозиториев:** {_pairs(a['repo_engines'])}")
    lines.append(f"- **Платформы:** {_pairs(a['platforms'])}")
    lines.append(f"- **Языки кода:** {_pairs(a['code_languages'])}; фрагментов кода со страниц: {a.get('code_snippets', 0)}")
    lines.append(f"- **Типы файлов:** {_pairs(a['file_types'], '.{0} ({1})', 25)}")
    lines.append(f"- **Ключевые слова:** {_pairs(a['keywords'], '{0}', 30)}")
    lines.append(f"- **Связанные сущности:** {_pairs(a['entities'], '{0} ({1})', 25)}")
    if a.get("official_sites"):
        lines.append(f"- **Официальные сайты:** {', '.join(a['official_sites'])}")
    if a.get("fandom_wikis"):
        lines.append(f"- **Фан-вики:** {', '.join(w + '.fandom.com' for w in a['fandom_wikis'])}")
    if a.get("timeline"):
        peak = sorted(a["timeline"], key=lambda x: -x[1])[:8]
        lines.append(f"- **Годы, чаще всего упоминаемые:** {_pairs(sorted(peak), '{0} ({1})', 8)}")
    if a.get("suggested_queries"):
        lines.append(f"- **Что ещё поискать:** {'; '.join(a['suggested_queries'])}")
    lines.append("")

    lines += ["## Источники", "", "| Источник | Статус | Найдено | Время, с | Ошибки |", "|---|---|---|---|---|"]
    for name, st in sorted(p["sources"].items()):
        errs = "; ".join(st.get("errors", []))[:160].replace("|", "/")
        lines.append(f"| {name} | {st.get('status')} | {st.get('count')} | {st.get('seconds')} | {errs} |")
    lines.append("")
    if p.get("notes"):
        lines += ["**Заметки:**", *[f"- {n}" for n in p["notes"]], ""]

    by_kind: dict[str, list[dict]] = {}
    for r in p["results"]:
        by_kind.setdefault(r["kind"], []).append(r)
    for kind in ALL_KINDS:
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines += [f"## {KIND_LABELS_RU.get(kind, kind)} ({len(items)})", ""]
        for r in items[:40]:
            title = r["title"].replace("[", "(").replace("]", ")")
            extra = []
            m = r.get("meta", {})
            for key in ("stars", "language", "views", "duration", "size", "upvotes", "points", "release_date"):
                if m.get(key):
                    extra.append(f"{key}: {m[key]}")
            if r.get("date"):
                extra.append(r["date"])
            line = f"- [{title}]({r['url']}) — `{r['domain']}` · score {r['score']}"
            if extra:
                line += " · " + ", ".join(map(str, extra))
            lines.append(line)
            if m.get("page_url"):
                lines.append(f"  - страница: {m['page_url']}")
            if r.get("snippet"):
                lines.append(f"  - {r['snippet'][:300]}")
        if len(items) > 40:
            lines.append(f"- … ещё {len(items) - 40} в results.json / report.html")
        lines.append("")
    if p.get("downloads"):
        ok = [d for d in p["downloads"] if "path" in d]
        lines += [f"## Скачанные файлы ({len(ok)})", ""]
        lines += [f"- `{d['path']}` ← {d['url']}" for d in ok[:200]]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

def md_to_html(md: str) -> str:
    """Tiny Markdown renderer for the AI summary (headings, lists, bold, links, code)."""
    out: list[str] = []
    in_list = False
    in_code = False
    for raw in md.splitlines():
        if raw.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
            else:
                out.append("<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(raw))
            continue
        line = html.escape(raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', line)
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        is_item = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)", line)
        if in_list and not is_item:
            out.append("</ul>")
            in_list = False
        if m:
            level = min(len(m.group(1)) + 2, 6)
            out.append(f"<h{level}>{m.group(2)}</h{level}>")
        elif is_item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{is_item.group(1)}</li>")
        elif line.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def render_html(p: dict[str, Any]) -> str:
    data = dict(p)
    data["ai_summary_html"] = md_to_html(p["ai_summary"]) if p.get("ai_summary") else ""
    data["kind_labels"] = KIND_LABELS_RU
    data["kinds"] = ALL_KINDS
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__TITLE__", html.escape(p["topic"])).replace("__DATA__", blob)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SearchMaster — __TITLE__</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2330;--muted:#5d6678;--line:#e2e5ea;--accent:#2f6fde;--accent-soft:#e7efff;
--ok:#1f8a4c;--warn:#b7791f;--bad:#c0392b;--code:#f1f3f6;--shadow:0 1px 2px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--panel:#171b23;--text:#e6e9ef;--muted:#98a2b3;
--line:#2a303b;--accent:#6ea0ff;--accent-soft:#1d2a44;--ok:#4cc38a;--warn:#e0a84f;--bad:#ff7b6b;--code:#11151c;--shadow:none}}
:root[data-theme="dark"]{--bg:#0f1218;--panel:#171b23;--text:#e6e9ef;--muted:#98a2b3;--line:#2a303b;--accent:#6ea0ff;
--accent-soft:#1d2a44;--ok:#4cc38a;--warn:#e0a84f;--bad:#ff7b6b;--code:#11151c;--shadow:none}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 60px}
header h1{margin:0 0 4px;font-size:26px}header .sub{color:var(--muted)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));margin:16px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;box-shadow:var(--shadow);min-width:0}
.card.wide{grid-column:1/-1}.card h3{margin:0 0 8px;font-size:14px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.stat{font-size:28px;font-weight:700}.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--accent-soft);color:var(--text);font-size:12.5px;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.chip.ok{background:color-mix(in srgb,var(--ok) 18%,transparent)}.chip.blocked,.chip.error{background:color-mix(in srgb,var(--bad) 18%,transparent)}
.chip.partial{background:color-mix(in srgb,var(--warn) 22%,transparent)}
.bars{display:flex;align-items:flex-end;gap:2px;height:90px;overflow-x:auto}.bars div{background:var(--accent);min-width:6px;flex:1;border-radius:2px 2px 0 0;position:relative}
.bars-labels{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}
.ai{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 18px;margin:16px 0}
.toolbar{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);background:var(--panel);color:var(--text);padding:6px 12px;border-radius:8px;cursor:pointer;font:inherit;font-size:14px}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
input[type=search],select{flex:1 1 220px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--text);font:inherit}
select{flex:0 0 auto}
.list{display:flex;flex-direction:column;gap:10px;margin-top:12px}
.item{display:flex;gap:12px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
.item img.th{width:150px;height:90px;object-fit:cover;border-radius:6px;background:var(--code);flex:0 0 auto}
.item .body{min-width:0;flex:1}.item .t{font-weight:600;font-size:15.5px;word-break:break-word}
.meta{color:var(--muted);font-size:12.5px;margin:2px 0 4px;display:flex;flex-wrap:wrap;gap:4px 10px}
.snip{font-size:14px;word-break:break-word}
details{margin-top:6px}summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{background:var(--code);padding:10px;border-radius:6px;overflow:auto;font-size:12.5px;max-height:320px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-top:12px}
.gal{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.gal img{width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--code);display:block}
.gal .cap{padding:6px 8px;font-size:12.5px;word-break:break-word}.gal .cap .meta{margin:0}
.kbadge{font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);color:var(--muted);text-transform:uppercase}
.empty{color:var(--muted);padding:30px;text-align:center}
.more{margin:16px auto;display:block}
@media (max-width:600px){.item{flex-direction:column}.item img.th{width:100%;height:160px}header h1{font-size:21px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>SearchMaster: <span id="topic"></span></h1>
  <div class="sub" id="sub"></div>
</header>
<div class="grid" id="stats"></div>
<div id="ai"></div>
<div class="grid" id="analytics"></div>
<div class="toolbar">
  <div id="tabs" class="chips"></div>
  <input type="search" id="q" placeholder="Фильтр по тексту, домену, источнику…">
  <select id="sort"><option value="score">По релевантности</option><option value="date">По дате</option><option value="sources">По числу источников</option></select>
</div>
<div id="view"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => /^https?:\/\//i.test(u || '') ? u : '#';
const A = D.analysis;
$('#topic').textContent = D.topic;
document.title = 'SearchMaster — ' + D.topic;
$('#sub').textContent = `${D.started.replace('T',' ')} → ${D.finished.replace('T',' ')} · запросов: ${D.http.requests} · из кеша: ${D.http.cache_hits}` + (D.aliases && D.aliases.length ? ' · синонимы: ' + D.aliases.join(', ') : '');

function card(title, inner, cls){ return `<div class="card ${cls||''}"><h3>${esc(title)}</h3>${inner}</div>`; }
function chips(list, fmt){ if(!list || !list.length) return '<span class="meta">—</span>'; return '<div class="chips">' + list.map(x => `<span class="chip">${fmt ? fmt(x) : esc(x)}</span>`).join('') + '</div>'; }

// --- top stats
const bk = A.by_kind || {};
$('#stats').innerHTML = [
  card('Всего релевантных', `<div class="stat">${A.total}</div><div class="meta">отфильтровано шума: ${A.filtered_out||0}, доменов: ${A.domains_count||0}</div>`),
  card('Скриншоты и изображения', `<div class="stat">${bk.image||0}</div><div class="meta">видео: ${bk.video||0}</div>`),
  card('Код', `<div class="stat">${(bk.repo||0)+(bk.code||0)}</div><div class="meta">репозиториев: ${bk.repo||0}, фрагментов: ${bk.code||0}, со страниц: ${A.code_snippets||0}</div>`),
  card('Файлы и архивы', `<div class="stat">${(bk.file||0)+(bk.archive||0)}</div><div class="meta">${Object.entries(A.file_categories||{}).filter(([k])=>k!=='image'&&k!=='video').map(([k,v])=>esc(k)+': '+v).join(', ')||'—'}</div>`),
  card('Источники (наведите, чтобы увидеть ошибки)', '<div class="chips">' + Object.entries(D.sources).map(([n,s]) =>
    `<span class="chip ${esc(s.status)}" title="${esc((s.errors||[]).join('\n') || s.status)}">${esc(n)}: ${s.count}${s.status!=='ok'?' ⚠ '+esc(s.status):''}</span>`).join('') + '</div>', 'wide')
].join('');

if (D.ai_summary_html) $('#ai').innerHTML = `<div class="ai"><h2>Аналитическая сводка (ИИ)</h2>${D.ai_summary_html}</div>`;

// --- analytics
const tl = A.timeline || [];
let tlHtml = '<span class="meta">—</span>';
if (tl.length){ const max = Math.max(...tl.map(x=>x[1])); tlHtml = `<div class="bars">${tl.map(([y,c])=>`<div style="height:${Math.max(4, c/max*100)}%" title="${y}: ${c}"></div>`).join('')}</div><div class="bars-labels"><span>${tl[0][0]}</span><span>${tl[tl.length-1][0]}</span></div>`; }
const pair = x => `${esc(x[0])} <b>${x[1]}</b>`;
const analytics = [
  A.developers && A.developers.length ? card('Разработчики / издатели', chips([...new Set([...A.developers, ...(A.publishers||[])].map(x=>x[0]))])) : '',
  card('Движки (упоминания)', chips(A.engines, pair)),
  A.repo_engines && A.repo_engines.length ? card('Технологии по файлам репозиториев', chips(A.repo_engines, pair)) : '',
  card('Платформы', chips(A.platforms, pair)),
  card('Хронология упоминаний', tlHtml),
  card('Ключевые слова', chips((A.keywords||[]).slice(0,30).map(x=>x[0]))),
  card('Связанные сущности', chips((A.entities||[]).slice(0,25), pair)),
  card('Типы файлов', chips((A.file_types||[]).slice(0,25), x=>`.${esc(x[0])} <b>${x[1]}</b>`)),
  card('Языки кода', chips(A.code_languages, pair)),
  card('Топ доменов', chips((A.top_domains||[]).slice(0,12), pair)),
  card('Что ещё поискать', chips(A.suggested_queries)),
  (D.notes && D.notes.length) ? card('Заметки', '<ul>' + D.notes.map(n=>`<li>${esc(n)}</li>`).join('') + '</ul>') : '',
].join('');
$('#analytics').innerHTML = analytics;

// --- tabs & results
const R = D.results;
const isCode = r => r.kind==='code' || r.kind==='repo' || (r.meta && r.meta.code_snippets && r.meta.code_snippets.length);
const TABS = [['all','Все',()=>true],['image','Скриншоты',r=>r.kind==='image'],['video','Видео',r=>r.kind==='video'],
  ['codes','Код',isCode],['file','Файлы',r=>r.kind==='file'],['wiki','Вики',r=>r.kind==='wiki'],
  ['discussion','Обсуждения',r=>r.kind==='discussion'],['page','Страницы',r=>r.kind==='page'],
  ['game','Игра',r=>r.kind==='game'],['archive','Архивы',r=>r.kind==='archive'],['news','Новости',r=>r.kind==='news'],
  ['paper','Наука',r=>r.kind==='paper'],['package','Пакеты',r=>r.kind==='package']];
let tab = 'all', limit = 120;
function counts(){ return Object.fromEntries(TABS.map(([k,,f])=>[k, R.filter(f).length])); }
function drawTabs(){ const c = counts(); $('#tabs').innerHTML = TABS.filter(([k])=>k==='all'||c[k]).map(([k,l])=>`<button class="tab ${k===tab?'active':''}" data-k="${k}">${l} ${c[k]}</button>`).join(''); }
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button'); if(!b) return; tab = b.dataset.k; limit = 120; drawTabs(); draw(); });
$('#q').addEventListener('input', () => { limit = 120; draw(); });
$('#sort').addEventListener('change', draw);

function metaLine(r){
  const m = r.meta || {}, bits = [`<span class="kbadge">${esc(D.kind_labels[r.kind]||r.kind)}</span>`, esc(r.domain)];
  if (r.date) bits.push(esc(r.date));
  for (const k of ['stars','language','views','duration','size','upvotes','points','comments','downloads','license','release_date','metacritic'])
    if (m[k]) bits.push(`${k}: ${esc(Array.isArray(m[k])?m[k].join(', '):m[k])}`);
  if (m.developers && m.developers.length) bits.push('разработчик: ' + esc(m.developers.join(', ')));
  bits.push(`score ${r.score}`);
  bits.push(`<span title="${esc(r.found_via.join('\n'))}">источников: ${r.found_via.length}</span>`);
  return `<div class="meta">${bits.join(' · ')}</div>`;
}
function itemHtml(r){
  const m = r.meta || {};
  let extra = '';
  if (m.page_url) extra += `<div class="meta">страница: <a href="${esc(safeUrl(m.page_url))}" target="_blank" rel="noopener">${esc(m.page_url)}</a></div>`;
  if (m.code_snippets && m.code_snippets.length) extra += `<details><summary>Фрагменты кода (${m.code_snippets.length})</summary>${m.code_snippets.map(c=>`<pre>${esc(c)}</pre>`).join('')}</details>`;
  if (m.tree) extra += `<details><summary>Структура репозитория (${m.tree.files} файлов)</summary><div class="meta">расширения: ${m.tree.top_extensions.map(x=>esc(x[0])+' '+x[1]).join(', ')}</div><div class="meta">технологии: ${esc((m.tree.engine_hints||[]).join(', ')||'—')}</div><pre>${esc((m.tree.notable||[]).join('\n'))}</pre></details>`;
  if (m.headings && m.headings.length) extra += `<details><summary>Оглавление страницы</summary><div class="meta">${m.headings.map(esc).join(' · ')}</div></details>`;
  if (r.content && r.content.length > (r.snippet||'').length + 200) extra += `<details><summary>Извлечённый текст</summary><pre>${esc(r.content)}</pre></details>`;
  if (m.local_path) extra += `<div class="meta">скачано: ${esc(m.local_path)}</div>`;
  const th = r.thumbnail ? `<img class="th" loading="lazy" referrerpolicy="no-referrer" src="${esc(safeUrl(r.thumbnail))}" alt="" onerror="this.remove()">` : '';
  return `<div class="item">${th}<div class="body"><div class="t"><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a></div>${metaLine(r)}<div class="snip">${esc(r.snippet||'')}</div>${extra}</div></div>`;
}
function galHtml(r){
  const m = r.meta || {}, src = r.thumbnail || r.url;
  const dims = m.width && m.height ? `${m.width}×${m.height} · ` : '';
  const link = m.page_url ? ` · <a href="${esc(safeUrl(m.page_url))}" target="_blank" rel="noopener">страница</a>` : '';
  return `<div class="gal"><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener"><img loading="lazy" referrerpolicy="no-referrer" src="${esc(safeUrl(src))}" alt="${esc(r.title)}" onerror="this.closest('.gal').style.display='none'"></a><div class="cap">${esc(r.title).slice(0,140)}<div class="meta">${dims}${esc(r.domain)}${link}</div></div></div>`;
}
function draw(){
  const q = $('#q').value.trim().toLowerCase();
  const f = TABS.find(t=>t[0]===tab)[2];
  let items = R.filter(f);
  if (q) items = items.filter(r => (r.title+' '+r.snippet+' '+r.url+' '+r.source+' '+(r.found_via||[]).join(' ')).toLowerCase().includes(q));
  const s = $('#sort').value;
  if (s==='date') items = items.slice().sort((a,b)=>(b.date||'').localeCompare(a.date||''));
  if (s==='sources') items = items.slice().sort((a,b)=>b.found_via.length-a.found_via.length);
  const shown = items.slice(0, limit);
  if (!items.length){ $('#view').innerHTML = '<div class="empty">Ничего не найдено</div>'; return; }
  const visual = tab==='image' || tab==='video';
  $('#view').innerHTML = (visual ? `<div class="gallery">${shown.map(galHtml).join('')}</div>` : `<div class="list">${shown.map(itemHtml).join('')}</div>`)
    + (items.length > limit ? `<button class="tab more" id="more">Показать ещё (${items.length-limit})</button>` : '');
  const more = $('#more'); if (more) more.onclick = () => { limit += 200; draw(); };
}
drawTabs(); draw();
</script>
</body>
</html>
"""
