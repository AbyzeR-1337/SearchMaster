"""Offline tests: no network access needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from searchmaster.analyze import (
    compute_stats, expansion_queries, merge_results, normalize_url, related_terms, score_all,
)
from searchmaster.classify import classify_url
from searchmaster.gh import build_index, parse_issue_form, request_from_env
from searchmaster.models import Result, SourceStatus
from searchmaster.report import Report, link_citations
from searchmaster.sources import PRESETS, resolve_sources
from searchmaster.textutil import slugify


def r(url, title, **kw):
    kw.setdefault("source", "test")
    kw.setdefault("category", "article")
    return Result(url=url, title=title, **kw)


# ---------------------------------------------------------------- urls & dedupe
def test_normalize_url_strips_tracking_and_www():
    a = normalize_url("https://www.example.com/page/?utm_source=x&b=2&a=1#frag")
    b = normalize_url("http://example.com/page?a=1&b=2")
    assert a == b == "example.com/page?a=1&b=2"


def test_urls_with_spaces_are_encoded():
    item = r("https://host/news/PC Gamer/1", "x")
    assert " " not in item.url and "PC%20Gamer" in item.url


def test_merge_results_combines_sources_and_meta():
    a = r("https://example.com/x", "Title", source="github", meta={"stars": 5})
    b = r("https://www.example.com/x/", "Title", source="web", snippet="longer snippet text", meta={"lang": "C"})
    merged = merge_results([a, b])
    assert len(merged) == 1
    assert merged[0].sources == ["github", "web"]
    assert merged[0].snippet == "longer snippet text"
    assert merged[0].meta == {"stars": 5, "lang": "C"}


# ---------------------------------------------------------------- scoring
def test_scoring_prefers_on_topic_results():
    good = r("https://a.com/hk", "Hollow Knight wiki", snippet="Hollow Knight is a metroidvania")
    partial = r("https://b.com/g", "Sir Gawain and the Green Knight", meta={"downloads": 60000})
    off = r("https://c.com/z", "Cooking recipes")
    items = [off, partial, good]
    score_all(items, "Hollow Knight", {})
    assert items[0] is good
    assert good.score > 0.5
    assert partial.score < 0.12  # only half of the query matched -> treated as off-topic
    assert off.score < 0.05


def test_russian_word_forms_match():
    item = r("https://a.ru/x", "Квантовых компьютерах будущего")
    score_all([item], "квантовые компьютеры", {})
    assert item.score > 0.3


def test_trusted_results_survive_without_terms():
    shot = r("https://cdn/ss1.jpg", "Скриншот #1", category="image", meta={"trusted": True})
    score_all([shot], "Hollow Knight", {})
    assert shot.score >= 0.5


def test_related_terms_skip_query_and_noise():
    items = [
        r(f"https://x.com/{i}", "Hollow Knight Silksong", snippet="Team Cherry Silksong Hornet steam") for i in range(4)
    ]
    terms = [t for t, _ in related_terms(items, "Hollow Knight")]
    assert "hollow" not in terms and "knight" not in terms and "steam" not in terms
    assert any("silksong" in t for t in terms)


def test_expansion_queries_use_game_name_and_facets():
    qs = expansion_queries("hk", [("silksong", 5)], "Hollow Knight", 6)
    assert qs[0] == "Hollow Knight"
    assert "Hollow Knight silksong" in qs
    assert any("screenshots" in q for q in qs)
    assert expansion_queries("hk", [], None, 0) == []


# ---------------------------------------------------------------- classify / misc
@pytest.mark.parametrize(
    "url,cat",
    [
        ("https://github.com/a/b", "code"),
        ("https://www.youtube.com/watch?v=1", "video"),
        ("https://site.com/manual.pdf", "file"),
        ("https://site.com/pic.PNG", "image"),
        ("https://hollowknight.fandom.com/wiki/Hornet", "wiki"),
        ("https://www.nexusmods.com/x", "mod"),
        ("https://example.com/about", "article"),
    ],
)
def test_classify_url(url, cat):
    assert classify_url(url) == cat


def test_slugify_transliterates():
    assert slugify("Ведьмак 3: Дикая Охота") == "vedmak-3-dikaya-ohota"
    assert slugify("!!!") == "search"


def test_resolve_sources():
    assert resolve_sources("all") == PRESETS["all"]
    assert resolve_sources("game,-reddit") == [s for s in PRESETS["game"] if s != "reddit"]
    assert resolve_sources("github,steam") == ["github", "steam"]
    with pytest.raises(ValueError):
        resolve_sources("nope")


def test_link_citations():
    cited = [r("https://a.com/1", "A"), r("https://b.com/2", "B")]
    text = link_citations("Fact [1]. Other [1, 2]. Bad [9].", cited)
    assert "[[1](https://a.com/1)]" in text
    assert "[[1](https://a.com/1), [2](https://b.com/2)]" in text
    assert "[9]" in text


# ---------------------------------------------------------------- GitHub glue
ISSUE_BODY = """### Запрос

