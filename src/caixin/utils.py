"""URL parsing and filename helpers."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from slugify import slugify

# Article URL pattern:
#   https://weekly.caixin.com/2026-07-10/102462604.html
#   https://china.caixin.com/2026-07-09/102462521.html
#   https://m.weekly.caixin.com/m/2026-07-11/102463000.html  (mobile)
#   https://www.caixin.com/2026-07-09/102462336.html
_ARTICLE_RE = re.compile(
    r"^https?://"
    r"(?:m\.)?"                                   # optional mobile subdomain
    r"(?P<channel>[\w-]+)"                         # e.g. weekly, china, www
    r"\.caixin\.com"
    r"(?:/m)?"                                     # optional mobile path prefix
    r"/(?P<date>\d{4}-\d{2}-\d{2})/"
    r"(?P<aid>\d+)\.html"
)

# Weekly issue index URL pattern:
#   https://weekly.caixin.com/2026/cw1214/
_ISSUE_RE = re.compile(
    r"^https?://weekly\.caixin\.com/(?P<year>\d{4})/cw(?P<total>\d+)/?$"
)


def parse_article_url(url: str) -> dict | None:
    """Return {channel, date, aid} for a Caixin article URL, or None."""
    m = _ARTICLE_RE.match(url.strip())
    if not m:
        return None
    return {
        "channel": m.group("channel"),
        "date": m.group("date"),
        "aid": int(m.group("aid")),
    }


def parse_issue_url(url: str) -> dict | None:
    """Return {year, total} for a weekly issue index URL, or None."""
    m = _ISSUE_RE.match(url.strip())
    if not m:
        return None
    return {"year": int(m.group("year")), "total": int(m.group("total"))}


def is_caixin_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host == "caixin.com" or host.endswith(".caixin.com")


def safe_filename(s: str, maxlen: int = 80) -> str:
    """Make a filesystem-safe filename, preserving CJK characters and case."""
    if not s:
        return "untitled"
    s = slugify(s, allow_unicode=True, lowercase=False)
    s = (s or "untitled")[:maxlen].rstrip("-_ ")
    return s or "untitled"


def normalize_article_url(url: str) -> str:
    """Strip query/fragment and collapse mobile to desktop article URL."""
    url = url.split("#")[0].split("?")[0].strip()
    # mobile -> desktop: m.weekly.caixin.com/m/<date>/<id>.html -> weekly.caixin.com/<date>/<id>.html
    m = re.match(r"^(https?://)m\.([\w-]+)\.caixin\.com/m/(.+)$", url)
    if m:
        url = f"{m.group(1)}{m.group(2)}.caixin.com/{m.group(3)}"
    return url
