from searchmaster.query import QueryPlan
from searchmaster.sources.base import classify_url
from searchmaster.utils import (
    canonical_url, file_category, file_extension, match_form, slugify, strip_html, tokenize,
)


def test_canonical_url_strips_tracking_and_normalises():
    assert canonical_url("https://www.Example.com/a/?utm_source=x&b=2#frag") == "https://example.com/a?b=2"
    assert canonical_url("http://example.com/") == "https://example.com/"
    assert canonical_url("https://youtu.be/abc123") == "https://youtube.com/watch?v=abc123"
    assert canonical_url("https://m.youtube.com/watch?v=abc123&t=5&feature=share") == "https://youtube.com/watch?v=abc123"


def test_tokenize_handles_hyphens_acronyms_cyrillic():
    assert tokenize("S.T.A.L.K.E.R. Half-Life_2 игра the 1998") == ["stalker", "half", "life", "игра", "1998"]
    assert tokenize("Ёжик в тумане") == ["ежик", "тумане"]


def test_match_form_makes_spellings_equal():
    assert match_form("Half-Life 2") == match_form('"half life 2"') == match_form("half_life_2")
    assert match_form("S.T.A.L.K.E.R.") == " stalker "


def test_file_detection():
    assert file_category("https://x.org/manual.PDF") == "document"
    assert file_category("https://x.org/page.html") is None
    assert file_category("https://x.org/a/b.tar.gz") == "archive"
    assert file_category("https://x.org/maps/c1a0.bsp") == "game-data"
    assert file_extension("https://x.org/Shot.JPG?w=3") == "jpg"


def test_misc_helpers():
    assert slugify("Half-Life 2: Episode One!") == "half-life-2-episode-one"
    assert strip_html("<b>a&amp;b</b>  c") == "a&b c"


def test_classify_url():
    assert classify_url("https://github.com/ValveSoftware/source-sdk-2013") == "repo"
    assert classify_url("https://github.com/a/b/blob/main/x.cpp") == "code"
    assert classify_url("https://github.com/a/b/issues/1") == "discussion"
    assert classify_url("https://en.wikipedia.org/wiki/Doom") == "wiki"
    assert classify_url("https://www.youtube.com/watch?v=1") == "video"
    assert classify_url("https://cdn.x.com/shot.png") == "image"
    assert classify_url("https://x.com/manual.pdf") == "file"
    assert classify_url("https://www.reddit.com/r/HalfLife/comments/1") == "discussion"
    assert classify_url("https://archive.org/details/foo") == "archive"
    assert classify_url("https://store.steampowered.com/app/220/") == "game"


def test_web_queries_interleave_facets_and_aliases():
    plan = QueryPlan("Half-Life 2", aliases=["HL2"], facets=["general", "images", "code", "files"])
    qs = plan.web_queries(8)
    facets = [f for f, _ in qs[:4]]
    assert facets == ["general", "images", "code", "files"]   # every facet covered early
    assert ('general', '"Half-Life 2"') in qs
    assert any(q == "HL2" for _, q in qs)                      # alias included
    assert len({q for _, q in plan.web_queries()}) == len(plan.web_queries())  # no duplicates


def test_web_queries_without_operators():
    plan = QueryPlan("Doom", facets=["files", "code"])
    assert all(":" not in q for _, q in plan.web_queries(operators=False))


def test_snowball_plan_only_runs_new_terms():
    plan = QueryPlan("Doom", facets=["general"], extra_terms=["id Software"], snowball_only=True)
    assert plan.web_queries() == [("snowball", "Doom id Software")]
    assert plan.api_queries("code") == ["Doom id Software"]


def test_topic_tokens():
    plan = QueryPlan("Half-Life 2", aliases=["HL2"])
    assert plan.topic_tokens == {"half", "life", "2", "hl2"}


def test_search_form_for_engines():
    from searchmaster.utils import search_form
    assert search_form("S.T.A.L.K.E.R.: Shadow of Chernobyl") == "STALKER Shadow of Chernobyl"
    assert search_form("Half-Life 2: Episode One") == "Half-Life 2 Episode One"
