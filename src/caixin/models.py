"""Data models for articles, weekly issues, and search results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Article:
    """A parsed Caixin article."""

    url: str
    article_id: int
    channel: str
    title: str
    clean_title: str
    authors: list[str] = field(default_factory=list)
    publish_time: str = ""
    source: str = ""
    lead: str = ""
    cover_image: str = ""
    issue: str = ""
    section: str = ""
    total_pages: int = 1
    body_html: str = ""
    body_text: str = ""
    paywalled: bool = False
    fetched_at: str = ""


@dataclass
class IssueEntry:
    """One row in the weekly issue list (from the weekly homepage)."""

    total_no: int  # 总期号, e.g. 1214
    year_issue: int  # 年度期号, e.g. 27
    year: int  # e.g. 2026
    publish_date: str  # e.g. 2026-07-13
    cover_title: str
    url: str  # e.g. https://weekly.caixin.com/2026/cw1214/


@dataclass
class WeeklyArticle:
    """An article listed inside a weekly issue's index page."""

    section: str
    title: str
    url: str
    byline: str = ""  # author teaser, e.g. "文｜财新周刊 罗国平"


@dataclass
class SearchResult:
    """One hit from the search command."""

    title: str
    url: str
    snippet: str = ""


@dataclass
class ChannelArticle:
    """A recent article listed on a channel (板块) homepage."""

    date: str  # YYYY-MM-DD (from the URL)
    title: str
    url: str