Hollow Knight

### Режим

deep

### Источники

game

### Языки

ru,en

### Исходники (ZIP)

3

### Опции

- [X] Не скачивать файлы, только ссылки
"""


def test_parse_issue_form():
    form = parse_issue_form(ISSUE_BODY)
    assert form["запрос"] == "Hollow Knight"
    assert form["режим"] == "deep"


def test_request_from_issue_form():
    req = request_from_env({"EVENT_NAME": "issues", "ISSUE_TITLE": "🔍 ", "ISSUE_BODY": ISSUE_BODY})
    assert req["query"] == "Hollow Knight"
    assert req["mode"] == "deep"
    assert req["sources"] == "game"
    assert req["code_zips"] == "3"
    assert req["download"] == "false"
    assert req["out_dir"].startswith("results/") and req["out_dir"].endswith("_hollow-knight")


def test_request_from_issue_title_only():
    req = request_from_env({"EVENT_NAME": "issues", "ISSUE_TITLE": "🔍 Stardew Valley --quick", "ISSUE_BODY": ""})
    assert req["query"] == "Stardew Valley"
    assert req["mode"] == "quick"
    assert req["download"] == "true"


def test_request_from_comment():
    req = request_from_env({"EVENT_NAME": "issue_comment", "COMMENT_BODY": "/search Celeste source code --deep --sources=code"})
    assert req["query"] == "Celeste source code"
    assert req["mode"] == "deep"
    assert req["sources"] == "code"


def test_request_sanitizes_inputs():
    req = request_from_env({"EVENT_NAME": "workflow_dispatch", "INPUT_QUERY": "x", "INPUT_MODE": "evil",
                            "INPUT_SOURCES": "a;rm -rf /", "INPUT_CODE_ZIPS": "999"})
    assert req["mode"] == "normal"
    assert req["sources"] == "all"
    assert req["code_zips"] == "10"


# ---------------------------------------------------------------- reports
def _data(results):
    return {
        "results": results,
        "min_score": 0.12,
        "stats": compute_stats(results),
        "terms": [("silksong", 3)],
        "timeline": [],
        "statuses": [SourceStatus(name="steam", title="Steam", state="ok", count=2, seconds=1.2)],
        "download": None,
        "ai_text": "### Кратко\nИгра [1].",
        "ai_cited": results,
        "ai_status": "готово",
        "links": {},
    }


def test_reports_are_written(tmp_path: Path):
    results = [
        r("https://store.steampowered.com/app/1/", "Hollow Knight", category="game", score=2.0,
          snippet="<b>Forge</b> | your path", meta={"game_card": True, "developers": ["Team Cherry"]}),
        r("https://cdn/ss.jpg", "Hollow Knight — скриншот #1", category="image", score=1.0, thumbnail="https://cdn/t.jpg"),
        r("https://x.com/</script><script>alert(1)</script>", "evil </script>", score=1.0),
    ]
    rep = Report(tmp_path / "out", "Hollow Knight", "normal")
    paths = rep.write(_data(results))
    md = paths["md"].read_text(encoding="utf-8")
    assert "# 🔍 Hollow Knight" in md and "Team Cherry" in md and "Галерея" in md
    assert "[[1](https://store.steampowered.com/app/1/)]" in md
    html = paths["html"].read_text(encoding="utf-8")
    assert "</script><script>alert" not in html  # data blob is escaped
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["query"] == "Hollow Knight" and len(data["results"]) == 3
    assert paths["csv"].read_text(encoding="utf-8-sig").startswith("score,category")
    assert len(paths["summary"].read_text(encoding="utf-8")) < len(md)


def test_build_index(tmp_path: Path):
    folder = tmp_path / "results" / "2026-01-01_0000_hk"
    folder.mkdir(parents=True)
    (folder / "results.json").write_text(json.dumps({"query": "HK", "mode": "deep", "created": "2026-01-01T00:00",
                                                    "stats": {"total": 5, "downloaded": 2}}), encoding="utf-8")
    index = build_index(tmp_path)
    assert "[HK](results/2026-01-01_0000_hk/report.md)" in index


# ---------------------------------------------------------------- aliases (offline, fake HTTP)
class FakeHttp:
    """Returns canned JSON by URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.session = type("S", (), {"headers": {}})()

    def get_json(self, url, params=None, headers=None, **kw):
        key = url + " " + " ".join(f"{k}={v}" for k, v in (params or {}).items())
        for needle, payload in self.routes.items():
            if needle in key:
                return payload
        raise RuntimeError(f"no route for {key}")


