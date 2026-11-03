import base64
import json

from searchmaster.extract import extract_page
from searchmaster.crawler import summarize_tree
from searchmaster.sources.code import parse_sourcegraph, repo_result
from searchmaster.sources.community import reddit_results
from searchmaster.sources.media import parse_bing_images, parse_youtube_html
from searchmaster.sources.web import decode_bing_url, decode_ddg_url, parse_bing, parse_ddg
from searchmaster.sources.wiki import fandom_candidates


def _bing_href(url: str) -> str:
    enc = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&u=a1{enc}&ntb=1"


def test_decode_wrappers():
    assert decode_bing_url(_bing_href("https://example.com/x?y=1")) == "https://example.com/x?y=1"
    assert decode_bing_url("https://plain.org") == "https://plain.org"
    assert decode_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") == "https://example.com/a"


def test_parse_bing():
    html = f"""<ol id="b_results">
      <li class="b_algo"><h2><a href="{_bing_href('https://half-life.com/')}">Half-Life <b>2</b></a></h2>
        <div class="b_caption"><p>The official site.</p></div></li>
      <li class="b_algo"><h2><a href="https://github.com/a/b">a/b</a></h2><p>repo text</p></li>
      <li class="b_ad"><h2><a href="https://ads.example">ad</a></h2></li></ol>"""
    res = list(parse_bing(html, "q", "bing"))
    assert [r.url for r in res] == ["https://half-life.com/", "https://github.com/a/b"]
    assert res[0].title == "Half-Life 2" and res[0].snippet == "The official site."
    assert res[1].kind == "repo"


def test_parse_ddg_skips_ads():
    html = """<div class="result results_links"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.org%2Fdoc.pdf">Doc</a>
      <a class="result__snippet">manual</a></div>
      <div class="result result--ad"><a class="result__a" href="https://ad.example">Ad</a></div>"""
    res = list(parse_ddg(html, "q", "duckduckgo"))
    assert len(res) == 1 and res[0].url == "https://x.org/doc.pdf" and res[0].kind == "file"


def test_parse_youtube_html():
    data = {"contents": {"x": [{"videoRenderer": {
        "videoId": "abc", "title": {"runs": [{"text": "HL2 "}, {"text": "trailer"}]},
        "ownerText": {"runs": [{"text": "Valve"}]}, "viewCountText": {"simpleText": "1,234 views"},
        "lengthText": {"simpleText": "2:01"}, "detailedMetadataSnippets": [{"snippetText": {"runs": [{"text": "desc"}]}}],
    }}]}}
    html = f"<script>var ytInitialData = {json.dumps(data)};</script>"
    res = parse_youtube_html(html, "q", "youtube")
    assert len(res) == 1
    r = res[0]
    assert r.url == "https://www.youtube.com/watch?v=abc" and r.title == "HL2 trailer"
    assert r.author == "Valve" and r.meta["duration"] == "2:01" and r.snippet == "desc"
    assert r.thumbnail.endswith("/abc/hqdefault.jpg")


def test_parse_bing_images():
    m = json.dumps({"murl": "https://img.x/shot.jpg", "purl": "https://page.x/", "t": "Shot", "turl": "https://t.x/1"})
    html = f'<a class="iusc" m=\'{m}\'></a><a class="iusc" m="{{bad json"></a>'
    res = parse_bing_images(html, "q", "bing_images")
    assert len(res) == 1 and res[0].meta["page_url"] == "https://page.x/" and res[0].kind == "image"


