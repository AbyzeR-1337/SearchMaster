// SearchMaster browser engine: sources, analysis, search orchestration.
// Pure JS without DOM access, so it also runs in Node for tests.

export const CATEGORIES = {
  game: "Игры",
  wiki: "Вики и справка",
  article: "Статьи и страницы",
  news: "Новости",
  image: "Изображения и скриншоты",
  video: "Видео",
  audio: "Аудио и музыка",
  code: "Исходный код",
  file: "Файлы и документы",
  mod: "Моды и фан-контент",
  discussion: "Обсуждения",
  paper: "Научные работы",
  book: "Книги",
};
export const CATEGORY_ORDER = Object.keys(CATEGORIES);

// "What to look for": groups of result categories.
export const ONLY_GROUPS = {
  image: { label: "скриншоты и картинки", cats: ["image"] },
  video: { label: "видео", cats: ["video"] },
  code: { label: "исходный код", cats: ["code"] },
  file: { label: "файлы и документы", cats: ["file", "paper", "book"] },
  mod: { label: "моды", cats: ["mod"] },
  discussion: { label: "обсуждения", cats: ["discussion"] },
  text: { label: "статьи и вики", cats: ["wiki", "article", "news", "paper", "book"] },
  audio: { label: "музыка и звук", cats: ["audio"] },
};

const NAME_SPLIT_RE = /\s+\/\s+|\s*\|\s*|\s*;\s*/;
const QUOTES = `"«»“”„'`;

// "Seven Souls Online / Седьмой элемент (игра, MMORPG)" -> names, context words per name, exact flag.
// Context words make an ambiguous name count only next to one of them (the song is not the game).
export function splitNames(query) {
  let q = String(query || "").trim();
  const exact = q.length > 2 && QUOTES.includes(q[0]) && QUOTES.includes(q[q.length - 1]);
  if (exact) q = q.slice(1, -1);
  const names = [];
  const contexts = {};
  const trimQ = (s) => s.trim().replace(new RegExp(`^[${QUOTES}]+|[${QUOTES}]+$`, "g"), "").trim();
  for (let part of q.split(NAME_SPLIT_RE)) {
    part = trimQ(part);
    const m = /^(.*?\S)\s*\(([^()]+)\)\s*$/.exec(part);
    let context = [];
    if (m) {
      part = trimQ(m[1]);
      context = m[2].split(/[,;]/).map((c) => c.trim()).filter(Boolean);
    }
    if (part && !names.some((n) => n.toLowerCase() === part.toLowerCase())) {
      names.push(part);
      if (context.length) contexts[part] = context;
    }
  }
  return { names: names.length ? names : [q], exact, contexts };
}

export const MODES = {
  quick: { limit: 10, deep: false },
  normal: { limit: 25, deep: false },
  deep: { limit: 40, deep: true, expansions: 5 },
  // everything: follow-ups for every name in its language and archives of the official sites
  max: { limit: 50, deep: true, expansions: 14, archives: true },
};

// ------------------------------------------------------------------ text
const STOPWORDS = new Set(`
a an and are as at be been but by can could did do does for from had has have he her his how i if in into is it its
just me more most my no not of on or our out so some such than that the their them then there these they this those
to too up us very was we were what when where which while who why will with would you your about after all also any
because before between both each few here him new now only other over own same she should through under until
again further once off above below during get got one two use used using may might must like via vs per
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня еще
нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая
много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно
всю между это также который которые которая которых свой своих
http https www com org net html htm php amp nbsp quot wiki wikipedia page edit file jpg png gif svg webp
since made next work works play complete beautiful data really great good way want need know thing things
les des une pour avec dans est sur par qui que pas plus del los las por para con una der die das und mit für
ist von den dem ein eine nicht auch`.split(/\s+/).filter(Boolean));

const NOISE_TERMS = new Set(`steam github gitlab codeberg fandom openverse archive internet reddit youtube modrinth
gamebanana speedrun arxiv wikimedia commons readme license screenshot скриншот image file files download view views
posted comments read more click here full article news новости подробнее читать game games игра игры игре игру года
году год англ рус still free content version release released world official best first make many much well see time
back page pages site online update updated назад обновлён сведения авторы`.split(/\s+/).filter(Boolean));

