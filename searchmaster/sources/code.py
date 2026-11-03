"""Source-code hosting: GitHub (repos, code, issues), GitLab, Codeberg."""

from __future__ import annotations

from ..models import Result
from ..textutil import truncate
from .base import Source


class GitHub(Source):
    name = "github"
    title = "GitHub"
    description = "Репозитории, исходный код (нужен токен) и обсуждения в issues"
    weight = 1.2
    expandable = True

    def _headers(self, token: str | None) -> dict:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def search(self, query: str, limit: int) -> list[Result]:
        token = self.config.key("GH_SEARCH_TOKEN") or self.config.key("GITHUB_TOKEN")
        headers = self._headers(token)
        per_page = min(limit, 100)
        results = self._repos(query, per_page, headers)

        # Code search requires authentication; a personal token works best.
        code_token = self.config.key("GH_SEARCH_TOKEN") or self.config.key("GITHUB_TOKEN")
        if code_token:
            try:
                results += self._code(query, min(per_page, 30), self._headers(code_token))
            except Exception as exc:
                self.ctx.log.debug("github code search failed: %s", exc)
        try:
            results += self._issues(query, min(per_page, 30), headers)
        except Exception as exc:
            self.ctx.log.debug("github issues search failed: %s", exc)
        return results

    def _repos(self, query: str, per_page: int, headers: dict) -> list[Result]:
        data = self.http.get_json(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
            headers=headers,
        )
        out = []
        for repo in data.get("items", []):
            full = repo["full_name"]
            out.append(
                self.result(
                    url=repo["html_url"],
                    title=full,
                    category="code",
                    snippet=repo.get("description") or "",
                    thumbnail=f"https://opengraph.githubassets.com/1/{full}",
                    date=(repo.get("pushed_at") or "")[:10] or None,
                    author=(repo.get("owner") or {}).get("login"),
                    meta={
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "language": repo.get("language"),
                        "license": (repo.get("license") or {}).get("spdx_id"),
                        "topics": repo.get("topics", [])[:10],
                        "repo": full,
                        "branch": repo.get("default_branch"),
                        "size_kb": repo.get("size"),
                        "created": (repo.get("created_at") or "")[:10],
                        "archived": repo.get("archived"),
                    },
                )
            )
        return out

    def _code(self, query: str, per_page: int, headers: dict) -> list[Result]:
        data = self.http.get_json(
            "https://api.github.com/search/code",
            params={"q": query, "per_page": per_page},
            headers={**headers, "Accept": "application/vnd.github.text-match+json"},
        )
        out = []
        for item in data.get("items", []):
            repo = (item.get("repository") or {}).get("full_name", "")
            fragments = [m.get("fragment", "") for m in item.get("text_matches", [])]
            out.append(
                self.result(
                    url=item["html_url"],
                    title=f"{item.get('path')} — {repo}",
                    category="code",
                    snippet=truncate(" … ".join(fragments), 400),
                    meta={"repo": repo, "path": item.get("path"), "kind": "file"},
                )
            )
        return out

    def _issues(self, query: str, per_page: int, headers: dict) -> list[Result]:
        data = self.http.get_json(
            "https://api.github.com/search/issues",
            params={"q": query, "sort": "reactions", "per_page": per_page},
            headers=headers,
        )
        out = []
        for item in data.get("items", []):
            repo = item.get("repository_url", "").removeprefix("https://api.github.com/repos/")
            out.append(
                self.result(
                    url=item["html_url"],
                    title=f"{item.get('title')} ({repo})",
                    category="discussion",
                    snippet=truncate(item.get("body") or "", 300),
                    date=(item.get("created_at") or "")[:10] or None,
                    author=(item.get("user") or {}).get("login"),
                    meta={"comments": item.get("comments", 0), "repo": repo,
                          "reactions": (item.get("reactions") or {}).get("total_count", 0)},
                )
            )
        return out


class GitLab(Source):
    name = "gitlab"
    title = "GitLab"
    description = "Публичные проекты на gitlab.com"

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://gitlab.com/api/v4/projects",
            params={"search": query, "order_by": "star_count", "sort": "desc", "per_page": min(limit, 100), "simple": "true"},
        )
        return [
            self.result(
                url=p["web_url"],
                title=p.get("name_with_namespace") or p.get("name"),
                category="code",
                snippet=p.get("description") or "",
                thumbnail=p.get("avatar_url"),
                date=(p.get("last_activity_at") or "")[:10] or None,
                meta={"stars": p.get("star_count", 0), "forks": p.get("forks_count", 0)},
            )
            for p in data
        ]


class Codeberg(Source):
    name = "codeberg"
    title = "Codeberg"
    description = "Открытые проекты на codeberg.org"

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://codeberg.org/api/v1/repos/search",
            params={"q": query, "limit": min(limit, 50), "sort": "stars", "order": "desc"},
        )
        return [
            self.result(
                url=r["html_url"],
                title=r.get("full_name"),
                category="code",
                snippet=r.get("description") or "",
                thumbnail=r.get("avatar_url") or None,
                date=(r.get("updated_at") or "")[:10] or None,
                meta={"stars": r.get("stars_count", 0), "forks": r.get("forks_count", 0), "language": r.get("language")},
            )
            for r in data.get("data", [])
        ]
