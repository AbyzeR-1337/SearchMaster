// Offline tests for the browser engine: node --test tests/web/
import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyUrl, expansionQueries, isLatin, makeResult, mergeResults, normalizeUrl, queryTerms, relatedTerms,
  scoreAll, similarity, slugify, stripHtml, tokenize, PRESETS, SOURCES,
} from "../../web/engine.js";

const R = (url, title, extra = {}) => makeResult(extra.source || "test", { url, title, category: "article", ...extra });

test("tokenize collapses dotted acronyms", () => {
  assert.deepEqual(tokenize("S.T.A.L.K.E.R.: Тень Чернобыля"), ["stalker", "тень", "чернобыля"]);
  assert.deepEqual(queryTerms("S.T.A.L.K.E.R.: Shadow of Chernobyl"), ["stalker", "shadow", "chernobyl"]);
});

test("similarity is stem-aware", () => {
  assert.equal(similarity("Квантовый компьютер", "квантовые компьютеры"), 1);
  assert.ok(similarity("S.T.A.L.K.E.R.: Тень Чернобыля", "Сталкер Тень Чернобыля") >= 0.5);
  assert.equal(similarity("Cooking", "Hollow Knight"), 0);
});

test("normalizeUrl drops tracking params and www", () => {
  assert.equal(normalizeUrl("https://www.example.com/page/?utm_source=x&b=2&a=1#f"), "example.com/page?a=1&b=2");
  assert.equal(normalizeUrl("http://example.com/page?a=1&b=2"), "example.com/page?a=1&b=2");
});

test("mergeResults combines duplicates", () => {
  const a = R("https://example.com/x", "T", { source: "github", meta: { stars: 5 } });
  const b = R("https://www.example.com/x/", "T", { source: "google", snippet: "longer snippet" });
  const m = mergeResults([a, b]);
  assert.equal(m.length, 1);
  assert.deepEqual(m[0].sources, ["github", "google"]);
  assert.equal(m[0].snippet, "longer snippet");
});

test("scoring keeps on-topic results and drops partial matches", () => {
  const good = R("https://a.com/hk", "Hollow Knight wiki", { snippet: "Hollow Knight is a metroidvania" });
  const partial = R("https://b.com/g", "Sir Gawain and the Green Knight", { meta: { downloads: 60000 } });
  const items = scoreAll([partial, good], ["Hollow Knight"]);
  assert.equal(items[0], good);
  assert.ok(good.score > 0.5);
  assert.ok(partial.score < 0.12);
});

test("aliases rescue results found by the English title", () => {
  const eng = R("https://store.steampowered.com/app/4500/", "S.T.A.L.K.E.R.: Shadow of Chernobyl");
  scoreAll([eng], ["Сталкер Тень Чернобыля"]);
  const low = eng.score;
  scoreAll([eng], ["Сталкер Тень Чернобыля", "S.T.A.L.K.E.R.: Shadow of Chernobyl"]);
  assert.ok(low < 0.12 && eng.score > 0.12);
});

test("related terms skip the query and prefer phrases", () => {
  const items = [0, 1, 2, 3].map((i) => R(`https://site${i}.com/p`, "Hollow Knight Silksong", { snippet: "Team Cherry made Hornet" }));
  scoreAll(items, ["Hollow Knight"]);
  const terms = relatedTerms(items, ["Hollow Knight"]).map(([t]) => t);
  assert.ok(!terms.includes("hollow") && !terms.includes("knight"));
  assert.ok(terms.includes("team cherry"));
});

test("misc helpers", () => {
  assert.equal(classifyUrl("https://github.com/a/b"), "code");
  assert.equal(classifyUrl("https://x.com/doc.PDF"), "file");
  assert.equal(classifyUrl("https://hollowknight.fandom.com/wiki/Hornet"), "wiki");
  assert.equal(slugify("Ведьмак 3: Дикая Охота"), "vedmak-3-dikaya-ohota");
  assert.equal(stripHtml("<b>a</b> &amp; &#39;b&#39; &lt;c&gt;"), "a & 'b' <c>");
  assert.ok(isLatin("Hollow Knight") && !isLatin("Сталкер"));
  assert.equal(makeResult("x", { url: "javascript:alert(1)", title: "x" }), null);
  const qs = expansionQueries("hk", [["silksong", 3]], "Hollow Knight", 4);
  assert.equal(qs[0], "Hollow Knight");
  assert.ok(qs.includes("Hollow Knight silksong"));
});

