import json
from pathlib import Path

from searchmaster import ci
from searchmaster import config as config_mod
from searchmaster import engine as engine_mod
from searchmaster.config import config_from_params
from searchmaster.models import Result
from searchmaster.sources.base import Source

FORM_BODY = """### Что ищем

Hollow Knight

### Другие названия (через запятую)

Холлоу Найт, HK

### Глубина

Глубоко

### Аспекты (если ничего не отмечено — ищем всё)

- [X] Скриншоты
- [ ] Видео
- [x] Код

### Скачать найденное (попадёт в архив запуска)

- [X] Картинки
- [ ] Документы

### ИИ-сводка

- [ ] Добавить аналитическую сводку Claude (нужен секрет ANTHROPIC_API_KEY)
"""


def test_params_from_issue_form():
    p = ci.params_from_issue("search: ", FORM_BODY)
    assert p["query"] == "Hollow Knight"
    assert p["aliases"] == "Холлоу Найт, HK"
    assert p["preset"] == "deep"
    assert p["facets"] == ["images", "code"]
    assert p["download"] == ["images"]
    assert p["ai"] is False
    cfg = config_from_params(p, out_dir="results")
    assert cfg.aliases == ["Холлоу Найт", "HK"] and cfg.deep == 50 and cfg.crawl_depth == 1


def test_params_from_plain_issue():
    p = ci.params_from_issue("Поиск: S.T.A.L.K.E.R.", "preset: быстро\nfacets: code,images\nunrelated text")
    assert p["query"] == "S.T.A.L.K.E.R." and p["preset"] == "fast" and p["facets"] == "code,images"
    empty_form = ci.params_from_issue("search: Doom", FORM_BODY.replace("Hollow Knight", "_No response_"))
    assert empty_form["query"] == "Doom"          # falls back to the title


def test_params_from_inputs_and_validation():
    p = ci.params_from_inputs({"query": " Doom\n1993 ", "preset": "fast", "facets": "all", "download": "none",
                               "ai": "true", "save_report": "false"})
    assert p["query"] == "Doom 1993" and p["facets"] == "" and p["download"] == "" and p["ai"] is True
    assert p["save_report"] is False
    cfg = config_from_params(p, out_dir="x")
    assert cfg.facets and cfg.download == [] and cfg.ai is True and cfg.rounds == 1
    bad = config_from_params({"query": "x", "facets": "code,evil", "download": "repos,rm -rf",
                              "languages": "en,../../,ru"}, out_dir="x")
    assert bad.facets == ["code"] and bad.download == ["repos"] and bad.languages == ["en", "ru"]


def test_neutralize_mentions_and_refs():
    text = "@octocat see #12 and mail a@b.com https://mastodon.social/@user/1 &#39;"
    out = ci.neutralize(text)
    assert "@octocat" not in out and "@​octocat" in out
    assert "#​12" in out
    assert "a@b.com" in out and "/@user/1" in out and "&#39;" in out


def test_outputs_are_single_line(tmp_path, monkeypatch):
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ci.set_outputs(query="Doom\nevil=1", total=3)
    lines = out.read_text().splitlines()
    assert lines == ["query=Doom evil=1", "total=3"]   # no injected extra output


class FakeSource(Source):
    name = "bing"
    facets = frozenset({"general", "images", "code"})

    def search(self, plan):
        for i in range(4):
            yield self.result(f"https://ex{i}.com/hk", f"Hollow Knight guide {i} by @someone",
                              snippet="Team Cherry metroidvania Hallownest Knight game", kind="page")
        yield self.result("https://github.com/a/hk-mod", "a/hk-mod", snippet="Hollow Knight mod Team Cherry",
                          kind="repo", meta={"stars": 42, "language": "C#"})
        yield Result(url="https://cdn.ex/hk.png", title="Hollow Knight screenshot", kind="image", source="bing")


def test_run_and_prepare_site(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "select_sources", lambda cfg: ([FakeSource], []))
    monkeypatch.setattr(config_mod, "PRESETS", {k: {**v, "deep": 0} for k, v in config_mod.PRESETS.items()})
    monkeypatch.chdir(tmp_path)
    event = {"issue": {"number": 7, "title": "search: Hollow Knight", "body": "preset: fast"},
             "repository": {"has_pages": True}}
    (tmp_path / "event.json").write_text(json.dumps(event), encoding="utf-8")
    out = tmp_path / "out.txt"
    for k, v in {"GITHUB_EVENT_NAME": "issues", "GITHUB_EVENT_PATH": str(tmp_path / "event.json"),
                 "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
                 "GITHUB_REPOSITORY": "Owner/SearchMaster", "GITHUB_RUN_ID": "99",
                 "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)

    assert ci.main(["run"]) == 0
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    run_dir = Path(outputs["run_dir"])
    assert outputs["save_report"] == "true" and int(outputs["total"]) >= 5
    assert (run_dir / "README.md").exists() and (run_dir / "report.html").exists()

    comment = Path(outputs["comment_file"]).read_text(encoding="utf-8")
    assert "### SearchMaster: Hollow Knight" in comment
    assert "/tree/searchmaster-reports/reports/" in comment                     # link to the full report
    assert "https://owner.github.io/SearchMaster/reports/" in comment            # Pages link (has_pages)
    assert "/actions/runs/99" in comment and "★ 42" in comment and "<img" in comment
    assert "@someone" not in comment                                           # no pings from scraped text
    assert "/issues/new?template=searchmaster.yml" in comment                    # "новый поиск" link
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == comment + "\n"

    site = tmp_path / "site"
    assert ci.main(["prepare-site", str(run_dir), str(site)]) == 0
    assert (site / "reports" / run_dir.name / "report.html").exists()
    readme = (site / "README.md").read_text(encoding="utf-8")
    assert "Hollow Knight" in readme and f"reports/{run_dir.name}" in readme
    assert "Owner/SearchMaster/issues/new?template=searchmaster.yml" in readme
    assert (site / "index.html").exists() and (site / "icon-192.png").exists() and (site / ".nojekyll").exists()


def test_prepare_site_skips_huge_downloads(tmp_path):
    run = tmp_path / "results" / "doom-20260101-000000"
    (run / "downloads" / "image").mkdir(parents=True)
    (run / "results.json").write_text(json.dumps({"topic": "Doom", "started": "2026-01-01T00:00:00",
                                                  "analysis": {"total": 1, "by_kind": {"page": 1}}}))
    (run / "downloads" / "image" / "big.bin").write_bytes(b"0" * 2048)
    site = tmp_path / "site"
    assert ci.cmd_prepare_site(str(run), str(site), max_download_mb=0.001) == 0
    dest = site / "reports" / run.name
    assert not (dest / "downloads").exists() and (dest / "DOWNLOADS.md").exists()


def test_bad_query_reports_error(tmp_path, monkeypatch):
    (tmp_path / "event.json").write_text(json.dumps({"issue": {"title": "search:", "body": ""}}))
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issues")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    assert ci.main(["run"]) == 1
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert "search: Hollow Knight" in Path(outputs["comment_file"]).read_text(encoding="utf-8")
