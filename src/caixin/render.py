"""Render an Article to Markdown with YAML frontmatter + local images."""
from __future__ import annotations

import re
import urllib.parse as uparse
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from .client import CaixinClient, CaixinError
from .models import Article


def yaml_escape(s: str) -> str:
    """Quote a string for YAML, escaping backslashes and double quotes."""
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def frontmatter(article: Article) -> str:
    lines = ["---"]
    lines.append(f"title: {yaml_escape(article.title)}")
    if article.authors:
        lines.append("authors:")
        for a in article.authors:
            lines.append(f"  - {yaml_escape(a)}")
    else:
        lines.append("authors: []")
    lines.append(f"date: {yaml_escape(article.publish_time)}")
    lines.append(f"source: {yaml_escape(article.source)}")
    lines.append(f"url: {yaml_escape(article.url)}")
    lines.append(f"channel: {yaml_escape(article.channel)}")
    if article.issue:
        lines.append(f"issue: {yaml_escape(article.issue)}")
    if article.section:
        lines.append(f"section: {yaml_escape(article.section)}")
    lines.append(f"pages: {article.total_pages}")
    lines.append(f"article_id: {article.article_id}")
    lines.append(f"fetched: {yaml_escape(article.fetched_at)}")
    lines.append(f"paywalled: {'true' if article.paywalled else 'false'}")
    lines.append("---")
    return "\n".join(lines)