def test_parse_sourcegraph_stream():
    matches = [
        {"type": "content", "path": "src/game.cpp", "repository": "github.com/o/r", "commit": "abc",
         "lineMatches": [{"line": 'auto t = "Half-Life 2";', "lineNumber": 9}], "language": "C++", "repoStars": 5},
        {"type": "repo", "repository": "github.com/o/r2", "description": "HL2 tools", "repoStars": 7},
    ]
    body = f"event: progress\ndata: {{}}\n\nevent: matches\ndata: {json.dumps(matches)}\n\nevent: done\ndata: {{}}\n\n"
    res = list(parse_sourcegraph(body, "q", "sourcegraph"))
    assert res[0].url == "https://github.com/o/r/blob/abc/src/game.cpp#L10"
    assert res[0].kind == "code" and "Half-Life 2" in res[0].snippet and res[0].meta["language"] == "C++"
    assert res[1].kind == "repo" and res[1].url == "https://github.com/o/r2"


def test_reddit_image_and_gallery_posts():
    post = {"permalink": "/r/hl/comments/1/x/", "title": "My screenshot", "score": 10, "num_comments": 2,
            "subreddit": "HalfLife", "created_utc": 1700000000, "post_hint": "image",
            "url_overridden_by_dest": "https://i.redd.it/a.png", "thumbnail": "https://t.redd.it/a.jpg"}
    res = list(reddit_results(post, "q", "reddit"))
    kinds = [r.kind for r in res]
    assert kinds == ["discussion", "image"]
    assert res[1].meta["page_url"].startswith("https://www.reddit.com/r/hl/")


def test_repo_result():
    r = repo_result({"full_name": "o/r", "html_url": "https://github.com/o/r", "description": "d",
                     "stargazers_count": 42, "topics": ["hl2"], "language": "C++", "pushed_at": "2024-01-02T00:00:00Z",
                     "owner": {"login": "o"}}, "q", "github")
    assert r.kind == "repo" and r.meta["stars"] == 42 and r.date == "2024-01-02" and r.meta["tags"] == ["hl2"]


def test_fandom_candidates():
    c = fandom_candidates(["Half-Life 2"])
    assert "halflife2" in c and "half-life" in c and "halflife" in c


PAGE = """<html lang="en"><head><title>T</title><meta property="og:title" content="Half-Life 2 modding">
<meta name="description" content="Desc"><meta property="og:image" content="/og.jpg">
<meta property="article:published_time" content="2004-11-16T00:00:00Z"></head>
<body><nav>menu junk</nav><article><h1>Modding</h1><p>Some text about the Source engine.</p>
<a href="/shots/big1.jpg"><img src="/shots/small1.jpg" alt="City 17" width="200" height="120"></a>
<img src="/img/logo.png" width="300"><img src="/img/icon.png" width="16" height="16" alt="x">
<pre><code>// sample mod code
void CBaseEntity::Spawn() {
    Precache();
}</code></pre>
<a href="/files/manual.pdf">Manual</a><a href="/files/tools.zip">Tools</a>
<a href="/wiki/half-life-2-maps">HL2 maps</a><a href="https://other.org/x">ext</a></article></body></html>"""


def test_extract_page():
    info = extract_page(PAGE, "https://site.org/guide/")
    assert info.title == "Half-Life 2 modding" and info.description == "Desc"
    assert info.og_image == "https://site.org/og.jpg" and info.published == "2004-11-16"
    urls = [i["url"] for i in info.images]
    assert "https://site.org/shots/big1.jpg" in urls          # full-size link preferred
    assert not any("icon.png" in u or "logo.png" in u for u in urls)
    assert {f["category"] for f in info.files} == {"document", "archive"}
    assert info.code and "CBaseEntity" in info.code[0]
    assert any(ln["url"].endswith("/wiki/half-life-2-maps") for ln in info.links)
    assert not any("other.org" in ln["url"] for ln in info.links)
    assert "menu junk" not in info.text and "Source engine" in info.text


def test_summarize_tree_detects_engines():
    t = summarize_tree(["MyGame.uproject", "Source/A.cpp", "Content/Map.umap", "Docs/format_spec.md", "tools/extract.py"])
    assert "Unreal Engine" in t["engine_hints"]
    assert t["files"] == 5 and "Docs/format_spec.md" in t["notable"]