test("every preset names existing sources", () => {
  const names = new Set(SOURCES.map((s) => s.name));
  for (const [preset, list] of Object.entries(PRESETS)) for (const n of list) assert.ok(names.has(n), `${preset}: ${n}`);
});

test("identify falls back to Wikidata full-text search when Wikipedia is unavailable", async () => {
  const { identify } = await import("../../web/engine.js");
  const json = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes("wikipedia.org")) return json({}, 429);
    if (u.includes("wbsearchentities")) return json({ search: [{ id: "Q1", label: "Stalker (film)" }] });
    if (u.includes("list=search")) return json({ query: { search: [{ title: "Q215861" }] } });
    if (u.includes("props=labels%7Caliases")) {
      return json({ entities: {
        Q1: { labels: { en: { value: "Stalker" } } },
        Q215861: { labels: { ru: { value: "S.T.A.L.K.E.R.: Тень Чернобыля" }, en: { value: "S.T.A.L.K.E.R.: Shadow of Chernobyl" } } },
      } });
    }
    if (u.includes("ids=Q215861") && u.includes("claims")) {
      return json({ entities: { Q215861: {
        labels: { ru: { value: "S.T.A.L.K.E.R.: Тень Чернобыля" }, en: { value: "S.T.A.L.K.E.R.: Shadow of Chernobyl" } },
        descriptions: { ru: { value: "компьютерная игра 2007 года" } },
        sitelinks: { ruwiki: { title: "S.T.A.L.K.E.R.: Тень Чернобыля" }, enwiki: { title: "S.T.A.L.K.E.R.: Shadow of Chernobyl" } },
        claims: {
          P31: [{ mainsnak: { datavalue: { value: { id: "Q7889" } } } }],
          P178: [{ mainsnak: { datavalue: { value: { id: "Q1135036" } } } }],
          P577: [{ mainsnak: { datavalue: { value: { time: "+2008-01-01T00:00:00Z", precision: 11 } } } },
                 { mainsnak: { datavalue: { value: { time: "+2007-03-20T00:00:00Z", precision: 11 } } } }],
          P1733: [{ mainsnak: { datavalue: { value: "4500" } } }],
        },
      } } });
    }
    if (u.includes("props=labels&")) return json({ entities: { Q7889: { labels: { ru: { value: "компьютерная игра" } } }, Q1135036: { labels: { ru: { value: "GSC Game World" } } } } });
    throw new Error("unexpected " + u);
  };
  try {
    const s = await identify("Сталкер Тень Чернобыля", { langs: ["ru", "en"], limit: 10, settings: {} });
    assert.equal(s.qid, "Q215861");
    assert.equal(s.title, "S.T.A.L.K.E.R.: Тень Чернобыля");
    assert.equal(s.steamAppId, "4500");
    assert.ok(s.isGame);
    assert.ok(s.aliases.includes("S.T.A.L.K.E.R.: Shadow of Chernobyl"));
    assert.equal(s.aliases[0], "S.T.A.L.K.E.R.: Shadow of Chernobyl"); // Latin names first
    const facts = Object.fromEntries(s.facts.map((f) => [f.label, f.value]));
    assert.equal(facts["Разработчик"], "GSC Game World");
    assert.equal(facts["Дата выхода"], "20.03.2007"); // earliest release only
    assert.ok(s.wikiUrl.startsWith("https://ru.wikipedia.org/wiki/"));
    assert.ok(s.results.some((r) => r.meta.game_card && r.url === "https://store.steampowered.com/app/4500/"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("identify prefers 'Name (qualifier)' over a longer title that merely contains the name", async () => {
  const { identify } = await import("../../web/engine.js");
  const json = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes("ru.wikipedia.org")) return json({ query: { search: [] } });
    if (u.includes("en.wikipedia.org/w/api.php") && u.includes("list=search")) {
      return json({ query: { search: [{ title: "Celeste" }, { title: "Mary Celeste" }, { title: "Celeste (video game)" }] } });
    }
    if (u.includes("/page/summary/Celeste_(video_game)")) return json({ title: "Celeste (video game)", extract: "2018 platform game" });
    if (u.includes("/page/summary/Celeste")) return json({ type: "disambiguation", title: "Celeste" });
    if (u.includes("/page/summary/")) return json({ title: "Mary Celeste", extract: "ship" });
    if (u.includes("prop=pageprops")) return json({ query: { pages: { 1: {} } } });
    return json({}, 404);
  };
  try {
    const s = await identify("Celeste", { langs: ["ru", "en"], limit: 12, settings: {} });
    assert.equal(s.title, "Celeste (video game)");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("splitNames handles several names and quotes", async () => {
  const { splitNames } = await import("../../web/engine.js");
  assert.deepEqual(splitNames("Seven Souls Online / Martial Empires / 7Souls / 세븐소울즈"),
    { names: ["Seven Souls Online", "Martial Empires", "7Souls", "세븐소울즈"], exact: false, contexts: {} });
  assert.deepEqual(splitNames("«Hollow Knight»"), { names: ["Hollow Knight"], exact: true, contexts: {} });
  assert.deepEqual(splitNames("AC/DC"), { names: ["AC/DC"], exact: false, contexts: {} });
});

test("exact scoring keeps whole-name matches only", () => {
  const whole = R("https://a.com/1", "Seven Souls Online closed beta starts");
  const near = R("https://b.com/2", "Seven deadly sins: souls of the online era");
  const other = R("https://c.com/3", "Martial Empires review");
  const one = R("https://d.com/4", "7Souls update notes");
  scoreAll([whole, near, other, one], ["Seven Souls Online", "Martial Empires", "7Souls"], {}, true);
  assert.ok(whole.score > 0.12 && other.score > 0.12 && one.score > 0.12);
  assert.ok(near.score < 0.12);
});

test("maximum mode: follow-ups for every name, archive sites", async () => {
  const { expansionQueries: ex, archiveSites, normalizeSite } = await import("../../web/engine.js");
  const qs = ex("Seven Souls Online", [["akkadia", 3]], null, 14, ["Seven Souls Online", "Martial Empires", "Седьмой элемент"]);
  assert.equal(qs.length, 14);
  assert.ok(qs.includes("Седьмой элемент скриншоты") && qs.some((q) => q.startsWith("Martial Empires ")));
  assert.equal(normalizeSite("https://www.7souls.ru/news?x=1"), "7souls.ru");
  assert.equal(normalizeSite("not a site"), "");
  const sites = new Map(archiveSites(["Seven Souls Online", "7Souls"],
    [R("https://www.sevensoulsonline.com/news", "x"), R("https://github.com/a/7souls", "y")], ["7souls.ru"], true));
  assert.equal(sites.get("7souls.ru"), true);
  assert.equal(sites.get("sevensoulsonline.com"), false);
  assert.ok(!sites.has("github.com") && sites.has("7souls.co.kr"));
});

test("waybackSite turns CDX rows into pages, screenshots and files", async () => {
  const { waybackSite } = await import("../../web/engine.js");
  const head = ["timestamp", "original", "mimetype", "length"];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = decodeURIComponent(String(url));
    const rows = u.includes("mimetype:image") ? [head, ["20110405000000", "http://7souls.ru/img/ss1.jpg", "image/jpeg", "120000"],
      ["20110405000000", "http://7souls.ru/img/btn.gif", "image/gif", "800"]]
      : u.includes("mimetype:text/html") ? [head, ["20110402000000", "http://7souls.ru/news/12", "text/html", "4000"]]
      : [head, ["20110402000000", "http://7souls.ru/files/manual.pdf", "application/pdf", "800000"]];
    return { ok: true, status: 200, json: async () => rows };
  };
  try {
    const found = await waybackSite("7souls.ru", true, 20);
    assert.equal(found.filter((r) => r.category === "image").length, 1);
    assert.ok(found.some((r) => r.category === "news" && r.url.endsWith("/news/12")));
    assert.ok(found.some((r) => r.category === "file" && r.meta.file_url.includes("id_/http://7souls.ru/files/manual.pdf")));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("context words keep an ambiguous name to the right subject", async () => {
  const { splitNames } = await import("../../web/engine.js");
  const { names, contexts } = splitNames("Seven Souls Online / Седьмой элемент (игра, MMORPG, Nival)");
  assert.deepEqual(names, ["Seven Souls Online", "Седьмой элемент"]);
  assert.deepEqual(contexts, { "Седьмой элемент": ["игра", "MMORPG", "Nival"] });
  const game = R("https://stopgame.ru/1", "Седьмой элемент: открытый бета-тест онлайн-игры от Nival");
  const song = R("https://youtube.com/2", "VITAS — Седьмой элемент (клип)");
  scoreAll([game, song], names, {}, true, contexts);
  assert.ok(game.score > 0.12 && song.score < 0.12);
});
