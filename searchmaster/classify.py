"""Guess what kind of thing a URL points to."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff", ".avif"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus"}
DOC_EXT = {".pdf", ".txt", ".md", ".doc", ".docx", ".odt", ".rtf", ".epub", ".csv", ".json", ".xml", ".xls", ".xlsx",
           ".ppt", ".pptx"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
CODE_EXT = {".c", ".h", ".cpp", ".hpp", ".cs", ".py", ".js", ".ts", ".lua", ".java", ".go", ".rs", ".gd", ".glsl",
            ".hlsl", ".shader", ".asm", ".s", ".ini", ".cfg", ".yaml", ".yml", ".toml"}
# Never downloaded automatically, even when found.
DANGEROUS_EXT = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".dll", ".ps1", ".vbs", ".js", ".jar", ".apk", ".app",
                 ".dmg", ".pkg", ".deb", ".rpm", ".sh", ".iso", ".bin", ".appimage", ".lnk", ".hta", ".reg"}

FILE_EXT = DOC_EXT | ARCHIVE_EXT | CODE_EXT

CODE_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "sourceforge.net", "sr.ht", "gitee.com")
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "rutube.ru", "vk.com/video", "dailymotion.com")
DISCUSSION_HOSTS = ("reddit.com", "stackexchange.com", "stackoverflow.com", "news.ycombinator.com", "forum", "discord",
                    "steamcommunity.com/app", "resetera.com", "pikabu.ru", "habr.com", "4pda", "dtf.ru")
WIKI_HOSTS = ("wikipedia.org", "fandom.com", "wiki.gg", "wikia.com", "pcgamingwiki.com", "wiki")
MOD_HOSTS = ("nexusmods.com", "moddb.com", "gamebanana.com", "modrinth.com", "curseforge.com", "thunderstore.io",
             "steamcommunity.com/sharedfiles")
NEWS_HOSTS = ("news", "ign.com", "gamespot.com", "kotaku.com", "pcgamer.com", "polygon.com", "eurogamer", "stopgame.ru",
              "igromania.ru", "rockpapershotgun.com", "theverge.com")


def url_ext(url: str) -> str:
    path = urlsplit(url).path.lower()
    return os.path.splitext(path)[1]


def classify_url(url: str) -> str:
    if not url:
        return "article"
    ext = url_ext(url)
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in FILE_EXT or ext in DANGEROUS_EXT:
        return "file"
    low = url.lower()
    host_path = low.split("://", 1)[-1]
    for hosts, cat in (
        (MOD_HOSTS, "mod"),
        (CODE_HOSTS, "code"),
        (VIDEO_HOSTS, "video"),
        (WIKI_HOSTS, "wiki"),
        (DISCUSSION_HOSTS, "discussion"),
        (NEWS_HOSTS, "news"),
    ):
        if any(h in host_path for h in hosts):
            return cat
    if "arxiv.org" in low or "doi.org" in low or "scholar" in low:
        return "paper"
    return "article"