def download_images(
    body_html: str,
    images_dir: Path,
    client: Optional[CaixinClient],
    base_url: str = "",
) -> tuple[str, list[str]]:
    """Download all <img> in body_html into images_dir; rewrite src to relative.

    Returns (rewritten_html, list_of_failed_src).
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    soup = BeautifulSoup(body_html, "lxml")
    failed: list[str] = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        src = src.strip()
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = uparse.urljoin(base_url or "https://www.caixin.com/", src)
        if not src.startswith(("http://", "https://")):
            continue
        # local name from URL path
        path = uparse.urlparse(src).path
        name = Path(path).name or "image"
        name = re.sub(r"[^\w.\-]+", "_", name)[:120]
        if not Path(name).suffix:
            name += ".jpg"
        dest = images_dir / name
        if client is not None and not dest.exists():
            try:
                client._throttle()
                r = client._client.get(src, headers={"Referer": base_url or "https://www.caixin.com/"})
                if r.status_code == 200 and r.content:
                    dest.write_bytes(r.content)
                else:
                    failed.append(src)
                    continue
            except Exception:
                failed.append(src)
                continue
        if dest.exists():
            img["src"] = f"images/{name}"
            if img.get("data-src"):
                del img["data-src"]
        else:
            failed.append(src)
    return soup.decode_contents(), failed


def _local_name(src: str) -> str:
    path = uparse.urlparse(src).path
    name = Path(path).name or "image"
    name = re.sub(r"[^\w.\-]+", "_", name)[:120]
    if not Path(name).suffix:
        name += ".jpg"
    return name


def download_one_image(
    src: str, images_dir: Path, client: Optional[CaixinClient], base_url: str = ""
) -> Optional[str]:
    """Download a single image URL into images_dir.

    Returns the local reference ``images/<name>`` on success, or ``None`` on
    failure (caller then falls back to the remote URL). Used for the cover
    image so it lands in the offline archive alongside body images.
    """
    s = src.strip()
    if s.startswith("//"):
        s = "https:" + s
    elif s.startswith("/"):
        s = uparse.urljoin(base_url or "https://www.caixin.com/", s)
    if not s.startswith(("http://", "https://")):
        return None
    images_dir.mkdir(parents=True, exist_ok=True)
    name = _local_name(s)
    dest = images_dir / name
    if client is not None and not dest.exists():
        try:
            client._throttle()
            r = client._client.get(s, headers={"Referer": base_url or "https://www.caixin.com/"})
            if r.status_code == 200 and r.content:
                dest.write_bytes(r.content)
            else:
                return None
        except Exception:
            return None
    return f"images/{name}" if dest.exists() else None


def preprocess_body_html(body_html: str) -> str:
    """Normalize a Caixin article body for clean Markdown.

    Photo essays (显影/视线) wrap each photo as
    ``<div class="imageBoxG"><img class="articleImageB" ...><div class="imageText">caption</div></div>``.
    Without intervention the caption renders as a loose paragraph that reads
    like body text. Here we fold the caption into the ``<img>`` alt *and* wrap
    it in ``<em>`` so it renders as an italic line directly under the photo.

    Also promotes protocol-relative (``//host``) image URLs to ``https:`` so the
    image markdown is valid even when images aren't downloaded (``--stdout``).
    """
    if not body_html:
        return ""
    soup = BeautifulSoup(body_html, "lxml")
    for box in soup.select(".imageBoxG"):
        img = box.find("img")
        cap = box.select_one(".imageText")
        if img and cap:
            text = cap.get_text(" ", strip=True)
            if text:
                img["alt"] = text
                cap.clear()
                em = soup.new_tag("em")
                em.string = text
                cap.append(em)
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("//"):
            img["src"] = "https:" + src
    return soup.decode_contents()


def body_to_markdown(body_html: str) -> str:
    """Convert article body HTML to Markdown."""
    md_text = md(
        body_html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "iframe", "form", "button"],
        default_title=True,
    )
    # blank out whitespace-only lines, then tidy excessive blank lines
    md_text = re.sub(r"(?m)^[ \t]+$", "", md_text)
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip()
    return md_text


def render_markdown(
    article: Article,
    client: Optional[CaixinClient] = None,
    download_imgs: bool = True,
    out_dir: Optional[Path] = None,
) -> str:
    """Build the full Markdown document for an article.

    If download_imgs is True and out_dir is given, images are saved to
    ``out_dir/images`` and referenced as relative ``images/<name>`` paths.
    """
    body_html = article.body_html or ""
    body_html = preprocess_body_html(body_html)

    if download_imgs and body_html and out_dir is not None:
        images_dir = out_dir / "images"
        body_html, _failed = download_images(body_html, images_dir, client, article.url)

    body_md = body_to_markdown(body_html)

    parts = [frontmatter(article), ""]

    # Title
    parts.append(f"# {article.clean_title or article.title}")
    parts.append("")

    # byline / meta line
    meta_bits = []
    if article.publish_time:
        meta_bits.append(article.publish_time)
    if article.source:
        meta_bits.append(f"来源：{article.source}")
    if article.authors:
        meta_bits.append("作者：" + "、".join(article.authors))
    if meta_bits:
        parts.append("*" + " ｜ ".join(meta_bits) + "*")
        parts.append("")

    if article.issue:
        parts.append(f"*{article.issue}*")
        parts.append("")

    # lead / summary
    if article.lead:
        parts.append(f"> {article.lead}")
        parts.append("")

    # paywall warning
    if article.paywalled:
        parts.append(
            "> ⚠️ **仅获取到摘要/预览**：未能取得全文。原因可能是 Cookie 缺失/失效、"
            "或该文章未在订阅范围内。请检查 `CAIXIN_COOKIE` 后重试。"
        )
        parts.append("")

    # cover image: download locally (like body images) so the offline archive
    # has it; fall back to the remote URL. Shown for --stdout (remote) too;
    # only --no-images (download_imgs=False with an out_dir) skips it.
    cover = article.cover_image
    if cover and (download_imgs or out_dir is None):
        if download_imgs and out_dir is not None:
            local = download_one_image(cover, out_dir / "images", client, article.url)
            cover_ref = local or cover
        else:
            cover_ref = cover
        parts.append(f"![]({cover_ref})")
        parts.append("")

    # body
    if body_md:
        parts.append(body_md)
    else:
        parts.append("（无正文内容）")

    parts.append("")
    parts.append(f"---\n*原文链接：<{article.url}>*")
    return "\n".join(parts)