def _ctx(routes, query, langs=("ru",)):
    import logging

    from searchmaster.config import Config, Context

    cfg = Config.build(query, "quick", langs=list(langs))
    return Context(config=cfg, http=FakeHttp(routes), log=logging.getLogger("test"))


def test_wikipedia_summary_and_aliases():
    from searchmaster.analyze import find_aliases
    from searchmaster.sources.wiki import Wikipedia

    routes = {
        "list=search": {"query": {"search": [{"title": "S.T.A.L.K.E.R.: Тень Чернобыля", "snippet": "игра"}]}},
        "page/summary": {"extract": "Компьютерная игра 2007 года.", "description": "игра"},
        "prop=langlinks": {"query": {"pages": {"1": {"langlinks": [
            {"lang": "en", "*": "S.T.A.L.K.E.R.: Shadow of Chernobyl"}, {"lang": "de", "*": "Stalker DE"}]}}}},
    }
    query = "Сталкер Тень Чернобыля"
    ctx = _ctx(routes, query)
    found = Wikipedia(ctx).search(query, 5)
    assert found[0].meta["summary"] is True
    assert found[0].meta["aliases"]["en"] == "S.T.A.L.K.E.R.: Shadow of Chernobyl"
    aliases = find_aliases(query, found, ["ru"])
    assert aliases[0] == "S.T.A.L.K.E.R.: Shadow of Chernobyl"
    assert "Stalker DE" not in aliases


def test_unrelated_top_hit_is_not_a_summary():
    from searchmaster.sources.wiki import Wikipedia

    routes = {"list=search": {"query": {"search": [{"title": "Cooking", "snippet": "food"}]}}}
    found = Wikipedia(_ctx(routes, "Hollow Knight")).search("Hollow Knight", 5)
    assert "summary" not in found[0].meta


def test_alias_scoring_rescues_english_results():
    eng = r("https://store.steampowered.com/app/4500/", "S.T.A.L.K.E.R.: Shadow of Chernobyl", category="game")
    score_all([eng], ["Сталкер Тень Чернобыля"], {})
    low = eng.score
    score_all([eng], ["Сталкер Тень Чернобыля", "S.T.A.L.K.E.R.: Shadow of Chernobyl"], {})
    assert low < 0.12 < eng.score


