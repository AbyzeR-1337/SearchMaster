"""Small text helpers: tokenizing, stop-words, HTML stripping, slugs."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone

WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
ACRONYM_RE = re.compile(r"(?<![^\W_])((?:[^\W\d_]\.){2,}[^\W\d_]?)", re.UNICODE)  # S.T.A.L.K.E.R. -> STALKER
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

STOPWORDS = set(
    """
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
    ist von den dem ein eine nicht auch
    """.split()
)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    text = ACRONYM_RE.sub(lambda m: m.group(1).replace(".", ""), text)
    return [w.lower() for w in WORD_RE.findall(text)]


def content_words(text: str | None, min_len: int = 3) -> list[str]:
    return [w for w in tokenize(text) if len(w) >= min_len and w not in STOPWORDS and not w.isdigit()]


def query_terms(query: str) -> list[str]:
    tokens = tokenize(query)
    terms = [w for w in tokens if (w not in STOPWORDS or len(tokens) <= 2) and (len(w) > 1 or w.isdigit())]
    return terms or tokens


def is_latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and all("a" <= ch.lower() <= "z" for ch in letters)


def truncate(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


_TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
         "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
    )
)


def slugify(text: str, max_len: int = 60) -> str:
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:max_len].rstrip("-")) or "search"


def ts_to_iso(value: float | int | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def year_of(date: str | None) -> int | None:
    if not date:
        return None
    match = re.search(r"(19[5-9]\d|20\d\d)", date)
    return int(match.group(1)) if match else None


def stem(term: str) -> str:
    """Crude stemming so that "компьютеры" also matches "компьютерах", "knight" — "knights"."""
    return term[: max(4, len(term) - 2)] if len(term) > 5 else term


def token_match(a: str, b: str) -> bool:
    return a == b or b.startswith(stem(a)) or a.startswith(stem(b))


def similarity(a: str, b: str) -> float:
    """Stem-aware token-set similarity between two titles (0..1)."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    matched = sum(1 for x in ta if any(token_match(x, y) for y in tb))
    return matched / (len(ta) + len(tb) - matched)
