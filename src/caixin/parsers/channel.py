"""Channel (板块) parsing: list recent articles on a Caixin channel homepage.

A "channel" is a Caixin section like 经济/金融/公司/政经/世界/观点/科技/..., served
either from a subdomain (``economy.caixin.com``) or a path on www
(``www.caixin.com/tech/``). Each channel homepage lists its recent articles as
cards whose HTML structure varies (``boxa`` / ``topNews`` / ``dl`` / ``dd`` /
``li.pr`` ...), so we use a structure-agnostic extraction: collect every
article URL on the page, dedupe, and pull the title via a small fallback chain
(link text -> ``<img alt>`` -> sibling heading), filtering out ``评论(N)``
comment-count links.
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ..client import CaixinError
from ..models import ChannelArticle

_ART = re.compile(r"https?://[\w-]+\.caixin\.com/(?:m/)?\d{4}-\d{2}-\d{2}/\d+\.html")
_COMMENT = re.compile(r"^评论\s*\(\s*\d+\s*\)")
_DATE = re.compile(r"/(\d{4}-\d{2}-\d{2})/")

# channel key -> (Chinese label, homepage URL)
CHANNELS: dict[str, tuple[str, str]] = {
    "home": ("首页", "https://www.caixin.com/"),
    "economy": ("经济", "https://economy.caixin.com/"),
    "finance": ("金融", "https://finance.caixin.com/"),
    "companies": ("公司", "https://companies.caixin.com/"),
    "china": ("政经", "https://china.caixin.com/"),
    "international": ("世界", "https://international.caixin.com/"),
    "opinion": ("观点", "https://opinion.caixin.com/"),
    "science": ("环科", "https://science.caixin.com/"),
    "tech": ("科技", "https://www.caixin.com/tech/"),
    "property": ("地产", "https://www.caixin.com/property/"),
    "auto": ("汽车", "https://www.caixin.com/auto/"),
    "consumer": ("消费", "https://www.caixin.com/consumer/"),
    "energy": ("能源", "https://www.caixin.com/energy/"),
    "health": ("健康", "https://www.caixin.com/health/"),
    "livelihood": ("民生", "https://www.caixin.com/livelihood/"),
    "esg": ("ESG", "https://www.caixin.com/esg/"),
    "datanews": ("数字说", "https://datanews.caixin.com/"),
    "cnreform": ("中国改革", "https://cnreform.caixin.com/"),
    "bijiao": ("比较", "https://bijiao.caixin.com/"),
}


def list_channels() -> list[tuple[str, str, str]]:
    """Return [(key, label, url), ...] for all known channels."""
    return [(k, label, url) for k, (label, url) in CHANNELS.items()]


def resolve_channel(name: str) -> tuple[str, str, str]:
    """Resolve a channel name or URL to (key, label, url). Raises on miss."""
    n = name.strip()
    if n.lower() in CHANNELS:
        label, url = CHANNELS[n.lower()]
        return n.lower(), label, url
    bare = n.rstrip("/")
    for k, (label, url) in CHANNELS.items():
        if url.rstrip("/") == bare:
            return k, label, url
    raise CaixinError(
        f"unknown channel: {name!r}. Run `caixin channel list` to see options."
    )


def parse_channel_articles(html: str) -> list[ChannelArticle]:
    """Extract recent articles from a channel homepage, newest first."""
    soup = BeautifulSoup(html, "lxml")
    items: dict[str, ChannelArticle] = {}
    for a in soup.find_all("a", href=_ART):
        href = a["href"].split("#")[0]
        m = _DATE.search(href)
        if not m or href in items:
            continue
        title = _title_of(a, href)
        if title and not _COMMENT.match(title):
            items[href] = ChannelArticle(date=m.group(1), title=title, url=href)
    return sorted(items.values(), key=lambda c: c.date, reverse=True)


def _title_of(a: Tag, url: str) -> str:
    """Best-effort title for an article link, falling back through sources."""
    txt = a.get_text(" ", strip=True)
    if txt and not _COMMENT.match(txt) and len(txt) > 2:
        return txt
    img = a.find("img") or (a.parent.find("img", alt=True) if a.parent else None)
    if img and img.get("alt"):
        return img["alt"].strip()
    # climb up to a heading that links to the same article
    p: Optional[Tag] = a.parent
    for _ in range(3):
        if p is None:
            break
        for h in p.find_all(["h3", "h4"], limit=3):
            if h.find("a", href=url):
                t = h.get_text(" ", strip=True)
                if t and not _COMMENT.match(t):
                    return t
        p = p.parent
    return ""
