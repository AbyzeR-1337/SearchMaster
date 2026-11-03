from searchmaster import analyze as an
from searchmaster.models import Result

NAMES = ["Half-Life 2"]
TOKENS = {"half", "life", "2"}


def R(url, title, snippet="", source="bing", kind="page", **meta):
    return Result(url=url, title=title, snippet=snippet, source=source, kind=kind, query="q", meta=meta)


def corpus():
    game = [
        R(f"https://site{i}.com/hl2", f"Half-Life 2 review {i}",
          "Valve shooter starring Gordon Freeman in City 17, built on the Source engine game") for i in range(6)
    ]
    noise = [
        R("https://so.com/q/1", "Half-Life Determination", "Compute radioactive decay constant from isotope data",
          source="stackexchange", kind="discussion"),
        R("https://shop.com/vac", "Shop Vac 14 Gallon", "wet dry vacuum"),
    ]
    related = [R("https://wiki.org/Half-Life_(series)", "Half-Life (series)",
                 "Valve game series with Gordon Freeman, Source engine, City 17")]
    return game, noise, related


def test_dedupe_merges_found_via_and_kind():
    a = R("https://www.example.com/x/?utm_source=1", "X", source="bing")
    b = R("https://example.com/x", "X better title", snippet="longer snippet", source="duckduckgo", kind="wiki")
    out = an.dedupe([a, b])
    assert len(out) == 1
    assert len(out[0].found_via) == 2 and out[0].snippet == "longer snippet" and out[0].kind == "wiki"


def test_context_filter_removes_homonyms_keeps_related():
    game, noise, related = corpus()
    results = game + noise + related
    ctx = an.score_results(results, NAMES, TOKENS)
    assert {"valve", "gordon", "freeman"} <= ctx
    kept = an.filter_relevant(results, strict=True, names=NAMES)
    titles = {r.title for r in kept}
    assert "Half-Life Determination" not in titles     # radioactive half-life: no topical context
    assert "Shop Vac 14 Gallon" not in titles
    assert "Half-Life (series)" in titles               # partial name but strong context
    assert all(g.title in titles for g in game)
    # non-strict keeps everything
    assert len(an.filter_relevant(results, strict=False)) == len(results)


def test_score_rewards_title_match_and_agreement():
    a = R("https://a.com", "Half-Life 2 guide", "text")
    b = R("https://b.com", "Some page", "mentions Half-Life 2 once")
    c = R("https://c.com", "Half-Life 2 guide", "text")
    c.found_via += ["duckduckgo: q", "brave: q"]
    an.score_results([a, b, c], NAMES, TOKENS)
    assert a.score > b.score
    assert c.score > a.score


def test_single_word_names_need_context_in_body():
    names, tokens = ["Control"], {"control"}
    good = [R(f"https://g{i}.com", f"Control game review {i}", "Remedy Jesse Faden Oldest House game") for i in range(4)]
    body_only_noise = R("https://n.com/x", "Thermostat manual", "how to control the temperature")
    body_only_good = R("https://n.com/y", "Remedy interview", "we built control with jesse faden and oldest house")
    results = good + [body_only_noise, body_only_good]
    an.score_results(results, names, tokens)
    kept = {r.title for r in an.filter_relevant(results, strict=True, names=names)}
    assert "Thermostat manual" not in kept and "Remedy interview" in kept


def test_near_duplicates_collapse():
    text = " ".join(f"word{i}" for i in range(400))
    a = R("https://a.com/p", "A")
    b = R("https://mirror.com/p", "B")
    for r in (a, b):
        r.content, r.crawled = text, True
    a.score, b.score = 5, 3
    out = an.drop_near_duplicates([a, b])
    assert out == [a] and a.meta["mirrors"] == ["https://mirror.com/p"]


def test_analyze_intelligence():
    game, noise, related = corpus()
    results = game + related + [
        R("https://store.steampowered.com/app/220/", "Half-Life 2 — Steam", "Valve", source="steam", kind="game",
          developers=["Valve"], publishers=["Valve"], website="http://www.half-life.com"),
        R("https://cdn.x/shot.jpg", "Half-Life 2 screenshot", kind="image"),
        R("https://x.org/manual.pdf", "Half-Life 2 manual (2004)", kind="file"),
    ]
    an.score_results(results, NAMES, TOKENS)
    a = an.analyze(results, NAMES, TOKENS)
    assert a["total"] == len(results)
    assert ("Source", 7) in a["engines"] or a["engines"][0][0] == "Source"
    assert a["developers"] == [("Valve", 1)]
    assert a["official_sites"] == ["http://www.half-life.com"]
    assert dict(a["file_types"]) == {"jpg": 1, "pdf": 1}
    assert 2004 in dict(a["timeline"])
    ents = [e for e, _ in a["entities"]]
    assert "Gordon Freeman" in ents and "City 17" in ents
    assert not any(an.match_form(e) == an.match_form("Half-Life 2") for e in ents)
    kws = [k for k, _ in a["keywords"]]
    assert "steam" not in kws and "half" not in kws


def test_discovered_sites_ignore_social():
    rs = [R("https://store.steampowered.com/app/1", "G", source="steam", kind="game", website="https://discord.gg/x"),
          R("https://store.steampowered.com/app/2", "G2", source="steam", kind="game", website="https://game.com"),
          R("https://foo.fandom.com/wiki/A", "A", kind="wiki")]
    sites, fandoms = an.discovered_sites(rs)
    assert sites == ["https://game.com"] and fandoms == ["foo"]


def test_partial_title_match_but_sequel_number_required():
    names = ["S.T.A.L.K.E.R.: Тень Чернобыля"]
    tokens = {"stalker", "тень", "чернобыля"}
    ru = R("https://ru.wikipedia.org/wiki/X", "S.T.A.L.K.E.R.: Тень Чернобыля — Википедия", "статья")
    renamed = R("https://x.com/a", "Сталкер: Тень Чернобыля — обзор", "")
    an.score_results([ru, renamed], names + ["Сталкер Тень Чернобыля"], tokens)
    assert an.is_relevant(ru) and an.is_relevant(renamed)

    hl = R("https://so.com/q", "Half-Life Determination", "radioactive decay")
    an.score_results([hl], NAMES, TOKENS)
    assert not hl.meta["partial"] and not an.is_relevant(hl, NAMES)


def test_video_title_noise_not_entities():
    rs = [R(f"https://y{i}.com", f"STALKER Shadow of Chernobyl Part {i} FULL GAME No Commentary PC Longplay",
            "GSC Game World made it", kind="video") for i in range(3)]
    ents = [e for e, _ in an.entities(rs, ["S.T.A.L.K.E.R.: Shadow of Chernobyl"])]
    assert "GSC Game World" in ents
    assert not any(e in ("FULL GAME", "No Commentary", "PC Longplay", "Part 1", "The S") for e in ents)