const WORD_RE = /[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu;
const ACRONYM_RE = /(?<![\p{L}\p{N}])((?:\p{L}\.){2,}\p{L}?)/gu; // S.T.A.L.K.E.R. -> stalker
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

export function tokenize(text) {
  if (!text) return [];
  const t = String(text).replace(ACRONYM_RE, (m) => m.replace(/\./g, ""));
  return (t.match(WORD_RE) || []).map((w) => w.toLowerCase());
}

export function stripHtml(s) {
  if (!s) return "";
  return String(s)
    .replace(/<[^>]+>/g, " ")
    .replace(/&(#x?[0-9a-f]+|\w+);/gi, (m, e) => {
      if (e[0] === "#") {
        const n = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : m;
      }
      return ENTITIES[e.toLowerCase()] ?? m;
    })
    .replace(/\s+/g, " ")
    .trim();
}

export function truncate(text, limit) {
  text = (text || "").trim();
  if (text.length <= limit) return text;
  let cut = text.slice(0, limit - 1);
  const sp = cut.lastIndexOf(" ");
  if (sp > limit * 0.6) cut = cut.slice(0, sp);
  return cut.replace(/[ ,.;:-]+$/, "") + "…";
}

export const stem = (t) => (t.length > 5 ? t.slice(0, Math.max(4, t.length - 2)) : t);
const tokenMatch = (a, b) => a === b || b.startsWith(stem(a)) || a.startsWith(stem(b));

export function similarity(a, b) {
  const ta = new Set(tokenize(a)), tb = new Set(tokenize(b));
  if (!ta.size || !tb.size) return 0;
  let matched = 0;
  for (const x of ta) if ([...tb].some((y) => tokenMatch(x, y))) matched++;
  return matched / (ta.size + tb.size - matched);
}

export function queryTerms(q) {
  const toks = tokenize(q);
  const terms = toks.filter((w) => (!STOPWORDS.has(w) || toks.length <= 2) && (w.length > 1 || /^\d+$/.test(w)));
  return terms.length ? terms : toks;
}

export const isLatin = (text) => {
  const letters = [...String(text)].filter((ch) => /\p{L}/u.test(ch));
  return letters.length > 0 && letters.every((ch) => /[a-z]/i.test(ch));
};

const contentWords = (text) => tokenize(text).filter((w) => w.length >= 3 && !STOPWORDS.has(w) && !/^\d+$/.test(w));

export function slugify(text) {
  const map = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l",
    м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh", щ: "sch",
    ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya" };
  const t = [...String(text).toLowerCase()].map((c) => map[c] ?? c).join("")
    .normalize("NFKD").replace(/[^\x00-\x7f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return t.slice(0, 60).replace(/-+$/, "") || "search";
}

export function yearOf(date) {
  const m = /(19[5-9]\d|20\d\d)/.exec(date || "");
  return m ? Number(m[1]) : null;
}

const tsToIso = (ts) => (ts ? new Date(Number(ts) * 1000).toISOString().slice(0, 10) : null);

// ------------------------------------------------------------------ urls & classification
const TRACK = /^(utm_|fbclid|gclid|yclid|mc_|ref$|ref_src|igshid|si$|feature$)/;

export function normalizeUrl(url) {
  try {
    const u = new URL(url);
    let host = u.hostname.toLowerCase().replace(/^www\./, "").replace(/^m\./, "").replace(/\.m\.wikipedia\.org$/, ".wikipedia.org");
    let path = u.pathname.replace(/\/+$/, "") || "/";
    if (host === "youtu.be") return `youtube.com/watch?v=${path.slice(1)}`;
    const params = [...u.searchParams].filter(([k]) => !TRACK.test(k)).sort((a, b) => (a[0] + a[1] < b[0] + b[1] ? -1 : 1));
    const q = new URLSearchParams(params).toString();
    return host + path + (q ? "?" + q : "");
  } catch {
    return url;
  }
}

const EXT = {
  image: new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"]),
  video: new Set([".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"]),
  audio: new Set([".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus"]),
  file: new Set([".pdf", ".txt", ".md", ".doc", ".docx", ".epub", ".csv", ".json", ".zip", ".7z", ".rar", ".tar", ".gz"]),
};
const HOSTS = [
  ["mod", ["nexusmods.com", "moddb.com", "gamebanana.com", "modrinth.com", "curseforge.com", "thunderstore.io", "steamcommunity.com/sharedfiles"]],
  ["code", ["github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "sourceforge.net", "sr.ht", "gitee.com"]],
  ["video", ["youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "rutube.ru", "vk.com/video", "dailymotion.com"]],
  ["wiki", ["wikipedia.org", "fandom.com", "wiki.gg", "wikia.com", "pcgamingwiki.com", "wiki"]],
  ["discussion", ["reddit.com", "stackexchange.com", "stackoverflow.com", "news.ycombinator.com", "forum", "discord", "habr.com", "pikabu.ru", "dtf.ru"]],
  ["news", ["news", "ign.com", "gamespot.com", "kotaku.com", "pcgamer.com", "polygon.com", "eurogamer", "stopgame.ru", "igromania.ru"]],
];

export function urlExt(url) {
  try {
    const m = /\.[a-z0-9]{1,5}$/i.exec(new URL(url).pathname);
    return m ? m[0].toLowerCase() : "";
  } catch {
    return "";
  }
}

export function classifyUrl(url) {
  const ext = urlExt(url);
  for (const [cat, set] of Object.entries(EXT)) if (set.has(ext)) return cat;
  const hp = String(url).toLowerCase().split("://").pop();
  for (const [cat, hosts] of HOSTS) if (hosts.some((h) => hp.includes(h))) return cat;
  if (/arxiv\.org|doi\.org|scholar/.test(hp)) return "paper";
  return "article";
}

// ------------------------------------------------------------------ results
export function makeResult(source, r) {
  let url = r.url || "";
  try {
    url = new URL(url).href;
  } catch {
    return null;
  }
  if (!/^https?:/.test(url)) return null;
  return {
    url,
    title: String(r.title || url).trim(),
    source,
    category: CATEGORIES[r.category] ? r.category : "article",
    snippet: (r.snippet || "").trim(),
    thumbnail: r.thumbnail || null,
    date: r.date || null,
    author: r.author || null,
    meta: r.meta || {},
    score: 0,
    sources: [source],
  };
}

export function mergeResults(list) {
  const merged = new Map();
  for (const r of list) {
    if (!r) continue;
    const key = normalizeUrl(r.url);
    const cur = merged.get(key);
    if (!cur) {
      merged.set(key, { ...r, sources: [...r.sources], meta: { ...r.meta } });
      continue;
    }
    for (const s of r.sources) if (!cur.sources.includes(s)) cur.sources.push(s);
    if ((r.snippet || "").length > (cur.snippet || "").length) cur.snippet = r.snippet;
    cur.thumbnail = cur.thumbnail || r.thumbnail;
    cur.date = cur.date || r.date;
    cur.author = cur.author || r.author;
    for (const [k, v] of Object.entries(r.meta || {})) if (!(k in cur.meta)) cur.meta[k] = v;
  }
  return [...merged.values()];
}

// ------------------------------------------------------------------ scoring
function popularity(meta) {
  let best = 0;
  for (const k of ["stars", "downloads", "points", "score", "views", "comments", "likes", "editions"]) {
    const v = meta[k];
    if (typeof v === "number" && v > 0) best = Math.max(best, Math.log10(v + 1));
  }
  return Math.min(best / 5, 1);
}

function recency(date) {
  const y = yearOf(date);
  return y ? Math.max(0, 1 - Math.max(0, new Date().getFullYear() - y) / 15) : 0;
}

const requiredCoverage = (n) => (n <= 2 ? 1 : n === 3 ? 0.66 : 0.6);

export function scoreResult(r, terms, phrase, weights = {}, exact = false, context = null) {
  if (!terms.length) return 0.5;
  const stems = terms.map(stem);
  const titleT = new Set(tokenize(r.title));
  const snipT = new Set(tokenize(r.snippet));
  const urlLow = r.url.toLowerCase();
  const squashedUrl = urlLow.replace(/[-_]/g, "");
  const coverage = (tokens) => {
    let hit = 0;
    terms.forEach((t, i) => {
      if (tokens.has(t)) hit++;
      else for (const tok of tokens) if (tok.startsWith(stems[i])) { hit++; break; }
    });
    return hit / terms.length;
  };
  const titleCov = coverage(titleT);
  const textCov = coverage(snipT);
  const urlCov = stems.filter((s) => urlLow.includes(s)).length / terms.length;
  const allCov = coverage(new Set([...titleT, ...snipT]));
  const hay = ` ${tokenize(`${r.title} ${r.snippet}`).join(" ")} `;
  const phraseFound = terms.length > 1 && (hay.includes(` ${phrase} `) || squashedUrl.includes(phrase.replace(/ /g, "")));

  let relevance = 0.5 * titleCov + 0.3 * textCov + 0.1 * urlCov + (phraseFound ? 0.25 : 0);
  // exact search: the name must appear as a whole, no stems or partial matches
  let onTopic = exact
    ? (terms.length > 1 ? phraseFound : hay.includes(` ${terms[0]} `) || squashedUrl.includes(terms[0]))
    : phraseFound || Math.max(allCov, urlCov) >= requiredCoverage(terms.length);
  if (onTopic && context?.length) {
    // an ambiguous name counts only next to one of its context words
    const all = [...titleT, ...snipT];
    onTopic = context.some((c) => all.some((tok) => tok === c || tok.startsWith(stem(c))));
  }
  if (r.meta.trusted) {
    relevance = Math.max(relevance, 0.6);
    onTopic = true;
  }
  if (r.meta.summary || r.meta.game_card) relevance += 0.4;
  let authority = Math.max(...r.sources.map((s) => weights[s] ?? 1));
  if (r.meta.kind === "file") authority *= 0.85;
  const multi = Math.min(r.sources.length - 1, 3) * 0.08;
  let score = relevance * authority * (1 + 0.3 * popularity(r.meta)) + 0.05 * recency(r.date) + multi;
  if (!onTopic) score *= exact ? 0.05 : 0.25;
  return Math.round(score * 10000) / 10000;
}

export function scoreAll(results, queries, weights = {}, exact = false, contexts = {}) {
  const variants = queries.filter((q) => q && q.trim())
    .map((q) => [queryTerms(q), tokenize(q).join(" "), (contexts[q] || []).flatMap((c) => tokenize(c))])
    .filter(([t]) => t.length);
  for (const r of results) r.score = Math.max(...variants.map(([t, p, c]) => scoreResult(r, t, p, weights, exact, c)));
  results.sort((a, b) => b.score - a.score);
  return results;
}

export function relatedTerms(results, queries, top = 30) {
  const qt = tokenize(queries.join(" "));
  const qset = new Set([...qt, qt.join(""), ...NOISE_TERMS]);
  const usable = (w) => !qset.has(w) && !/['’]/.test(w) && !w.split(/[-']/).filter(Boolean).every((p) => qset.has(p));
  const counter = new Map();
  const domains = new Map();
  for (const r of results.slice(0, 300)) {
    const words = contentWords(`${r.title}. ${r.snippet}`).filter(usable);
    const doc = new Set(words);
    for (let i = 0; i + 1 < words.length; i++) if (words[i] !== words[i + 1]) doc.add(`${words[i]} ${words[i + 1]}`);
    let host = "";
    try { host = new URL(r.url).hostname; } catch {}
    for (const t of doc) {
      counter.set(t, (counter.get(t) || 0) + 1);
      if (!domains.has(t)) domains.set(t, new Set());
      domains.get(t).add(host);
    }
  }
  const hostCount = new Set(results.map((r) => { try { return new URL(r.url).hostname; } catch { return ""; } })).size;
  const minDf = results.length > 10 ? 2 : 1;
  const minSites = hostCount > 3 ? 2 : 1;
  const items = [...counter].filter(([t, c]) => c >= minDf && domains.get(t).size >= minSites);
  items.sort((a, b) => b[1] * (b[0].includes(" ") ? 2 : 1) - a[1] * (a[0].includes(" ") ? 2 : 1) || b[0].length - a[0].length);
  const phrases = items.filter(([t]) => t.includes(" ")).slice(0, top * 2);
  const picked = [];
  for (const [t, c] of items) {
    if (!t.includes(" ") && phrases.some(([p, pc]) => p.split(" ").includes(t) && pc >= 0.5 * c)) continue;
    picked.push([t, c]);
    if (picked.length >= top) break;
  }
  return picked;
}

export function computeStats(results) {
  const count = (fn) => {
    const m = new Map();
    for (const r of results) {
      const k = fn(r);
      if (k) m.set(k, (m.get(k) || 0) + 1);
    }
    return [...m].sort((a, b) => b[1] - a[1]);
  };
  return {
    total: results.length,
    byCategory: Object.fromEntries(CATEGORY_ORDER.map((c) => [c, results.filter((r) => r.category === c).length]).filter(([, n]) => n)),
    domains: count((r) => { try { return new URL(r.url).hostname.replace(/^www\./, ""); } catch { return null; } }).slice(0, 12),
    years: count((r) => yearOf(r.date)).sort((a, b) => a[0] - b[0]),
    languages: count((r) => (r.category === "code" ? r.meta.language : null)).slice(0, 8),
    licenses: count((r) => r.meta.license).slice(0, 8),
  };
}

// ------------------------------------------------------------------ HTTP
const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

export function qs(base, params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.append(k, v);
  }
  return `${base}?${u}`;
}

// Identical requests within a few minutes share one network call (the subject lookup and the
// Wikipedia source ask the same thing; repeated searches stay light on the APIs).
const memo = new Map();
const MEMO_TTL = 10 * 60 * 1000;

export function getJSON(url, opts = {}) {
  const key = url + "|" + (opts.headers?.Authorization ? "auth" : "");
  const hit = memo.get(key);
  if (hit && Date.now() - hit.at < MEMO_TTL) return hit.promise;
  const promise = fetchJSON(url, opts);
  memo.set(key, { at: Date.now(), promise });
  promise.catch(() => memo.delete(key));
  if (memo.size > 400) memo.delete(memo.keys().next().value);
  return promise;
}

async function fetchJSON(url, { headers = {}, timeout = 12000, retries = 1 } = {}) {
  for (let attempt = 0; ; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    let resp;
    try {
      resp = await fetch(url, { headers, signal: ctl.signal });
    } catch (e) {
      clearTimeout(timer);
      if (e.name === "AbortError") throw Object.assign(new Error("сайт не ответил вовремя"), { timeout: true }); // a slow site stays slow
      if (attempt < retries) { await sleep(1200); continue; }
      throw new Error("сайт недоступен или не разрешает запросы из браузера");
    }
    clearTimeout(timer);
    if ((resp.status === 429 || resp.status >= 500) && attempt < retries) {
      await sleep(1500 * (attempt + 1));
      continue;
    }
    if (!resp.ok) {
      const err = new Error(resp.status === 429 ? "слишком много запросов, попробуйте позже" :
        resp.status === 403 ? "доступ ограничен (лимит запросов)" : `ошибка ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return resp.json();
  }
}

// ------------------------------------------------------------------ Wikimedia helpers
const wikiUrl = (base, title) => `${base}/wiki/${encodeURIComponent(title.replace(/ /g, "_"))}`;
export const commonsFile = (name, width = 640) =>
  `https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(name.replace(/ /g, "_"))}?width=${width}`;

async function mediawikiSearch(base, q, limit, label, apiPath = "/w/api.php") {
  const d = await getJSON(qs(`${base}${apiPath}`, {
    action: "query", list: "search", srsearch: q, srlimit: Math.min(limit, 50), srprop: "snippet|timestamp",
    format: "json", origin: "*",
  }));
  return (d.query?.search || []).map((it) => ({
    url: wikiUrl(base, it.title), title: it.title, category: "wiki", snippet: stripHtml(it.snippet),
    date: (it.timestamp || "").slice(0, 10) || null, meta: { site: label, page: it.title },
  }));
}

async function mediawikiImages(api, q, limit, label) {
  const d = await getJSON(qs(api, {
    action: "query", generator: "search", gsrsearch: q, gsrnamespace: 6, gsrlimit: Math.min(limit, 50),
    prop: "imageinfo", iiprop: "url|mime|extmetadata", iiurlwidth: 400, format: "json", origin: "*",
  }));
  const pages = Object.values(d.query?.pages || {}).sort((a, b) => (a.index || 0) - (b.index || 0));
  return pages.map((p) => {
    const info = (p.imageinfo || [])[0] || {};
    if (!info.url) return null;
    const mime = info.mime || "";
    const ext = info.extmetadata || {};
    return {
      url: info.descriptionurl || info.url,
      title: (p.title || "").replace(/^(File|Файл):/, ""),
      category: mime.startsWith("image/") ? "image" : mime.startsWith("video/") ? "video" : mime.startsWith("audio/") ? "audio" : "file",
      snippet: truncate(stripHtml(ext.ImageDescription?.value), 300),
      thumbnail: info.thumburl || info.url,
      author: stripHtml(ext.Artist?.value) || null,
      meta: { file_url: info.url, license: ext.LicenseShortName?.value, site: label },
    };
  }).filter(Boolean);
}

// ------------------------------------------------------------------ subject identification (Wikipedia + Wikidata)
const FACT_PROPS = [
  ["P31", "Что это"], ["P178", "Разработчик"], ["P123", "Издатель"], ["P577", "Дата выхода"], ["P571", "Создано"],
  ["P136", "Жанр"], ["P400", "Платформы"], ["P404", "Режимы"], ["P408", "Движок"], ["P57", "Режиссёр"],
  ["P86", "Композитор"], ["P50", "Автор"], ["P170", "Создатель"], ["P112", "Основатель"], ["P179", "Серия"],
  ["P155", "Предыдущая часть"], ["P156", "Следующая часть"], ["P495", "Страна"], ["P17", "Страна"],
  ["P277", "Язык программирования"], ["P275", "Лицензия"], ["P159", "Штаб-квартира"],
];
const LINK_PROPS = [
  ["P856", "Официальный сайт", (v) => v],
  ["P1733", "Steam", (v) => `https://store.steampowered.com/app/${v}/`],
  ["P2725", "GOG", (v) => `https://www.gog.com/${v}`],
  ["P6337", "PCGamingWiki", (v) => `https://www.pcgamingwiki.com/wiki/${v}`],
  ["P5794", "IGDB", (v) => `https://www.igdb.com/games/${v}`],
  ["P1933", "MobyGames", (v) => `https://www.mobygames.com/game/${v}`],
  ["P1324", "Исходный код", (v) => v],
  ["P3984", "Reddit", (v) => `https://www.reddit.com/r/${v}/`],
  ["P7294", "itch.io", (v) => v],
  ["P6262", "Fandom", (v) => { const [w, a] = String(v).split(":"); const [l, s] = w.includes(".") ? w.split(".") : [null, w]; return `https://${s}.fandom.com/${l ? l + "/" : ""}wiki/${a || ""}`; }],
];

function wdTime(v) {
  const m = /^([+-]\d+)-(\d\d)-(\d\d)/.exec(v.time || "");
  if (!m) return null;
  const y = String(Number(m[1]));
  if (v.precision >= 11) return `${m[3]}.${m[2]}.${y}`;
  if (v.precision === 10) return `${m[2]}.${y}`;
  return y;
}

export async function identify(query, ctx) {
  const langs = ctx.langs;
  // 1. best matching Wikipedia article in any of the languages
  const perLang = await Promise.all(langs.map(async (lang, li) => {
    try {
      const base = `https://${lang}.wikipedia.org`;
      const found = await mediawikiSearch(base, query, ctx.limit || 10, `Википедия (${lang})`);
      // earlier search positions and the first language win ties
      return found.slice(0, 5).map((f, i) => {
        // "Celeste (video game)" should match "Celeste" as well as the bare "Celeste" page does,
        // otherwise a longer unrelated title ("Mary Celeste") wins on similarity
        const bare = f.meta.page.replace(/\s*\([^)]*\)\s*$/, "");
        const qualified = bare !== f.meta.page;
        const sim = Math.max(similarity(f.meta.page, query), similarity(bare, query) - (qualified ? 0.05 : 0));
        return { base, page: f.meta.page, sim: sim - i * 0.02 - li * 0.01 };
      }).filter((c) => c.sim >= 0.28);
    } catch { return []; }
  }));
  const candidates = perLang.flat().sort((a, b) => b.sim - a.sim).slice(0, 4);
  const subject = { title: query, description: "", extract: "", image: null, wikiUrl: null, qid: null, facts: [], links: [],
    aliases: [], isGame: false, steamAppId: null, fandom: null, results: [] };

  let hit = null;
  for (const c of candidates) {
    try {
      const s = await getJSON(`${c.base}/api/rest_v1/page/summary/${encodeURIComponent(c.page.replace(/ /g, "_"))}`);
      if (s.type === "disambiguation") continue; // a list of meanings is not the subject
      hit = c;
      subject.title = s.title || c.page;
      subject.description = s.description || "";
      subject.extract = s.extract || "";
      subject.image = s.thumbnail?.source || s.originalimage?.source || null;
      subject.wikiUrl = s.content_urls?.desktop?.page || wikiUrl(c.base, c.page);
      break;
    } catch {
      hit = c;
      subject.title = c.page;
      subject.wikiUrl = wikiUrl(c.base, c.page);
      break;
    }
  }

  if (hit) {
    try {
      const d = await getJSON(qs(`${hit.base}/w/api.php`, { action: "query", prop: "pageprops|langlinks", titles: hit.page,
        ppprop: "wikibase_item", lllimit: 500, format: "json", origin: "*", redirects: 1 }));
      const p = Object.values(d.query?.pages || {})[0] || {};
      subject.qid = p.pageprops?.wikibase_item || null;
      for (const l of p.langlinks || []) if (["en", "ru", ...langs].includes(l.lang)) subject.aliases.push(l["*"]);
    } catch {}
  }
  // 2. Wikidata (also when Wikipedia had nothing)
  try {
    if (!subject.qid) subject.qid = await wikidataLookup(query, langs);
    if (subject.qid) await fillWikidata(subject, langs);
  } catch {}
  if (!hit && !subject.qid) return null;

  // 3. results derived from the subject
  if (subject.wikiUrl) {
    subject.results.push(makeResult("wikipedia", { url: subject.wikiUrl, title: subject.title, category: "wiki",
      snippet: subject.extract, thumbnail: subject.image, meta: { summary: true, site: "Википедия" } }));
  }
  if (subject.steamAppId) {
    const id = subject.steamAppId;
    const cdn = `https://cdn.akamai.steamstatic.com/steam/apps/${id}`;
    subject.results.push(makeResult("steam", { url: `https://store.steampowered.com/app/${id}/`, title: subject.title,
      category: "game", snippet: subject.description, thumbnail: `${cdn}/header.jpg`,
      meta: { game_card: true, trusted: true, site: "Steam", appid: id } }));
    for (const [file, label] of [["library_hero.jpg", "фон библиотеки"], ["capsule_616x353.jpg", "обложка"], ["header.jpg", "шапка"]]) {
      subject.results.push(makeResult("steam", { url: `${cdn}/${file}`, title: `${subject.title} — ${label} Steam`,
        category: "image", thumbnail: `${cdn}/${file}`, meta: { trusted: true, file_url: `${cdn}/${file}`, site: "Steam" } }));
    }
  }
  for (const l of subject.links) {
    if (l.label === "Steam") continue;
    subject.results.push(makeResult("wikidata", { url: l.url, title: `${subject.title} — ${l.label}`, category: classifyUrl(l.url),
      meta: { trusted: true, site: l.label } }));
  }
  subject.results = subject.results.filter(Boolean);
  const seen = new Set([query.toLowerCase()]);
  subject.aliases = subject.aliases.filter((a) => {
    const k = a.toLowerCase();
    if (seen.has(k) || similarity(a, query) >= 1) return false;
    seen.add(k);
    return true;
  }).sort((a, b) => Number(isLatin(b)) - Number(isLatin(a))).slice(0, 3);
  return subject;
}

// Find the Wikidata item for a query when Wikipedia gave nothing: label prefix search first,
// then full-text search, checking every label and alias ("Сталкер" vs "S.T.A.L.K.E.R.").
async function wikidataLookup(query, langs) {
  const api = "https://www.wikidata.org/w/api.php";
  const ids = [];
  for (const lang of [...new Set([langs[0] || "en", "en"])]) {
    try {
      const d = await getJSON(qs(api, { action: "wbsearchentities", search: query, language: lang, uselang: lang, limit: 5, format: "json", origin: "*" }));
      const best = (d.search || []).find((x) => similarity(x.label || "", query) >= 0.5 || (x.aliases || []).some((a) => similarity(a, query) >= 0.5));
      if (best) return best.id;
      ids.push(...(d.search || []).map((x) => x.id));
    } catch {}
  }
  try {
    const d = await getJSON(qs(api, { action: "query", list: "search", srsearch: query, srlimit: 5, format: "json", origin: "*" }));
    ids.push(...(d.query?.search || []).map((x) => x.title).filter((t) => /^Q\d+$/.test(t)));
  } catch {}
  const unique = [...new Set(ids)].slice(0, 10);
  if (!unique.length) return null;
  const languages = [...new Set([...langs, "en", "ru"])].join("|");
  const d = await getJSON(qs(api, { action: "wbgetentities", ids: unique.join("|"), props: "labels|aliases", languages, format: "json", origin: "*" }));
  let best = null, bestSim = 0.45; // at least about half of the words must match a label or alias
  for (const id of unique) {
    const e = d.entities?.[id];
    if (!e) continue;
    const names = [...Object.values(e.labels || {}).map((l) => l.value), ...Object.values(e.aliases || {}).flat().map((a) => a.value)];
    const sim = Math.max(0, ...names.map((n) => similarity(n, query)));
    if (sim > bestSim) { best = id; bestSim = sim; }
  }
  return best;
}

async function fillWikidata(subject, langs) {
  const languages = [...new Set([...langs, "en", "ru"])].join("|");
  const d = await getJSON(qs("https://www.wikidata.org/w/api.php", { action: "wbgetentities", ids: subject.qid,
    props: "labels|descriptions|claims|sitelinks", languages, format: "json", origin: "*" }));
  const e = d.entities?.[subject.qid];
  if (!e) return;
  const pick = (obj) => { for (const l of [...langs, "en", "ru"]) if (obj?.[l]) return obj[l].value; return ""; };
  if (!subject.description) subject.description = pick(e.descriptions);
  if (!subject.wikiUrl) {
    // Wikipedia was unreachable or had no match: take the name and article link from Wikidata
    subject.title = pick(e.labels) || subject.title;
    for (const l of [...langs, "en"]) {
      const sl = e.sitelinks?.[`${l}wiki`];
      if (sl) { subject.wikiUrl = wikiUrl(`https://${l}.wikipedia.org`, sl.title); break; }
    }
  }
  for (const l of Object.values(e.labels || {})) subject.aliases.push(l.value);
  for (const key of ["enwiki", "ruwiki"]) if (e.sitelinks?.[key]) subject.aliases.push(e.sitelinks[key].title);
  const claims = e.claims || {};
  const values = (p) => (claims[p] || []).map((c) => c.mainsnak?.datavalue?.value).filter((v) => v !== undefined);
  subject.isGame = values("P31").some((v) => ["Q7889", "Q16070115", "Q4393107"].includes(v.id));
  subject.steamAppId = values("P1733")[0] || null;
  const img = values("P18")[0] || values("P154")[0];
  if (!subject.image && img) subject.image = commonsFile(img, 480);
  const fandom = values("P6262").find((v) => !/gamicus|databank|wikia-ru|community/.test(v));
  if (fandom) subject.fandom = String(fandom).split(":")[0];
  for (const [p, label, fmt] of LINK_PROPS) {
    const v = values(p)[0];
    if (v && !(p === "P6262" && !fandom)) subject.links.push({ label, url: fmt(p === "P6262" ? fandom : v) });
  }
  // resolve item ids used in facts to labels
  const ids = new Set();
  for (const [p] of FACT_PROPS) for (const v of values(p).slice(0, 5)) if (v?.id) ids.add(v.id);
  let labels = {};
  if (ids.size) {
    const ld = await getJSON(qs("https://www.wikidata.org/w/api.php", { action: "wbgetentities", ids: [...ids].slice(0, 50).join("|"),
      props: "labels", languages, format: "json", origin: "*" }));
    labels = Object.fromEntries(Object.entries(ld.entities || {}).map(([id, ent]) => [id, pick(ent.labels)]));
  }
  const usedLabels = new Set();
  for (const [p, label] of FACT_PROPS) {
    if (usedLabels.has(label)) continue;
    let raw = values(p);
    // dates: only the first (earliest) release, not every port and re-release
    if (raw[0]?.time) raw = [...raw].sort((a, b) => (a.time < b.time ? -1 : 1)).slice(0, 1);
    const vals = raw.slice(0, 5).map((v) => (v?.id ? labels[v.id] : v?.time ? wdTime(v) : typeof v === "string" ? v : null)).filter(Boolean);
    const unique = [...new Set(vals)];
    if (unique.length) {
      subject.facts.push({ label, value: unique.join(", ") });
      usedLabels.add(label);
    }
  }
}

// ------------------------------------------------------------------ sources
const gh = (ctx) => {
  const h = { Accept: "application/vnd.github+json" };
  if (ctx.settings.githubToken) h.Authorization = `Bearer ${ctx.settings.githubToken}`;
  return h;
};

export const SOURCES = [
  {
    name: "wikipedia", title: "Википедия", phrase: true, cats: ["wiki"], weight: 1.3, expandable: true,
    async search(q, ctx) {
      const out = [], errs = [];
      await Promise.all(ctx.langs.map(async (lang) => {
        try { out.push(...await mediawikiSearch(`https://${lang}.wikipedia.org`, q, ctx.limit, `Википедия (${lang})`)); }
        catch (e) { errs.push(`${lang}: ${e.message}`); }
      }));
      if (!out.length && errs.length) throw new Error(errs.join("; "));
      return out;
    },
  },
  {
    name: "fandom", title: "Fandom-вики", phrase: true, cats: ["wiki", "image", "video"], weight: 1.2,
    async search(q, ctx) {
      const subject = await ctx.subject;
      const words = tokenize(q).filter((w) => !STOPWORDS.has(w));
      const longest = words.sort((a, b) => b.length - a.length)[0] || "";
      const candidates = [...new Set([subject?.fandom, slugify(q).replace(/-/g, ""), slugify(q), slugify(longest)].filter((x) => x && x.length >= 3))];
      for (const sub of candidates) {
        if (sub !== subject?.fandom) {
          try {
            const si = await getJSON(qs(`https://${sub}.fandom.com/api.php`, { action: "query", meta: "siteinfo", siprop: "general", format: "json", origin: "*" }));
            const name = si.query?.general?.sitename || "";
            const bare = name.replace(/\b(wiki|вики|fandom)\b/gi, " ");
            // "Online Wiki" must not count as the wiki of "Seven Souls Online"
            if (similarity(bare, q.replace(/"/g, "")) < 0.5 && !tokenize(name).join("").includes(slugify(q).replace(/-/g, ""))) continue;
          } catch { continue; }
        }
        const out = [];
        for (const lang of [...new Set(["en", ...ctx.langs])]) {
          const base = `https://${sub}.fandom.com${lang === "en" ? "" : "/" + lang}`;
          try {
            const pages = await mediawikiSearch(base, q, ctx.limit, `${sub}.fandom (${lang})`, "/api.php");
            const imgs = await mediawikiImages(`${base}/api.php`, q, Math.max(6, ctx.limit / 2), `${sub}.fandom`).catch(() => []);
            for (const r of [...pages, ...imgs]) { r.meta.trusted = true; out.push(r); }
          } catch {}
        }
        if (out.length) return out;
      }
      return [];
    },
  },
  {
    name: "commons", title: "Wikimedia Commons", phrase: true, cats: ["image", "video", "audio", "file"], weight: 1.1, expandable: true,
    search: (q, ctx) => mediawikiImages("https://commons.wikimedia.org/w/api.php", q, ctx.limit, "Commons"),
  },
  {
    name: "github", title: "GitHub", phrase: true, cats: ["code", "discussion"], weight: 1.2, expandable: true,
    async search(q, ctx) {
      const headers = gh(ctx);
      const repos = await getJSON(qs("https://api.github.com/search/repositories", { q, sort: "stars", order: "desc", per_page: Math.min(ctx.limit, 50) }), { headers });
      const out = (repos.items || []).map((r) => ({
        url: r.html_url, title: r.full_name, category: "code", snippet: r.description || "",
        thumbnail: `https://opengraph.githubassets.com/1/${r.full_name}`, date: (r.pushed_at || "").slice(0, 10), author: r.owner?.login,
        meta: { stars: r.stargazers_count, language: r.language, license: r.license?.spdx_id, repo: r.full_name, site: "GitHub" },
      }));
      if (ctx.pass !== "first") return out;
      const extra = [getJSON(qs("https://api.github.com/search/issues", { q, sort: "reactions", per_page: 20 }), { headers })
        .then((d) => (d.items || []).map((i) => ({
          url: i.html_url, title: i.title, category: "discussion", snippet: truncate(i.body || "", 300),
          date: (i.created_at || "").slice(0, 10), author: i.user?.login,
          meta: { comments: i.comments, site: i.repository_url?.replace("https://api.github.com/repos/", "") },
        })))];
      const perRepo = {};
      if (ctx.settings.githubToken) {
        extra.push(getJSON(qs("https://api.github.com/search/code", { q, per_page: 20 }), { headers: { ...headers, Accept: "application/vnd.github.text-match+json" } })
          .then((d) => (d.items || []).filter((i) => {
            // one repository must not flood the results (e.g. 30 files of an asset folder)
            const repo = i.repository?.full_name || "";
            perRepo[repo] = (perRepo[repo] || 0) + 1;
            return perRepo[repo] <= 2;
          }).map((i) => ({
            url: i.html_url, title: `${i.path} — ${i.repository?.full_name}`, category: "code",
            snippet: truncate((i.text_matches || []).map((m) => m.fragment).join(" … "), 300), meta: { kind: "file", site: "GitHub (код)" },
          }))));
      }
      for (const r of await Promise.allSettled(extra)) if (r.status === "fulfilled") out.push(...r.value);
      return out;
    },
  },
  {
    name: "gitlab", title: "GitLab", phrase: false, cats: ["code"],
    async search(q, ctx) {
      const d = await getJSON(qs("https://gitlab.com/api/v4/projects", { search: q, order_by: "star_count", sort: "desc", per_page: Math.min(ctx.limit, 50), simple: "true" }));
      return d.map((p) => ({ url: p.web_url, title: p.name_with_namespace, category: "code", snippet: p.description || "",
        thumbnail: p.avatar_url, date: (p.last_activity_at || "").slice(0, 10), meta: { stars: p.star_count, site: "GitLab" } }));
    },
  },
  {
    name: "codeberg", title: "Codeberg", phrase: false, cats: ["code"],
    async search(q, ctx) {
      const d = await getJSON(qs("https://codeberg.org/api/v1/repos/search", { q, limit: Math.min(ctx.limit, 50), sort: "stars", order: "desc" }));
      return (d.data || []).map((r) => ({ url: r.html_url, title: r.full_name, category: "code", snippet: r.description || "",
        date: (r.updated_at || "").slice(0, 10), meta: { stars: r.stars_count, language: r.language, site: "Codeberg" } }));
    },
  },
  {
    name: "archive", title: "Internet Archive", phrase: true, cats: ["file", "image", "video", "audio", "article"], weight: 1.15, expandable: true,
    async search(q, ctx) {
      // Programs/games are left out on purpose: for commercial titles they are usually unauthorized copies.
      const d = await getJSON(qs("https://archive.org/advancedsearch.php", { q: `(${q}) AND NOT mediatype:software`,
        "fl[]": ["identifier", "title", "description", "mediatype", "date", "downloads", "creator"], "sort[]": "downloads desc",
        rows: Math.min(ctx.limit * 2, 80), output: "json" }));
      const cat = { texts: "file", data: "file", collection: "file", image: "image", movies: "video", audio: "audio", etree: "audio", web: "article" };
      return (d.response?.docs || []).map((doc) => {
        const arr = (v) => (Array.isArray(v) ? v.join(" ") : v || "");
        return { url: `https://archive.org/details/${doc.identifier}`, title: arr(doc.title) || doc.identifier,
          category: cat[doc.mediatype] || "file", snippet: truncate(stripHtml(arr(doc.description)), 300),
          thumbnail: `https://archive.org/services/img/${doc.identifier}`, date: (doc.date || "").slice(0, 10) || null,
          author: arr(doc.creator).slice(0, 80) || null, meta: { downloads: doc.downloads, site: "Internet Archive" } };
      });
    },
  },
  {
    name: "openverse", title: "Openverse", phrase: false, cats: ["image", "audio"], expandable: true,
    async search(q, ctx) {
      const [img, aud] = await Promise.allSettled([
        getJSON(qs("https://api.openverse.org/v1/images/", { q, page_size: Math.min(ctx.limit, 20) })),
        getJSON(qs("https://api.openverse.org/v1/audio/", { q, page_size: 10 })),
      ]);
      if (img.status === "rejected" && aud.status === "rejected") throw img.reason;
      const out = [];
      for (const [res, category] of [[img, "image"], [aud, "audio"]]) {
        if (res.status !== "fulfilled") continue;
        for (const it of res.value.results || []) {
          out.push({ url: it.foreign_landing_url || it.url, title: it.title || "без названия", category,
            thumbnail: category === "image" ? it.thumbnail : null, author: it.creator,
            meta: { file_url: it.url, license: [it.license, it.license_version].filter(Boolean).join(" ").toUpperCase() || null, site: "Openverse" } });
        }
      }
      return out;
    },
  },
  {
    name: "youtube", title: "YouTube", phrase: true, cats: ["video"], expandable: true, needs: (s) => (s.youtubeKey ? "" : "нужен ключ YouTube в настройках"),
    async search(q, ctx) {
      const d = await getJSON(qs("https://www.googleapis.com/youtube/v3/search", { part: "snippet", q, type: "video", maxResults: Math.min(ctx.limit, 25), key: ctx.settings.youtubeKey }));
      return (d.items || []).filter((i) => i.id?.videoId).map((i) => ({
        url: `https://www.youtube.com/watch?v=${i.id.videoId}`, title: stripHtml(i.snippet?.title), category: "video",
        snippet: stripHtml(i.snippet?.description), thumbnail: i.snippet?.thumbnails?.medium?.url,
        date: (i.snippet?.publishedAt || "").slice(0, 10), author: i.snippet?.channelTitle, meta: { site: "YouTube" } }));
    },
  },
  {
    name: "google", title: "Google (весь интернет)", phrase: true, expandable: true,
    needs: (s) => (s.googleKey && s.googleCx ? "" : "нужны ключ Google и ID поисковой системы в настройках"),
    async search(q, ctx) {
      const base = { key: ctx.settings.googleKey, cx: ctx.settings.googleCx, q, num: 10 };
      const pages = ctx.limit >= 25 ? [1, 11] : [1];
      const calls = pages.map((start) => getJSON(qs("https://www.googleapis.com/customsearch/v1", { ...base, start })));
      calls.push(getJSON(qs("https://www.googleapis.com/customsearch/v1", { ...base, searchType: "image" })).then((d) => ({ images: d })));
      const out = [];
      for (const r of await Promise.allSettled(calls)) {
        if (r.status !== "fulfilled") continue;
        if (r.value.images) {
          for (const it of r.value.images.items || []) out.push({ url: it.image?.contextLink || it.link, title: it.title, category: "image",
            thumbnail: it.image?.thumbnailLink, meta: { file_url: it.link, site: it.displayLink } });
        } else {
          for (const it of r.value.items || []) out.push({ url: it.link, title: it.title, category: classifyUrl(it.link),
            snippet: it.snippet, thumbnail: it.pagemap?.cse_thumbnail?.[0]?.src, meta: { site: it.displayLink } });
        }
      }
      if (!out.length) throw new Error("Google не ответил: проверьте ключ и лимит");
      return out;
    },
  },
  {
    name: "hackernews", title: "Hacker News", phrase: false, cats: ["discussion"], expandable: true,
    async search(q, ctx) {
      const d = await getJSON(qs("https://hn.algolia.com/api/v1/search", { query: q, hitsPerPage: Math.min(ctx.limit, 30), tags: "story" }));
      return (d.hits || []).map((h) => ({ url: `https://news.ycombinator.com/item?id=${h.objectID}`, title: h.title, category: "discussion",
        snippet: truncate(stripHtml(h.story_text), 300) || h.url || "", date: (h.created_at || "").slice(0, 10), author: h.author,
        meta: { points: h.points, comments: h.num_comments, site: "Hacker News" } }));
    },
  },
  {
    name: "stackexchange", title: "Stack Exchange", phrase: false, cats: ["discussion"], expandable: true,
    async search(q, ctx) {
      const sites = ["gaming", "gamedev", "stackoverflow"];
      const res = await Promise.allSettled(sites.map((site) => getJSON(qs("https://api.stackexchange.com/2.3/search/advanced",
        { q, site, order: "desc", sort: "relevance", pagesize: Math.min(ctx.limit, 20) })).then((d) => [site, d])));
      const ok = res.filter((r) => r.status === "fulfilled");
      if (!ok.length) throw res[0].reason;
      return ok.flatMap(({ value: [site, d] }) => (d.items || []).map((it) => ({
        url: it.link, title: stripHtml(it.title), category: "discussion", snippet: (it.tags || []).join(", "),
        date: tsToIso(it.creation_date), author: stripHtml(it.owner?.display_name), meta: { score: it.score, answers: it.answer_count, site } })));
    },
  },
  {
    name: "gamebanana", title: "GameBanana", phrase: false, cats: ["mod"],
    async search(q, ctx) {
      const d = await getJSON(qs("https://gamebanana.com/apiv11/Util/Search/Results", { _sModelName: "Mod", _sOrder: "best_match",
        _sSearchString: q, _nPage: 1, _nPerpage: Math.min(ctx.limit, 50) }));
      return (d._aRecords || []).map((r) => {
        const img = r._aPreviewMedia?._aImages?.[0];
        const game = r._aGame?._sName;
        return { url: r._sProfileUrl, title: r._sName + (game ? ` (${game})` : ""), category: "mod", snippet: game ? `Игра: ${game}` : "",
          thumbnail: img ? `${img._sBaseUrl}/${img._sFile220 || img._sFile}` : null, date: tsToIso(r._tsDateModified || r._tsDateAdded),
          author: r._aSubmitter?._sName, meta: { views: r._nViewCount, likes: r._nLikeCount, site: "GameBanana" } };
      });
    },
  },
  {
    name: "modrinth", title: "Modrinth", phrase: false, cats: ["mod"],
    async search(q, ctx) {
      const d = await getJSON(qs("https://api.modrinth.com/v2/search", { query: q, limit: Math.min(ctx.limit, 50) }));
      return (d.hits || []).map((h) => ({ url: `https://modrinth.com/${h.project_type || "mod"}/${h.slug}`, title: h.title, category: "mod",
        snippet: h.description || "", thumbnail: h.icon_url || null, date: (h.date_modified || "").slice(0, 10), author: h.author,
        meta: { downloads: h.downloads, site: "Modrinth (Minecraft)" } }));
    },
  },
  {
    name: "speedrun", title: "speedrun.com", phrase: false, cats: ["game"],
    async search(q, ctx) {
      const d = await getJSON(qs("https://www.speedrun.com/api/v1/games", { name: q, max: Math.min(ctx.limit, 20) }));
      return (d.data || []).map((g) => ({ url: g.weblink, title: g.names?.international, category: "game",
        thumbnail: g.assets?.["cover-large"]?.uri, date: g.released ? String(g.released) : null, meta: { site: "speedrun.com" } }));
    },
  },
  {
    name: "openlibrary", title: "Open Library", phrase: true, cats: ["book"],
    async search(q, ctx) {
      const d = await getJSON(qs("https://openlibrary.org/search.json", { q, limit: Math.min(ctx.limit, 30) }));
      return (d.docs || []).map((doc) => ({ url: `https://openlibrary.org${doc.key}`, title: doc.title, category: "book",
        snippet: (doc.subject || []).slice(0, 8).join(", "), thumbnail: doc.cover_i ? `https://covers.openlibrary.org/b/id/${doc.cover_i}-M.jpg` : null,
        date: doc.first_publish_year ? String(doc.first_publish_year) : null, author: (doc.author_name || []).slice(0, 3).join(", ") || null,
        meta: { editions: doc.edition_count, site: "Open Library" } }));
    },
  },
];

export const PRESETS = {
  all: SOURCES.map((s) => s.name),
  game: ["wikipedia", "fandom", "commons", "github", "archive", "youtube", "google", "gamebanana", "speedrun", "stackexchange", "openverse"],
  code: ["github", "gitlab", "codeberg", "stackexchange", "hackernews", "google", "wikipedia", "archive"],
  media: ["commons", "openverse", "fandom", "youtube", "google", "archive"],
  text: ["wikipedia", "fandom", "google", "hackernews", "stackexchange", "openlibrary", "archive"],
};

const WEIGHTS = Object.fromEntries(SOURCES.map((s) => [s.name, s.weight ?? 1]));
WEIGHTS.steam = 1.4;
WEIGHTS.wikidata = 1.2;

const FACETS = ["screenshots", "source code", "mod", "soundtrack", "guide"];
const FACETS_EN = ["screenshots", "gameplay", "review", "guide", "patch notes", "soundtrack", "wiki", "forum", "client",
  "file format", "artwork", "trailer"];
const FACETS_RU = ["скриншоты", "обзор", "гайд", "обновление", "форум", "видео", "клиент", "саундтрек", "интервью"];

export function expansionQueries(query, terms, subjectTitle, count, names = []) {
  const base = subjectTitle || query;
  if (count > 6) {
    // maximum mode: every name gets its own follow-ups, in Russian for Cyrillic names
    const bases = [...new Set([base, ...names])].slice(0, 4);
    const list = terms.slice(0, 2).map(([t]) => `${base} ${t}`);
    const perBase = Math.max(3, Math.ceil((count - list.length) / bases.length));
    for (const b of bases) for (const f of (/[а-яё]/i.test(b) ? FACETS_RU : FACETS_EN).slice(0, perBase)) list.push(`${b} ${f}`);
    const seen = new Set([query.toLowerCase()]);
    return list.filter((q) => !seen.has(q.toLowerCase()) && seen.add(q.toLowerCase())).slice(0, count);
  }
  const qs_ = [];
  if (subjectTitle && subjectTitle.toLowerCase() !== query.toLowerCase()) qs_.push(subjectTitle);
  for (const [t] of terms.slice(0, 2)) qs_.push(`${base} ${t}`);
  for (const f of FACETS) qs_.push(`${base} ${f}`);
  const seen = new Set([query.toLowerCase()]);
  return qs_.filter((q) => !seen.has(q.toLowerCase()) && seen.add(q.toLowerCase())).slice(0, count);
}

// ------------------------------------------------------------------ archives of official sites (Wayback Machine)
const CDX = "https://web.archive.org/cdx/search/cdx";
const PAGE_HINTS = /news|notice|update|patch|event|guide|board|gallery|screen|media|download|about|class|world|story|faq|wallpaper|artwork/i;
const waybackDate = (ts) => (ts && ts.length >= 8 ? `${ts.slice(0, 4)}-${ts.slice(4, 6)}-${ts.slice(6, 8)}` : null);

export function normalizeSite(value) {
  const v = String(value || "").trim().toLowerCase().replace(/^[a-z]+:\/\//, "").split(/[/?#]/)[0].replace(/^www\./, "");
  return /^[a-z0-9.-]+\.[a-z]{2,}$/.test(v) ? v : "";
}

async function cdx(domain, mime, limit, collapse = "digest") {
  const rows = await getJSON(qs(CDX, { url: `${domain}/*`, output: "json", fl: "timestamp,original,mimetype,length",
    filter: ["statuscode:200", `mimetype:${mime}`], collapse, limit }), { timeout: 40000, retries: 0 });
  return Array.isArray(rows) && rows[0]?.[0] === "timestamp" ? rows.slice(1) : rows || [];
}

export async function waybackSite(domain, trusted, perSite = 60) {
  const label = `архив ${domain}`;
  const out = [];
  const [imgs, pages, files] = await Promise.allSettled([
    cdx(domain, "image/(jpeg|png|gif)", perSite * 3),
    cdx(domain, "text/html", 1500, "urlkey"),
    cdx(domain, "(application/pdf|application/zip|video/.*|audio/.*|application/x-shockwave-flash)", Math.ceil(perSite / 3)),
  ]);
  if (imgs.status === "fulfilled") {
    for (const [ts, original, , length] of imgs.value) {
      if (!(Number(length) >= 15000)) continue; // skip buttons and icons
      const raw = `https://web.archive.org/web/${ts}id_/${original}`;
      out.push({ url: `https://web.archive.org/web/${ts}/${original}`, title: `${original.split("/").pop().slice(0, 80)} (${label})`,
        category: "image", thumbnail: raw, date: waybackDate(ts), meta: { file_url: raw, trusted, site: label } });
      if (out.length >= perSite) break;
    }
  }
  if (pages.status === "fulfilled") {
    const ranked = [...pages.value].sort((a, b) => Number(!PAGE_HINTS.test(a[1])) - Number(!PAGE_HINTS.test(b[1])) || a[1].length - b[1].length);
    for (const [ts, original] of ranked.slice(0, Math.ceil(perSite / 2))) {
      out.push({ url: `https://web.archive.org/web/${ts}/${original}`, title: original.replace(/^https?:\/\//, "").slice(0, 110),
        category: /news|notice|update|patch|event/i.test(original) ? "news" : "article",
        snippet: `Архивная копия официальной страницы от ${waybackDate(ts)}`, date: waybackDate(ts), meta: { trusted, site: label } });
    }
  }
  if (files.status === "fulfilled") {
    for (const [ts, original, mime] of files.value) {
      const raw = `https://web.archive.org/web/${ts}id_/${original}`;
      out.push({ url: `https://web.archive.org/web/${ts}/${original}`, title: `${original.split("/").pop().slice(0, 80)} (${label})`,
        category: mime.startsWith("video/") ? "video" : mime.startsWith("audio/") ? "audio" : "file",
        snippet: `${mime}, сохранено ${waybackDate(ts)}`, date: waybackDate(ts), meta: { file_url: raw, trusted, site: label } });
    }
  }
  if (!out.length && [imgs, pages, files].every((r) => r.status === "rejected")) throw imgs.reason;
  return out;
}

const SKIP_ARCHIVE_HOSTS = ["archive.org", "github.com", "gitlab.com", "wikipedia.org", "wikimedia.org", "google.", "youtube.com",
  "reddit.com", "steampowered.com", "steamstatic.com", "openlibrary.org", "fandom.com", "wikidata.org"];

// given sites, sites whose address contains a name of the subject, guessed ones in maximum mode
export function archiveSites(names, results, given = [], guess = false) {
  const sites = new Map(given.map(normalizeSite).filter(Boolean).map((d) => [d, true]));
  const squashed = [...new Set(names.map((n) => n.toLowerCase().replace(/[^a-z0-9]/g, "")))].filter((s) => s.length >= 5);
  for (const r of results) {
    let host = "";
    try { host = new URL(r.url).hostname.toLowerCase().replace(/^www\./, ""); } catch { continue; }
    if (SKIP_ARCHIVE_HOSTS.some((h) => host.includes(h))) continue;
    if (squashed.some((s) => host.replace(/-/g, "").includes(s)) && !sites.has(host)) sites.set(host, false);
  }
  if (guess) for (const s of squashed) for (const tld of [".com", ".ru", ".net", ".co.kr"]) if (!sites.has(s + tld)) sites.set(s + tld, false);
  return [...sites].slice(0, 16);
}

// ------------------------------------------------------------------ orchestration
export async function runSearch(query, opts, onUpdate = () => {}) {
  const mode = MODES[opts.mode] || MODES.normal;
  const split = splitNames(query);
  const names = split.names;
  const contexts = split.contexts || {};
  const exact = Boolean(opts.exact) || split.exact;
  const ownRepo = String(opts.ownRepo || "AbyzeR-1337/SearchMaster").toLowerCase();
  const onlyCats = ONLY_GROUPS[opts.only]?.cats || null;
  const chosen = SOURCES.filter((s) => (opts.sources || PRESETS.all).includes(s.name));
  const ctx = { limit: mode.limit, langs: opts.langs?.length ? opts.langs : ["ru", "en"], settings: opts.settings || {}, pass: "first" };
  const state = { query, names, exact, only: opts.only || "", raw: [], statuses: {}, subject: null, aliases: [], queries: [...names],
    results: [], terms: [], stats: null, done: false, startedAt: Date.now() };
  for (const s of chosen) state.statuses[s.name] = { name: s.name, title: s.title, state: "waiting", count: 0, message: "", running: 0 };
  state.statuses.identify = { name: "identify", title: "Wikidata", state: "running", count: 0, message: "", running: 1 };

  let timer = null;
  const recompute = () => {
    let merged = mergeResults(state.raw);
    if (onlyCats) merged = merged.filter((r) => onlyCats.includes(r.category));
    // SearchMaster's own repository (its issues and PRs mention the searches) is not a finding
    merged = merged.filter((r) => !(r.url.toLowerCase() + "/").includes(`github.com/${ownRepo}/`));
    scoreAll(merged, state.queries, WEIGHTS, exact, contexts);
    state.results = merged;
    const relevant = merged.filter((r) => r.score >= 0.12);
    state.terms = relatedTerms(relevant, state.queries);
    state.stats = computeStats(relevant);
  };
  const emit = (now = false) => {
    if (now) {
      clearTimeout(timer);
      timer = null;
      recompute();
      onUpdate(state);
      return;
    }
    if (timer) return;
    timer = setTimeout(() => { timer = null; recompute(); onUpdate(state); }, 250);
  };

  const phrased = (src, q) => {
    if (q.includes('"')) return src.phrase ? q : q.replace(/"/g, "");
    const base = exact && src.phrase && q.trim().includes(" ") ? `"${q}"` : q;
    return contexts[q]?.length ? `${base} ${contexts[q][0]}` : base; // "Седьмой элемент" игра
  };

  const run = async (src, q, pass) => {
    const st = state.statuses[src.name];
    let why = src.needs ? src.needs(ctx.settings) : "";
    if (!why && onlyCats && src.cats && !src.cats.some((c) => onlyCats.includes(c))) why = "не нужен для выбранного типа";
    if (st.slow) return; // timed out before: don't make the whole search wait for it again
    if (why) {
      st.state = "skipped";
      st.message = why;
      return;
    }
    st.running++;
    st.state = "running";
    emit();
    try {
      const found = (await src.search(phrased(src, q), { ...ctx, pass })).map((r) => {
        const res = makeResult(src.name, r);
        if (res && pass !== "first") res.meta.via_query = q;
        return res;
      }).filter(Boolean);
      st.count += found.length;
      state.raw.push(...found);
    } catch (e) {
      if (pass === "first" || !st.count) st.message = e.message;
      st.failed = pass === "first" ? true : st.failed;
      if (e.timeout) st.slow = true;
    } finally {
      st.running--;
      if (!st.running) st.state = st.count ? "ok" : st.failed ? "error" : "empty";
      emit();
    }
  };

  // the subject: the first name that Wikipedia/Wikidata recognise. A match for a secondary or
  // ambiguous name must be confirmed by another name or by a context word ("Седьмой элемент" the song is not the game)
  const confirmed = (subj, n, i) => {
    const labels = [subj.title, ...subj.aliases];
    const otherNames = names.filter((x) => x !== n && labels.some((l) => similarity(l, x) >= 0.5)).length;
    const text = ` ${tokenize(`${subj.description} ${subj.extract}`).join(" ")} `;
    const ctxHit = (contexts[n] || []).some((c) => tokenize(c).some((w) => text.includes(` ${w}`)));
    if (contexts[n]?.length) return ctxHit || otherNames > 0;
    return i === 0 || otherNames > 0;
  };
  const subjectPromise = (async () => {
    for (const [i, n] of names.entries()) {
      const subj = await identify(n, ctx).catch(() => null);
      if (subj && confirmed(subj, n, i)) return subj;
    }
    return null;
  })();
  ctx.subject = subjectPromise;
  // every name through every source; the names of one source go one after another to stay polite
  const first = Promise.all(chosen.map(async (s) => {
    for (const [i, n] of names.entries()) await run(s, n, i === 0 ? "first" : "name");
  }));
  const subject = await subjectPromise;
  const idSt = state.statuses.identify;
  idSt.running = 0;
  const known = (q) => state.queries.some((x) => x.toLowerCase() === q.toLowerCase());
  if (subject) {
    state.subject = subject;
    state.raw.push(...subject.results);
    idSt.state = "ok";
    idSt.count = subject.results.length;
    state.aliases = subject.aliases.filter((a) => !names.some((n) => n.toLowerCase() === a.toLowerCase()));
    for (const a of state.aliases) if (!known(a)) state.queries.push(a);
    if (subject.title && !known(subject.title)) state.queries.push(subject.title);
  } else {
    idSt.state = "empty";
  }
  emit();
  // second pass: another name of the subject (e.g. the English title for a Russian query)
  const primary = names[0];
  const alias = state.aliases.find((a) => isLatin(a) && !isLatin(primary)) ||
    (state.aliases[0] && similarity(state.aliases[0], primary) < 0.5 ? state.aliases[0] : null);
  const second = alias ? Promise.all(chosen.filter((s) => s.name !== "wikipedia" && s.name !== "fandom").map((s) => run(s, alias, "alias"))) : null;
  await first;
  if (second) await second;
  // archives of the official sites: given ones, discovered ones, guessed ones in maximum mode
  const given = (opts.sites || []).map(normalizeSite).filter(Boolean);
  if (mode.archives || given.length) {
    recompute();
    const sites = archiveSites([...names, ...state.aliases], state.results, given, Boolean(mode.archives));
    const st = (state.statuses.wayback = { name: "wayback", title: "Архив сайтов (Wayback)", state: "running", count: 0, message: "", running: 1 });
    emit();
    const queue = [...sites];
    const worker = async () => {
      while (queue.length) {
        const [domain, trusted] = queue.shift();
        try {
          if (!trusted) {
            const a = await getJSON(qs("https://archive.org/wayback/available", { url: domain, timestamp: "2000" }), { timeout: 20000 });
            if (!a.archived_snapshots?.closest?.available) continue; // never archived: a guess that did not exist
          }
          const found = (await waybackSite(domain, trusted, mode.archives ? 80 : 40)).map((r) => makeResult("wayback", r)).filter(Boolean);
          st.count += found.length;
          state.raw.push(...found);
          emit();
        } catch (e) {
          if (!st.count) st.message = `${e.message} (полный архив доступен в облачном поиске)`;
        }
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    st.running = 0;
    st.state = st.count ? "ok" : st.message ? "error" : "empty";
    emit();
  }
  if (mode.deep) {
    recompute();
    const base = subject?.title && !/\(/.test(subject.title) ? subject.title : alias || primary;
    let extra = expansionQueries(primary, state.terms, base, mode.expansions || 5, names);
    // exact search keeps the name itself as a phrase: "Seven Souls Online" screenshots
    if (exact) {
      for (const n of [...new Set([base, ...names])].sort((a, b) => b.length - a.length)) {
        extra = extra.map((q) => (q.startsWith(n) && !q.includes('"') ? `"${n}"${q.slice(n.length)}` : q));
      }
    }
    state.expanded = extra;
    const expandable = chosen.filter((s) => s.expandable && s.name !== "wikipedia");
    for (const q of extra) await Promise.all(expandable.map((s) => run(s, q, "deep")));
  }
  state.done = true;
  state.finishedAt = Date.now();
  emit(true);
  return state;
}
