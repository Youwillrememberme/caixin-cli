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

from ..client import CaixinClient, CaixinError
from ..models import ChannelArticle

_ART = re.compile(r"https?://[\w-]+\.caixin\.com/(?:m/)?\d{4}-\d{2}-\d{2}/\d+\.html")
_COMMENT = re.compile(r"^评论\s*\(\s*\d+\s*\)")
_DATE = re.compile(r"/(\d{4}-\d{2}-\d{2})/")
_LOADMORE = re.compile(r"loadMoreNews\w*\(\s*\d+\s*,\s*(\d+)\s*,\s*\d+\s*,\s*(\d+)\s*\)")

# AJAX "load more" endpoint (subdomain channels only). Discovered from
# channel.js: loadMoreNewses(t, channelId, pageIdx, count) calls
# gateway.caixin.com/api/extapi/homeInterface.jsp?channel=<id>&start=<p*count>&count=<count>
_HOME_API = "https://gateway.caixin.com/api/extapi/homeInterface.jsp"

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


def extract_loadmore(html: str) -> Optional[tuple[int, int]]:
    """Extract (channel_id, count) from a `loadMoreNewses(0,id,1,count)` call.

    Subdomain channel pages embed this call to drive their AJAX "load more";
    path-based channels and the homepage do not, so this returns None for them.
    """
    m = _LOADMORE.search(html)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _fetch_page(client: CaixinClient, channel_id: int, start: int, count: int) -> tuple[list[ChannelArticle], int]:
    """Fetch one AJAX page of articles; return (articles, maxes)."""
    obj = client.get_jsonp(_HOME_API, params={
        "channel": channel_id, "start": start, "count": count,
        "picdim": "_145_97", "callback": "cb",
    })
    maxes = int(obj.get("maxes") or 0)
    arts: list[ChannelArticle] = []
    for d in obj.get("datas") or []:
        link = (d.get("link") or "").strip()
        title = (d.get("desc") or d.get("summ") or "").strip()
        time = (d.get("time") or "").strip()
        date = time[:10] if time else ""
        if not date:
            dm = _DATE.search(link)
            date = dm.group(1) if dm else ""
        if link and title:
            arts.append(ChannelArticle(date=date, title=title, url=link))
    return arts, maxes


def list_channel_articles(client: CaixinClient, channel_url: str, limit: int = 20) -> tuple[list[ChannelArticle], bool]:
    """List recent articles for a channel, paginating via AJAX when supported.

    Returns (articles, paginated). Subdomain channels paginate through the
    gateway "load more" API (up to ~1000 articles); path-based channels and the
    homepage fall back to the single page's HTML articles (no pagination).
    """
    html = client.get_html(channel_url)
    lm = extract_loadmore(html)
    if lm:
        channel_id, count = lm
        arts: list[ChannelArticle] = []
        start = 0
        maxes = 1  # enter loop; set by first page
        while len(arts) < limit:
            page_arts, maxes = _fetch_page(client, channel_id, start, count)
            if not page_arts:
                break
            arts.extend(page_arts)
            start += count
            if maxes and start >= maxes:
                break
        return arts[:limit], True
    # fallback: parse the single page's HTML
    return parse_channel_articles(html)[:limit], False
