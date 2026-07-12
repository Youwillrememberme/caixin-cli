"""Search Caixin articles via SearXNG-free, keyless web search.

`search.caixin.com` is a JavaScript SPA whose API base is injected at runtime,
and the major search engines are geo/anti-bot constrained (Bing's `cn.bing.com`
ignores the `site:` operator; DuckDuckGo and Google are unreachable from China;
Baidu returns an anti-bot page to bare HTTP clients).

**Sogou** (sogou.com) is a Chinese search engine that works from China, honors the
`site:` operator, and embeds the real Caixin article URLs directly in its result
HTML - so no redirect decoding is needed. We query Sogou with
`site:caixin.com <query>` and extract the paired (title, caixin URL, snippet).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from ..client import CaixinClient, CaixinError
from ..models import SearchResult
from ..utils import is_caixin_url

_SOGOU = "https://www.sogou.com/web"
_ARTICLE_RE = re.compile(r"https?://[\w-]+\.caixin\.com/\d{4}-\d{2}-\d{2}/\d+\.html")


def search_caixin(client: CaixinClient, query: str, limit: int = 20) -> list[SearchResult]:
    """Search Caixin via Sogou `site:caixin.com` query. Returns deduped hits."""
    results: list[SearchResult] = []
    seen: set[str] = set()
    q = f"site:caixin.com {query}".strip()
    for page in range(1, 4):
        if len(results) >= limit:
            break
        params = {"query": q, "page": page}
        try:
            html = _fetch(client, _SOGOU, params)
        except CaixinError:
            break
        hits = _parse_sogou(html)
        if not hits:
            break
        for title, url, snippet in hits:
            if not is_caixin_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append(SearchResult(title=title, url=url, snippet=snippet))
            if len(results) >= limit:
                break
    return results


def _fetch(client: CaixinClient, url: str, params: dict) -> str:
    """GET a search-engine page via the client's session."""
    r = client._client.get(
        url,
        params=params,
        headers={
            "Referer": "https://www.sogou.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    if r.status_code >= 400:
        raise CaixinError(f"search engine HTTP {r.status_code}")
    return r.content.decode("utf-8", errors="replace")


def _clean_title(title: str) -> str:
    """Strip Sogou's trailing source tags like '_财新网', '_ 财新周刊 频道'."""
    prev = None
    while prev != title:
        prev = title
        title = re.sub(r"[\s_]+(财新周刊|财新网)(\s*频道)?\s*$", "", title).strip()
    return title


def _parse_sogou(html: str) -> list[tuple[str, str, str]]:
    """Extract (title, caixin_article_url, snippet) from a Sogou results page."""
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, str, str]] = []
    for blk in soup.select("div.vrwrap"):
        h = blk.select_one("h3.vr-title a") or blk.select_one("h3 a")
        title = h.get_text(" ", strip=True) if h else ""
        title = _clean_title(title)
        urls = _ARTICLE_RE.findall(blk.decode_contents())
        if not title or not urls:
            continue
        url = urls[0]
        # snippet: block text minus the title, trimmed
        snippet = blk.get_text(" ", strip=True)
        snippet = snippet.replace(title, "", 1).strip()
        out.append((title, url, snippet[:200]))
    return out
