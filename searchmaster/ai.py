"""Optional AI layer (Claude via the official Anthropic SDK).

* `expand_query`  — before searching: alternative titles, original-language names,
                    abbreviations, developer, related terms.
* `summarize`     — after analysis: a structured research brief in Russian.

Everything here is optional: if `anthropic` is not installed or no credentials
are configured, the caller gets a clear message and the search runs without AI.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .models import Result
from .utils import truncate

log = logging.getLogger("searchmaster.ai")

# Server-side fallback: if a safety classifier declines, the API retries on the
# recommended fallback model inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "aliases": {"type": "array", "items": {"type": "string"},
                    "description": "Alternative / original-language titles and common abbreviations"},
        "related_terms": {"type": "array", "items": {"type": "string"},
                          "description": "Short terms that find more specific material: developer, engine, characters, modding tools"},
        "is_game": {"type": "boolean"},
    },
    "required": ["aliases", "related_terms", "is_game"],
    "additionalProperties": False,
}


class AIUnavailable(RuntimeError):
    pass


def _client():
    try:
        import anthropic
    except ImportError as exc:
        raise AIUnavailable("пакет anthropic не установлен: pip install anthropic") from exc
    try:
        return anthropic, anthropic.Anthropic()
    except Exception as exc:  # missing credentials etc.
        raise AIUnavailable(f"не удалось создать клиента Anthropic: {exc}") from exc


def _final_text(message: Any) -> str:
    if getattr(message, "stop_reason", None) == "refusal":
        details = getattr(message, "stop_details", None)
        raise AIUnavailable(f"модель отказалась отвечать ({getattr(details, 'category', None)})")
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()


def _call(model: str, effort: str, system: str, prompt: str, *, max_tokens: int,
          schema: dict | None = None) -> str:
    anthropic, client = _client()
    output_config: dict[str, Any] = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config=output_config,
            betas=[_FALLBACK_BETA],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable("неверный или отсутствующий ANTHROPIC_API_KEY") from exc
    except anthropic.RateLimitError as exc:
        raise AIUnavailable("превышен лимит запросов к API, попробуйте позже") from exc
    except anthropic.APIStatusError as exc:
        raise AIUnavailable(f"ошибка API ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailable("нет соединения с API Anthropic") from exc
    return _final_text(message)


def expand_query(topic: str, *, model: str, effort: str = "low") -> dict[str, Any]:
    system = ("You help a research crawler. Given a topic (often a video game), list alternative "
              "titles (original Japanese/Chinese/Russian names, abbreviations, working titles) and short "
              "related search terms that surface screenshots, technical docs, source code, mods and history. "
              "Only include names you are confident are real. Keep each item under 6 words.")
    text = _call(model, effort, system, f"Topic: {topic}", max_tokens=4000, schema=EXPAND_SCHEMA)
    data = json.loads(text)
    data["aliases"] = [a for a in data.get("aliases", []) if a.strip().lower() != topic.lower()][:6]
    data["related_terms"] = data.get("related_terms", [])[:8]
    return data


def _digest(results: list[Result], analysis: dict[str, Any], limit: int = 60) -> str:
    lines = []
    for i, r in enumerate(results[:limit], 1):
        body = truncate((r.content or r.snippet or "").replace("\n", " "), 700)
        extra = []
        for key in ("stars", "developers", "release_date", "engine", "language", "views"):
            if r.meta.get(key):
                extra.append(f"{key}={r.meta[key]}")
        lines.append(f"[{i}] ({r.kind}, {r.source}) {r.title}\nURL: {r.url}\n{' '.join(extra)}\n{body}")
    facts = {k: analysis.get(k) for k in (
        "by_kind", "engines", "repo_engines", "platforms", "developers", "publishers",
        "timeline", "file_types", "code_languages", "entities", "keywords", "official_sites")}
    return json.dumps(facts, ensure_ascii=False, default=str)[:12000] + "\n\n" + "\n\n".join(lines)


def summarize(topic: str, results: list[Result], analysis: dict[str, Any], *, model: str, effort: str) -> str:
    system = (
        "Ты аналитик-исследователь. По собранным материалам составь отчёт на русском языке в Markdown. "
        "Опирайся только на переданные источники и ссылайся на них номерами [n]. Если чего-то в "
        "источниках нет — так и напиши, не выдумывай. Структура: "
        "1) Кратко о теме; 2) Ключевые факты (разработчик, даты, движок, платформы); "
        "3) Где лучшие скриншоты и медиа; 4) Исходный код, моды и инструменты (репозитории, что в них); "
        "5) Файлы и документы (мануалы, форматы файлов, архивы); 6) Сообщества и обсуждения; "
        "7) Хронология; 8) Пробелы и что искать дальше (конкретные запросы)."
    )
    prompt = f"Тема: {topic}\n\nМатериалы (отсортированы по релевантности):\n\n{_digest(results, analysis)}"
    return _call(model, effort, system, prompt, max_tokens=32000)
