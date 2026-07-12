"""Weekly (财新周刊) parsing: issue list, issue index, issue resolution."""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ..client import CaixinClient, CaixinError
from ..models import IssueEntry, WeeklyArticle
from ..utils import parse_article_url, parse_issue_url

WEEKLY_HOME = "https://weekly.caixin.com/"
_ARTICLE_HREF = re.compile(r"https?://[\w-]+\.caixin\.com/\d{4}-\d{2}-\d{2}/\d+\.html")
_CW_HREF = re.compile(r"https?://weekly\.caixin\.com/(?P<year>\d{4})/cw(?P<total>\d+)/?")


def _add_days(iso_date: str, days: int) -> str:
    """Add days to a YYYY-MM-DD string; return '' on failure."""
    try:
        from datetime import datetime, timedelta
        d = datetime.strptime(iso_date, "%Y-%m-%d") + timedelta(days=days)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return ""


def parse_issue_list(html: str) -> list[IssueEntry]:
    """Parse the weekly homepage's 往期 list into IssueEntry rows."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[IssueEntry] = []
    seen: set[int] = set()
    for a in soup.find_all("a", href=_CW_HREF):
        m = _CW_HREF.search(a["href"])
        if not m:
            continue
        total = int(m.group("total"))
        year = int(m.group("year"))
        if total in seen:
            continue

        # Find the nearest repeating container: prefer an <li> ancestor, else the
        # nearest ancestor whose text carries issue metadata (出版/年度期号/总期号).
        container: Optional[Tag] = a.parent
        node: Optional[Tag] = a
        for _ in range(6):
            if node is None:
                break
            node = node.parent
            if node is None:
                break
            if node.name == "li":
                container = node
                break
            txt = node.get_text(" ", strip=True)
            if "出版" in txt or "年度期号" in txt or "总期号" in txt:
                container = node
                break

        ctext = container.get_text(" ", strip=True) if container else ""
        link_text = a.get_text(" ", strip=True)
        if not link_text:
            img = a.find("img")
            if img and img.get("alt"):
                link_text = img["alt"]
        cover_title = link_text or ""

        date_m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*出版", ctext)
        if date_m:
            publish_date = (
                f"{date_m.group(1)}-{int(date_m.group(2)):02d}-{int(date_m.group(3)):02d}"
            )
        else:
            iso = re.search(r"出版日期\s*[:：]?\s*(\d{4})-(\d{2})-(\d{2})", ctext)
            if iso:
                publish_date = f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
        yi_m = re.search(r"年度期号\s*[:：]\s*(\d+)", ctext)
        if yi_m:
            year_issue = int(yi_m.group(1))
        else:
            yi_m = re.search(r"\d{4}年第\s*(\d+)\s*期", ctext)
            if yi_m:
                year_issue = int(yi_m.group(1))

        entries.append(
            IssueEntry(
                total_no=total,
                year_issue=year_issue,
                year=year,
                publish_date=publish_date,
                cover_title=cover_title,
                url=f"https://weekly.caixin.com/{year}/cw{total}/",
            )
        )
        seen.add(total)
    # newest first
    entries.sort(key=lambda e: e.total_no, reverse=True)
    # The newest (current-issue "hero") on the homepage is often mis-parsed and
    # inherits the next issue's year_issue/date (it has no <li> wrapper). If the
    # two newest share a year_issue (impossible for sequential issues), the hero
    # is wrong: advance it by one issue (year_issue+1, publish_date+7 days).
    if len(entries) >= 2 and entries[0].year_issue and entries[0].year_issue == entries[1].year_issue:
        entries[0].year_issue = entries[1].year_issue + 1
        if entries[1].publish_date:
            entries[0].publish_date = _add_days(entries[1].publish_date, 7) or entries[0].publish_date
    return entries


def parse_issue_articles(html: str) -> list[WeeklyArticle]:
    """Parse an issue index page into its articles, grouped by section."""
    soup = BeautifulSoup(html, "lxml")
    # The cover-story section lives in .magazine-container; all other sections
    # (.magIntrotit headers + <dl> article lists) live alongside it under .main.
    container = soup.select_one(".main") or soup
    current_section = ""
    articles: list[WeeklyArticle] = []
    seen_urls: set[str] = set()

    def is_section_header(el: Tag) -> bool:
        cls = el.get("class") or []
        return any("tit" in c.lower() for c in cls) and bool(el.get_text(strip=True))

    for el in container.descendants:
        if not isinstance(el, Tag):
            continue
        if is_section_header(el):
            current_section = _section_name(el.get_text(" ", strip=True))
            continue
        if el.name == "a" and el.get("href"):
            href = el["href"].strip()
            if not _ARTICLE_HREF.match(href) or href in seen_urls:
                continue
            title = el.get_text(" ", strip=True)
            if not title:
                continue
            byline = _byline_after(el)
            articles.append(
                WeeklyArticle(section=current_section, title=title, url=href, byline=byline)
            )
            seen_urls.add(href)
    return articles


def parse_issue_meta(html: str) -> dict:
    """Extract total issue no / year-issue / publish date from an issue index page."""
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one(".magazine-container .title") or soup.select_one(".title")
    src_el = soup.select_one(".magazine-container .source") or soup.select_one(".source")
    t = title_el.get_text(" ", strip=True) if title_el else ""
    s = src_el.get_text(" ", strip=True) if src_el else ""
    total_m = re.search(r"总第\s*(\d+)\s*期", t)
    year_m = re.search(r"(\d{4})年第\s*(\d+)\s*期", s)
    date_m = re.search(r"出版日期[：:]\s*(\d{4}-\d{2}-\d{2})", s)
    return {
        "total_no": int(total_m.group(1)) if total_m else 0,
        "year": int(year_m.group(1)) if year_m else 0,
        "year_issue": int(year_m.group(2)) if year_m else 0,
        "publish_date": date_m.group(1) if date_m else "",
    }


def _section_name(text: str) -> str:
    """'封面报道Cover Story' -> '封面报道' (leading CJK run, including ／)."""
    m = re.match(r"([一-龥／]+)", text)
    return m.group(1) if m else text.strip()


def _byline_after(a: Tag) -> str:
    """Look at following siblings for a 文｜ / 摄影｜ style byline."""
    sib = a
    for _ in range(4):
        sib = sib.next_sibling
        if sib is None:
            break
        if isinstance(sib, Tag) and sib.name in ("dd", "p", "div", "span"):
            txt = sib.get_text(" ", strip=True)
            if re.match(r"^(文|摄影|撰稿|记者)[｜|·]", txt):
                return txt
            if txt and not re.match(r"^(文|摄影|撰稿|记者)[｜|·]", txt):
                # summary text - keep as a descriptor if no byline precedes
                return txt
    return ""


def resolve_latest(client: CaixinClient) -> IssueEntry:
    """Fetch the homepage and return the newest issue entry."""
    html = client.get_html(WEEKLY_HOME)
    entries = parse_issue_list(html)
    if not entries:
        raise CaixinError("could not find any issue on the weekly homepage")
    return entries[0]


def resolve_issue(client: CaixinClient, identifier: str) -> tuple[int, int]:
    """Resolve an issue identifier to (year, total_issue_no).

    Accepts: 'latest', 'cw1214', '1214' (total), '2026-27' (year-issue), or a
    full issue index URL.
    """
    ident = identifier.strip()

    # Full URL
    parsed = parse_issue_url(ident)
    if parsed:
        return parsed["year"], parsed["total"]

    if ident.lower() == "latest":
        e = resolve_latest(client)
        return e.year, e.total_no

    # cw1214
    m = re.fullmatch(r"cw\s*(\d+)", ident, re.I)
    if m:
        total = int(m.group(1))
        year = _lookup_year_by_total(client, total)
        return year, total

    # 1214  (bare total issue number)
    if re.fullmatch(r"\d{3,4}", ident):
        total = int(ident)
        year = _lookup_year_by_total(client, total)
        return year, total

    # 2026-27  (year-issue)
    m = re.fullmatch(r"(\d{4})[-\s]+(\d{1,2})", ident)
    if m:
        year, yi = int(m.group(1)), int(m.group(2))
        total = _lookup_total_by_year_issue(client, year, yi)
        return year, total

    raise CaixinError(
        f"unrecognized issue identifier: {identifier!r} "
        "(expected latest | cw1214 | 1214 | 2026-27 | URL)"
    )


def _lookup_year_by_total(client: CaixinClient, total: int) -> int:
    """Find the year for a total issue number via the homepage list, else guess."""
    try:
        html = client.get_html(WEEKLY_HOME)
        for e in parse_issue_list(html):
            if e.total_no == total:
                return e.year
    except CaixinError:
        pass
    # Homepage lists ~25 recent issues; older ones aren't there. Guess by trying
    # recent years until the issue index page responds with content.
    return _guess_year_by_probing(client, total)


def _lookup_total_by_year_issue(client: CaixinClient, year: int, yi: int) -> int:
    try:
        html = client.get_html(WEEKLY_HOME)
        for e in parse_issue_list(html):
            if e.year == year and e.year_issue == yi:
                return e.total_no
    except CaixinError:
        pass
    raise CaixinError(
        f"could not resolve year-issue {year}-{yi} from the homepage list "
        "(it may be older than the listed range; try the cw<total> form instead)."
    )


def _guess_year_by_probing(client: CaixinClient, total: int) -> int:
    import datetime as _dt
    this_year = _dt.date.today().year
    for year in range(this_year, this_year - 6, -1):
        url = f"https://weekly.caixin.com/{year}/cw{total}/"
        try:
            html = client.get_html(url)
            if html and "总第" in html:
                return year
        except CaixinError:
            continue
    # last resort: assume current year
    return this_year
