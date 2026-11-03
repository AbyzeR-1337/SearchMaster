"""Source code: GitHub, GitLab, Codeberg and Sourcegraph (code search across ~all public repos)."""

from __future__ import annotations

import json
from typing import Iterator

from ..models import CODE, REPO, Result
from ..utils import slugify, strip_html, truncate
from .base import Source

CODE_FACETS = frozenset({"code", "technical", "files"})


class GitHubSource(Source):
    name = "github"
    description = "GitHub: репозитории, темы (topics); с GITHUB_TOKEN — ещё и поиск по коду"
    facets = CODE_FACETS

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.config.github_token:
            h["Authorization"] = f"Bearer {self.config.github_token}"
        return h

    def search(self, plan) -> Iterator[Result]:
        authed = bool(self.config.github_token)
        # Unauthenticated search allows ~10 requests/minute, so stay frugal.
        queries = plan.api_queries("code", limit=5 if authed else 3)
        if not plan.snowball_only:
            queries.append(f"topic:{slugify(plan.topic)}")
        delay = 2.5 if authed else 6.5
        for q in queries:
            data = self.http.get_json(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": min(self.limit, 50)},
                headers=self._headers(), delay=delay,
            )
            for item in data.get("items", []):
                yield repo_result(item, q, self.name)
        if authed and not plan.snowball_only:
            try:
                yield from self._code_search(plan, delay)
            except Exception as exc:  # e.g. a token without code-search rights: keep the repo results
                self.log.warning("GitHub code search unavailable: %s", exc)

    def _code_search(self, plan, delay: float) -> Iterator[Result]:
        for name in plan.search_names[:2]:
            q = f'"{name}"' if " " in name else name
            data = self.http.get_json(
                "https://api.github.com/search/code",
                params={"q": q, "per_page": min(self.limit, 50)},
                headers=self._headers("application/vnd.github.text-match+json"), delay=delay,
            )
            for item in data.get("items", []):
                repo = item.get("repository", {})
                frags = [m.get("fragment", "") for m in item.get("text_matches", []) if m.get("fragment")]
                yield self.result(
                    item["html_url"], f"{repo.get('full_name', '')}/{item.get('path', '')}", query=q, kind=CODE,
                    snippet=truncate(" … ".join(frags), 600),
                    meta={"repo": repo.get("full_name"), "path": item.get("path"),
                          "code_snippets": frags[:3], "file_name": item.get("name")},
                )


def repo_result(item: dict, q: str, source: str) -> Result:
    full = item.get("full_name", "")
    return Result(
        url=item.get("html_url", f"https://github.com/{full}"), title=full, kind=REPO, source=source, query=q,
        snippet=item.get("description") or "",
        thumbnail=f"https://opengraph.githubassets.com/1/{full}" if full else None,
        date=(item.get("pushed_at") or item.get("updated_at") or "")[:10] or None,
        author=(item.get("owner") or {}).get("login"),
        meta={
            "stars": item.get("stargazers_count", 0), "forks": item.get("forks_count", 0),
            "language": item.get("language"), "tags": item.get("topics", []),
            "license": (item.get("license") or {}).get("spdx_id"), "size_kb": item.get("size"),
            "default_branch": item.get("default_branch"), "repo": full,
            "archived": item.get("archived"), "created": (item.get("created_at") or "")[:10],
            "homepage": item.get("homepage"),
        },
    )


class GitLabSource(Source):
    name = "gitlab"
    description = "GitLab.com: публичные проекты"
    facets = CODE_FACETS

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("code", limit=3):
            items = self.http.get_json(
                "https://gitlab.com/api/v4/projects",
                params={"search": q, "per_page": min(self.limit, 50), "simple": "true"},
            )
            for p in items:
                yield self.result(
                    p["web_url"], p.get("name_with_namespace", p.get("name", "")), query=q, kind=REPO,
                    snippet=p.get("description") or "", thumbnail=p.get("avatar_url"),
                    date=(p.get("last_activity_at") or "")[:10] or None,
                    meta={"stars": p.get("star_count", 0), "forks": p.get("forks_count", 0),
                          "tags": p.get("topics") or p.get("tag_list") or [], "repo": p.get("path_with_namespace")},
                )


