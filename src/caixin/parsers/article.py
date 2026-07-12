"""Article parsing: metadata from server HTML + body from the content API."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup

from ..client import CaixinClient, CaixinError
from ..models import Article
from ..utils import normalize_article_url, parse_article_url

# Noise selectors to strip from the server-rendered #Main_Content_Val teaser
# and from any fallback body HTML.
_NOISE_SELECTORS = [
    "script", "style", "iframe", "noscript",
    ".bd_block", ".chargeWall", "#chargeWall", "#content_msg",
    ".aitt", ".pip_none", "#pageBtn",
    ".relate-reading", ".xgydBox", ".recommend", ".tuijian",
    ".qr_code", ".qrcode", ".wx-share", ".share",
]


def parse_meta(html: str, url: str) -> dict:
    """Extract metadata from an article page's server-rendered HTML."""
    soup = BeautifulSoup(html, "lxml")
    meta: dict = {}

    def og(prop: str) -> str:
        tag = soup.select_one(f'meta[property="og:{prop}"]')
        return tag.get("content", "").strip() if tag else ""

    def meta_name(name: str) -> str:
        tag = soup.select_one(f'meta[name="{name}"]')
        return tag.get("content", "").strip() if tag else ""

    # Title: prefer the in-page h1, fall back to og:title / <title>
    h1 = soup.select_one("#conTit h1") or soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else (og("title") or soup.title.get_text(strip=True) if soup.title else "")
    title = re.sub(r"\s+", " ", title).strip()
    meta["title"] = title
    meta["clean_title"] = _clean_title(title)

    # Structured metadata block (hidden, for Baidu crawlers) is the cleanest source
    pubtime = _text_of(soup, "#pubtime_baidu")
    source = _text_of(soup, "#source_baidu")
    author_raw = _text_of(soup, "#author_baidu")

    # publish time fallbacks
    if not pubtime:
        pubtime = meta_name("publishdate") or meta_name("article:published_time") or og("article:published_time")
    meta["publish_time"] = pubtime

    # source
    source = re.sub(r"^来源[:：]\s*", "", source).strip()
    if not source:
        m = re.search(r"来源[:：]\s*([^\s<]+)", html)
        if m:
            source = m.group(1)
    meta["source"] = source or "财新网"

    # authors: "作者：徐路易,冯禹丁,路尘,王嘉鹏,曾佳"  (comma or Chinese comma)
    author_raw = re.sub(r"^作者[:：]\s*", "", author_raw).strip()
    if author_raw:
        meta["authors"] = [a.strip() for a in re.split(r"[,，、]", author_raw) if a.strip()]
    else:
        # fallback: "文｜财新周刊 张三 李四" style byline in the body
        m = re.search(r"文[｜|][^<\n]{0,80}", html)
        meta["authors"] = [m.group(0).strip()] if m else []

    meta["lead"] = og("description") or meta_name("description")
    meta["cover_image"] = og("image")

    # Issue reference: 来源于《财新周刊》2026年07月13日第27期
    meta["issue"] = _parse_issue_ref(html)

    # srcinfoid (== article URL id on caixin) and entity json
    parsed = parse_article_url(url) or {}
    aid = parsed.get("aid")
    m = re.search(r"srcinfoid\s*=\s*['\"]?(\d+)", html)
    if m:
        aid = int(m.group(1))
    meta["article_id"] = aid or 0
    meta["channel"] = parsed.get("channel", "")

    # Pagination: count distinct ?pN links
    pages = set()
    for a in soup.select("a[href]"):
        mm = re.search(r"\?p(\d+)\b", a.get("href", ""))
        if mm:
            pages.add(int(mm.group(1)))
    meta["pagination_pages"] = max(pages) if pages else 1

    return meta


def _text_of(soup, selector: str) -> str:
    el = soup.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def _clean_title(title: str) -> str:
    """Strip freshness/section prefixes like leading '最新' for a clean title."""
    t = title.strip()
    t = re.sub(r"^最新", "", t)
    return t.strip()