def test_index_json_for_web_app(tmp_path: Path):
    from searchmaster.gh import collect_searches

    folder = tmp_path / "results" / "2026-01-02_0000_celeste"
    folder.mkdir(parents=True)
    (folder / "results.json").write_text(json.dumps({"query": "Celeste", "mode": "normal", "created": "2026-01-02T00:00",
                                                    "game": "Celeste", "stats": {"total": 7}}), encoding="utf-8")
    items = collect_searches(tmp_path)
    assert items == [{"path": "results/2026-01-02_0000_celeste", "query": "Celeste", "mode": "normal",
                      "created": "2026-01-02T00:00", "total": 7, "downloaded": 0, "game": "Celeste"}]


# ---------------------------------------------------------------- several names, exact search, "only"
def test_split_names():
    from searchmaster.textutil import split_names

    assert split_names("Seven Souls Online / Martial Empires / 7Souls / 세븐소울즈") == (
        ["Seven Souls Online", "Martial Empires", "7Souls", "세븐소울즈"], False)
    assert split_names('"Hollow Knight"') == (["Hollow Knight"], True)
    assert split_names("AC/DC") == (["AC/DC"], False)  # a slash without spaces is part of the name


def test_config_names_exact_only():
    from searchmaster.config import Config

    cfg = Config.build('"Martial Empires" | Seven Souls Online', "quick", only=["image", "nope"])
    assert cfg.names == ["Martial Empires", "Seven Souls Online"]
    assert cfg.only == ["image"] and cfg.wanted_categories == {"image"}
    cfg = Config.build('"Seven Souls Online"', "quick")
    assert cfg.exact and cfg.names == ["Seven Souls Online"]


def test_exact_scoring_requires_whole_name():
    whole = r("https://a.com/1", "Seven Souls Online closed beta starts")
    split = r("https://b.com/2", "Seven deadly sins: souls of the online era")
    other_name = r("https://c.com/3", "Martial Empires review")
    items = [whole, split, other_name]
    score_all(items, ["Seven Souls Online", "Martial Empires"], {}, exact=True)
    assert whole.score > 0.12 and other_name.score > 0.12
    assert split.score < 0.12
    # without exact the loose match still passes
    score_all([split], ["Seven Souls Online"], {}, exact=False)
    assert split.score > 0.12


def test_request_exact_and_only():
    req = request_from_env({"EVENT_NAME": "issue_comment",
                            "COMMENT_BODY": "/search Seven Souls Online / Martial Empires --exact --only=image --deep"})
    assert req["query"] == "Seven Souls Online / Martial Empires"
    assert req["exact"] == "true" and req["only"] == "image" and req["mode"] == "deep"
    body = ISSUE_BODY + "\n### Точный поиск\n\nда\n\n### Тип материалов\n\ncode\n"
    req = request_from_env({"EVENT_NAME": "issues", "ISSUE_TITLE": "🔍 ", "ISSUE_BODY": body})
    assert req["exact"] == "true" and req["only"] == "code"
    req = request_from_env({"EVENT_NAME": "workflow_dispatch", "INPUT_QUERY": "x", "INPUT_ONLY": "all", "INPUT_EXACT": "false"})
    assert req["only"] == "" and req["exact"] == "false"


def test_fandom_guess_rejects_generic_wiki():
    from searchmaster.sources.wiki import Fandom

    routes = {"meta=siteinfo": {"query": {"general": {"sitename": "Online Wiki"}}}}
    assert not Fandom(_ctx(routes, "Seven Souls Online"))._wiki_matches("online", "Seven Souls Online")
    routes = {"meta=siteinfo": {"query": {"general": {"sitename": "Seven Souls Online Wiki"}}}}
    assert Fandom(_ctx(routes, "Seven Souls Online"))._wiki_matches("sevensoulsonline", "Seven Souls Online")