class CodebergSource(Source):
    name = "codeberg"
    description = "Codeberg (Gitea/Forgejo): открытые репозитории"
    facets = CODE_FACETS

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("code", limit=2):
            data = self.http.get_json(
                "https://codeberg.org/api/v1/repos/search",
                params={"q": q, "limit": min(self.limit, 50), "sort": "stars", "order": "desc"},
            )
            for p in data.get("data", []):
                yield self.result(
                    p["html_url"], p.get("full_name", ""), query=q, kind=REPO,
                    snippet=p.get("description") or "", thumbnail=p.get("avatar_url"),
                    date=(p.get("updated_at") or "")[:10] or None,
                    meta={"stars": p.get("stars_count", 0), "forks": p.get("forks_count", 0),
                          "language": p.get("language"), "tags": p.get("topics") or [], "repo": p.get("full_name")},
                )


def parse_sse(text: str) -> Iterator[tuple[str, str]]:
    """Yield (event, data) pairs from a text/event-stream body."""
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            yield event, "\n".join(data)


def _repo_web_url(repository: str) -> str:
    return repository if repository.startswith("http") else f"https://{repository}"


class SourcegraphSource(Source):
    name = "sourcegraph"
    description = "Sourcegraph: поиск строк кода по миллионам публичных репозиториев (без ключа)"
    facets = CODE_FACETS

    def search(self, plan) -> Iterator[Result]:
        names = [] if plan.snowball_only else plan.search_names[:3]
        for name in names:
            literal = f'"{name}"' if " " in name else name
            for q in (f"{literal} count:{self.limit * 2}", f"type:repo {literal} count:{self.limit}"):
                resp = self.http.get(
                    "https://sourcegraph.com/.api/search/stream",
                    params={"q": q, "display": self.limit * 2},
                    headers={"Accept": "text/event-stream"}, timeout=40,
                )
                yield from parse_sourcegraph(resp.text, q, self.name)


def parse_sourcegraph(text: str, q: str, source: str) -> Iterator[Result]:
    for event, data in parse_sse(text):
        if event != "matches":
            continue
        try:
            matches = json.loads(data)
        except ValueError:
            continue
        for m in matches:
            repo = m.get("repository", "")
            base = _repo_web_url(repo)
            if m.get("type") == "repo":
                yield Result(url=base, title=repo.split("/", 1)[-1], kind=REPO, source=source, query=q,
                             snippet=strip_html(m.get("description") or ""),
                             meta={"stars": m.get("repoStars", 0), "repo": repo})
                continue
            if m.get("type") not in ("content", "path"):
                continue
            path = m.get("path", "")
            lines: list[tuple[int, str]] = []
            for lm in m.get("lineMatches") or []:
                lines.append((lm.get("lineNumber", 0) + 1, lm.get("line", "")))
            for cm in m.get("chunkMatches") or []:
                start = (cm.get("contentStart") or {}).get("line", 0) + 1
                lines.append((start, cm.get("content", "")))
            commit = m.get("commit") or "HEAD"
            anchor = f"#L{lines[0][0]}" if lines else ""
            if "github.com/" in repo or "gitlab.com/" in repo:
                sep = "/-/blob/" if "gitlab.com/" in repo else "/blob/"
                url = f"{base}{sep}{commit}/{path}{anchor}"
            else:
                url = f"https://sourcegraph.com/{repo}/-/blob/{path}{anchor}"
            snippets = [f"L{n}: {line.strip()}" for n, line in lines[:5]]
            yield Result(
                url=url, title=f"{repo.split('/', 1)[-1]}/{path}", kind=CODE, source=source, query=q,
                snippet=truncate(" | ".join(snippets), 600),
                meta={"repo": repo, "path": path, "language": m.get("language"),
                      "stars": m.get("repoStars", 0), "code_snippets": [line for _, line in lines[:5]]},
            )