def _parse_issue_ref(html: str) -> str:
    """Find '来源于《财新周刊》YYYY年MM月DD日第N期' and normalize."""
    m = re.search(
        r"来源于[^《]*《财新周刊》[^第]*?(\d{4})年(\d{1,2})月(\d{1,2})日第(\d+)期", html
    )
    if m:
        year, _mo, _da, issue_no = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"财新周刊 {year}年第{int(issue_no)}期"
    # alt: 2026年第27期 without full date
    m = re.search(r"《财新周刊》\s*(\d{4})年(?:\d{1,2}月\d{1,2}日)?第(\d+)期", html)
    if m:
        return f"财新周刊 {m.group(1)}年第{int(m.group(2))}期"
    return ""


def fetch_article(client: CaixinClient, url: str, section: str = "", renderer=None) -> Article:
    """Fetch and parse a single article (all pages) into an Article.

    If ``renderer`` (a BrowserRenderer) is supplied and a cookie is set, the
    full body is rendered via a headless browser; otherwise the server-rendered
    teaser is used (free articles / no cookie).
    """
    url = normalize_article_url(url)
    parsed = parse_article_url(url)
    if not parsed:
        raise CaixinError(f"not a Caixin article URL: {url}")

    html = client.get_html(url)
    meta = parse_meta(html, url)

    aid = meta["article_id"]
    nav_pages = meta["pagination_pages"]
    body_html, total_pages, paywalled, body_source = _fetch_body(
        client, aid, url, nav_pages, html, renderer
    )

    # Strip noise + compute plain text
    body_html = _strip_noise(body_html)
    body_text = BeautifulSoup(body_html, "lxml").get_text("\n", strip=True)

    return Article(
        url=url,
        article_id=aid,
        channel=meta["channel"],
        title=meta["title"],
        clean_title=meta["clean_title"],
        authors=meta["authors"],
        publish_time=meta["publish_time"],
        source=meta["source"],
        lead=meta["lead"],
        cover_image=meta["cover_image"],
        issue=meta["issue"],
        section=section,
        total_pages=total_pages,
        body_html=body_html,
        body_text=body_text,
        paywalled=paywalled,
        fetched_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )


def _fetch_body(client: CaixinClient, aid: int, url: str, nav_pages: int, base_html: str, renderer=None):
    """Return (body_html, total_pages, paywalled, source).

    With a BrowserRenderer + cookie, render the full body via headless browser.
    Otherwise fall back to the server-rendered #Main_Content_Val (full body for
    free articles, teaser for paid ones).
    """
    if renderer is not None and client.settings.has_cookie:
        try:
            body_html, total, paywalled = renderer.render_article(url, nav_pages)
        except Exception as e:
            raise CaixinError(f"browser rendering failed: {e}")
        return body_html, total, paywalled, "browser"

    # No cookie / no renderer: use server HTML.
    body = _fetch_server_pages(client, url, nav_pages, base_html)
    paywalled = len(re.sub(r"\s+", "", BeautifulSoup(body, "lxml").get_text())) < 500
    return body, nav_pages, paywalled, "server"


def _fetch_server_pages(client: CaixinClient, url: str, nav_pages: int, base_html: str) -> str:
    """Concatenate #Main_Content_Val across ?p1..?pN from server HTML (free articles)."""
    parts = [_server_main_content(base_html)]
    seen = {re.sub(r"\s+", "", parts[0])}
    for p in range(2, nav_pages + 1):
        page_url = f"{url}?p{p}"
        try:
            ph = client.get_html(page_url)
        except CaixinError:
            break
        seg = _server_main_content(ph)
        key = re.sub(r"\s+", "", seg)
        if key and key not in seen:  # skip identical teasers on paid multi-page
            seen.add(key)
            parts.append(seg)
    return "".join(parts)


def _server_main_content(html: str) -> str:
    """Return inner HTML of #Main_Content_Val, or '' if absent."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("#Main_Content_Val")
    if not node:
        return ""
    # drop the leading &nbsp; placeholder and stray byline duplication is fine to keep
    return node.decode_contents()


def _ensure_str(v) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _strip_noise(html_str: str) -> str:
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "lxml")
    for sel in _NOISE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    return soup.decode_contents()
