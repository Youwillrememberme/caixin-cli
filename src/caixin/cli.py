"""Caixin CLI - fetch articles and 财新周刊 issues as Markdown."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from .auth import LoginError, login_with_qrcode, verify_cookie
from .browser import BrowserRenderer
from .client import CaixinClient, CaixinError
from .config import DEFAULT_CONFIG_FILE, Settings, load_settings, save_cookie_to_config
from .parsers.article import fetch_article
from .parsers.channel import list_channel_articles, list_channels, resolve_channel
from .parsers.search import search_caixin
from .parsers.weekly import (
    parse_issue_articles,
    parse_issue_list,
    resolve_issue,
    WEEKLY_HOME,
)
from .render import render_markdown
from .utils import normalize_article_url, safe_filename

# Force UTF-8 stdout so Chinese / markdown prints cleanly on Windows consoles.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

app = typer.Typer(
    name="caixin",
    help="Fetch Caixin (财新网) articles and 财新周刊 issues as Markdown.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _make_client(settings: Settings) -> CaixinClient:
    if not settings.has_cookie:
        console.print(
            "[yellow]提示：未检测到 Cookie。免费文章可正常获取，"
            "付费文章仅能拿到摘要。用 --cookie / CAIXIN_COOKIE 提供登录 Cookie。[/yellow]"
        )
    return CaixinClient(settings)


def _make_renderer(settings: Settings):
    """Create a headless-browser renderer if we have a cookie + Playwright.

    Returns None (and prints a hint) if Playwright isn't installed or no browser
    is available; callers then fall back to the teaser path.
    """
    if not settings.has_cookie:
        return None
    if not BrowserRenderer.available():
        console.print(
            "[yellow]未安装 Playwright，付费全文将退回摘要模式。"
            "安装：pip install playwright（并确保已装 Edge/Chrome）。[/yellow]"
        )
        return None
    try:
        r = BrowserRenderer(settings.cookie, settings.user_agent)
        r._ensure()  # launch now so a missing browser fails fast
        console.print(f"[dim]浏览器渲染就绪 (channel={r.channel})[/dim]")
        return r
    except Exception as e:
        console.print(f"[yellow]无法启动浏览器渲染（退回摘要模式）：{e}[/yellow]")
        return None


def _save_one(client: CaixinClient, renderer, url: str, out_dir: Path, section: str = "",
              download_imgs: bool = True, prefix: str = "") -> tuple[bool, str]:
    """Fetch + render + save one article. Returns (ok, message)."""
    try:
        article = fetch_article(client, url, section=section, renderer=renderer)
    except CaixinError as e:
        return False, f"获取失败：{e}"

    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(article, client=client, download_imgs=download_imgs, out_dir=out_dir)
    fname = safe_filename(f"{prefix}{article.clean_title or article.title}") + ".md"
    (out_dir / fname).write_text(md, encoding="utf-8")
    flag = " (仅摘要)" if article.paywalled else ""
    return True, f"{fname}{flag}"


@app.callback()
def main(
    ctx: typer.Context,
    cookie: Optional[str] = typer.Option(None, "--cookie", envvar="CAIXIN_COOKIE",
        help="Raw Cookie header string (k=v; k=v) for caixin.com auth."),
    cookie_file: Optional[str] = typer.Option(None, "--cookie-file",
        help="Path to a file with a Cookie header or Netscape cookies.txt."),
    out: Optional[str] = typer.Option(None, "--out", "-o", envvar="CAIXIN_OUT",
        help="Output directory (default: ./caixin-downloads)."),
    delay: Optional[float] = typer.Option(None, "--delay", envvar="CAIXIN_DELAY",
        help="Seconds to wait between requests (default: 1.5)."),
    debug: bool = typer.Option(False, "--debug", help="Verbose error output."),
):
    """Personal-use archival of Caixin content you have subscribed to."""
    ctx.obj = load_settings(cookie=cookie, cookie_file=cookie_file,
                            output_dir=out, delay=delay)


@app.command()
def article(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="Caixin article URL (desktop or mobile)."),
    no_images: bool = typer.Option(False, "--no-images", help="Skip downloading images."),
    stdout: bool = typer.Option(False, "--stdout", help="Print markdown to stdout instead of saving."),
):
    """Fetch a single article as Markdown."""
    url = _coerce_url(url)
    settings: Settings = ctx.obj
    client = _make_client(settings)
    renderer = _make_renderer(settings)
    try:
        article = fetch_article(client, url, renderer=renderer)
    except CaixinError as e:
        console.print(f"[red]错误：{e}[/red]")
        _cleanup(client, renderer); raise typer.Exit(1)

    if stdout:
        md = render_markdown(article, client=None, download_imgs=False)
        sys.stdout.write(md + "\n")
        _cleanup(client, renderer)
        return

    out_dir = settings.output_dir / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(article, client=client, download_imgs=not no_images, out_dir=out_dir)
    fname = safe_filename(f"{article.publish_time[:10] or 'article'}-{article.clean_title}") + ".md"
    (out_dir / fname).write_text(md, encoding="utf-8")
    flag = " [yellow](仅摘要，请检查 Cookie)[/yellow]" if article.paywalled else ""
    console.print(f"[green]✓[/green] 已保存：{out_dir / fname}{flag}")
    _cleanup(client, renderer)


def _run_weekly(ctx: typer.Context, issue: str, no_images: bool,
                list_only: bool, section: Optional[str], limit: Optional[int]) -> None:
    settings: Settings = ctx.obj
    client = _make_client(settings)
    try:
        year, total = resolve_issue(client, issue)
        issue_url = f"https://weekly.caixin.com/{year}/cw{total}/"
        console.print(f"[cyan]期号：[/cyan]《财新周刊》总第{total}期 ({year})  {issue_url}")
        html = client.get_html(issue_url)
        articles = parse_issue_articles(html)
    except CaixinError as e:
        console.print(f"[red]错误：{e}[/red]"); raise typer.Exit(1)
    finally:
        client.close()

    if not articles:
        console.print("[red]未在该期目录页找到文章。[/red]"); raise typer.Exit(1)
    if section:
        articles = [a for a in articles if a.section == section]

    _print_article_table(articles)
    if list_only:
        return

    out_dir = settings.output_dir / f"weekly-{year}-cw{total}"
    if limit:
        articles = articles[:limit]

    client = _make_client(settings)
    renderer = _make_renderer(settings)
    ok = fail = 0
    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      console=console, transient=True) as prog:
            tid = prog.add_task("", total=len(articles))
            for i, art in enumerate(articles, 1):
                prefix = f"{i:02d}-"
                if art.section:
                    prefix += safe_filename(art.section) + "-"
                prog.update(tid, description=f"[{i}/{len(articles)}] {art.title[:30]}")
                ok_, msg = _save_one(client, renderer, art.url, out_dir, section=art.section,
                                     download_imgs=not no_images, prefix=prefix)
                prog.advance(tid)
                if ok_:
                    ok += 1
                else:
                    fail += 1
                    console.print(f"[red]✗[/red] {art.title}：{msg}")
    finally:
        _cleanup(client, renderer)
    console.print(f"\n[green]完成[/green] 成功 {ok} / 失败 {fail}  ->  {out_dir}")


@app.command(name="weekly")
def weekly(
    ctx: typer.Context,
    issue: str = typer.Argument(..., help="Issue: latest | cw1214 | 1214 | 2026-27 | URL"),
    no_images: bool = typer.Option(False, "--no-images", help="Skip downloading images."),
    list_only: bool = typer.Option(False, "--list-only", help="Only list the issue's articles."),
    section: Optional[str] = typer.Option(None, "--section", help="Only fetch this section."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max articles to fetch."),
):
    """Fetch all articles in a 财新周刊 issue."""
    _run_weekly(ctx, issue, no_images, list_only, section, limit)


@app.command(name="latest")
def latest(
    ctx: typer.Context,
    no_images: bool = typer.Option(False, "--no-images"),
    list_only: bool = typer.Option(False, "--list-only", help="Only list, don't fetch."),
):
    """Resolve and fetch (or list) the newest 财新周刊 issue."""
    _run_weekly(ctx, "latest", no_images, list_only, None, None)


@app.command(name="weekly-list")
def weekly_list(
    ctx: typer.Context,
    year: Optional[int] = typer.Option(None, "--year", help="Filter by year."),
    limit: int = typer.Option(30, "--limit", "-n", help="Max issues to show (0 = all)."),
):
    """List available 财新周刊 issues."""
    settings: Settings = ctx.obj
    client = _make_client(settings)
    try:
        html = client.get_html(WEEKLY_HOME)
        entries = parse_issue_list(html)
    except CaixinError as e:
        console.print(f"[red]错误：{e}[/red]"); raise typer.Exit(1)
    finally:
        client.close()

    if year:
        entries = [e for e in entries if e.year == year]
    if limit and limit > 0:
        entries = entries[:limit]
    console.print(f"[dim]共 {len(entries)} 期（用 --year / --limit 调整）[/dim]")

    table = Table(title="《财新周刊》往期目录")
    table.add_column("总期号", style="cyan", justify="right")
    table.add_column("年度期号", style="magenta", justify="right")
    table.add_column("出版日期", style="green")
    table.add_column("封面标题")
    table.add_column("URL", overflow="fold")
    for e in entries:
        table.add_row(str(e.total_no), str(e.year_issue), e.publish_date,
                      e.cover_title or "-", e.url)
    console.print(table)


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results."),
    fetch: bool = typer.Option(False, "--fetch", help="Fetch each result as Markdown."),
    no_images: bool = typer.Option(False, "--no-images"),
):
    """Search Caixin articles (via Sogou site:caixin.com)."""
    settings: Settings = ctx.obj
    client = _make_client(settings)
    try:
        results = search_caixin(client, query, limit=limit)
    except CaixinError as e:
        console.print(f"[red]搜索失败：{e}[/red]"); raise typer.Exit(1)
    finally:
        client.close()

    if not results:
        console.print("[yellow]未找到结果。[/yellow]"); return

    table = Table(title=f"搜索 “{query}”  ({len(results)} 条)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("标题")
    table.add_column("URL", overflow="fold")
    for i, r in enumerate(results, 1):
        table.add_row(str(i), r.title, r.url)
    console.print(table)

    if fetch:
        out_dir = settings.output_dir / "search" / safe_filename(query)
        client = _make_client(settings)
        renderer = _make_renderer(settings)
        ok = fail = 0
        try:
            for i, r in enumerate(results, 1):
                ok_, msg = _save_one(client, renderer, r.url, out_dir, download_imgs=not no_images)
                mark = "[green]✓[/green]" if ok_ else "[red]✗[/red]"
                line = f"{mark} [{i}/{len(results)}] {r.title}"
                if not ok_:
                    line += f"  {msg}"
                console.print(line)
                ok += int(ok_); fail += int(not ok_)
        finally:
            _cleanup(client, renderer)
        console.print(f"\n[green]完成[/green] 成功 {ok} / 失败 {fail}  ->  {out_dir}")


@app.command()
def channel(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="频道名（economy/finance/...），或 list 列出全部"),
    fetch: bool = typer.Option(False, "--fetch", help="下载文章而非仅列出"),
    limit: int = typer.Option(20, "--limit", "-n", help="最多篇数"),
    no_images: bool = typer.Option(False, "--no-images"),
):
    """列出/抓取某频道（板块）的最新文章。"""
    settings: Settings = ctx.obj
    if not name or name.lower() == "list":
        _print_channels()
        return

    try:
        key, label, url = resolve_channel(name)
    except CaixinError as e:
        console.print(f"[red]错误：{e}[/red]"); raise typer.Exit(1)

    client = _make_client(settings)
    try:
        articles, paginated = list_channel_articles(client, url, limit=limit)
    except CaixinError as e:
        console.print(f"[red]错误：{e}[/red]"); _cleanup(client, None); raise typer.Exit(1)

    if not articles:
        console.print(f"[red]在 {label} 频道未找到文章。[/red]"); _cleanup(client, None); raise typer.Exit(1)

    note = "[dim]（AJAX 翻页，可加载更多历史）[/dim]" if paginated else "[dim]（仅首页文章，无翻页）[/dim]"
    console.print(f"[cyan]频道：[/cyan]{label}（{key}）  {url}  共 {len(articles)} 篇 {note}")
    _print_channel_table(articles)

    if not fetch:
        _cleanup(client, None)
        return

    out_dir = settings.output_dir / "channel" / key
    renderer = _make_renderer(settings)
    ok = fail = 0
    try:
        for i, art in enumerate(articles, 1):
            ok_, msg = _save_one(client, renderer, art.url, out_dir, download_imgs=not no_images)
            mark = "[green]✓[/green]" if ok_ else "[red]✗[/red]"
            line = f"{mark} [{i}/{len(articles)}] {art.title}"
            if not ok_:
                line += f"  {msg}"
            console.print(line)
            ok += int(ok_); fail += int(not ok_)
    finally:
        _cleanup(client, renderer)
    console.print(f"\n[green]完成[/green] 成功 {ok} / 失败 {fail}  ->  {out_dir}")


@app.command()
def login(
    ctx: typer.Context,
    timeout: int = typer.Option(180, "--timeout", help="扫码等待超时（秒）。"),
    print_cookie: bool = typer.Option(False, "--print", help="仅打印 Cookie 不写文件。"),
    method: Optional[str] = typer.Option(
        None, "--method", help="登录方式：scan（扫码）/ cookie（粘贴）；不指定则交互选择。",
    ),
):
    """登录财新并写入 Cookie：扫码（财新 App）或粘贴 Cookie。"""
    settings: Settings = ctx.obj
    m = (method or "").strip().lower()

    if m not in ("scan", "cookie"):
        console.print("[cyan]登录方式：[/cyan]")
        console.print("  1) 扫码（财新 App）")
        console.print("  2) 粘贴 Cookie")
        choice = Prompt.ask("请选择", choices=["1", "2"], default="1")
        m = "scan" if choice == "1" else "cookie"

    if m == "scan":
        console.print("[cyan]启动浏览器…[/cyan] 请在弹出的窗口用「财新 App」扫码并确认登录。")
        try:
            cookie, info = login_with_qrcode(timeout=timeout, headless=False)
        except LoginError as e:
            console.print(f"[red]登录失败：{e}[/red]")
            raise typer.Exit(1)
        _finalize_login(cookie, info, print_cookie)
        return

    # method == "cookie": paste a raw Cookie header string
    raw = Prompt.ask("请粘贴 Cookie（整行 k=v; k=v）")
    cookie = raw.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[len("cookie:"):].strip()
    if not cookie:
        console.print("[red]Cookie 为空。[/red]")
        raise typer.Exit(1)
    console.print("[dim]校验 Cookie…[/dim]")
    info = verify_cookie(cookie, settings.user_agent)
    if info is None:
        console.print(
            "[red]Cookie 无效或已失效（接口未返回登录态）。请重新获取整段 Cookie 后再试。[/red]"
        )
        raise typer.Exit(1)
    _finalize_login(cookie, info, print_cookie)


def _finalize_login(cookie: str, info: dict, print_cookie: bool) -> None:
    """Show user info, then save (or print) the freshly obtained cookie."""
    uid = info.get("uid") or info.get("userId") or ""
    nick = info.get("nickname") or info.get("nickName") or ""
    bits = []
    if uid:
        bits.append(f"uid={uid}")
    if nick:
        bits.append(f"昵称={nick}")
    info_line = "  ".join(bits) if bits else "(无用户信息)"
    console.print(f"[green]✓ 登录有效[/green] {info_line}")

    if print_cookie:
        console.print("[dim]Cookie:[/dim]")
        console.print(cookie)
        return
    try:
        save_cookie_to_config(DEFAULT_CONFIG_FILE, cookie)
    except Exception as e:
        console.print(f"[red]写入配置失败：{e}[/red]")
        console.print("[dim]Cookie（请手动保存）：[/dim]")
        console.print(cookie)
        raise typer.Exit(1)
    console.print(f"[green]✓ Cookie 已写入[/green] {DEFAULT_CONFIG_FILE}")
    console.print("[dim]验证：caixin weekly-list[/dim]")


# -- helpers ------------------------------------------------------------------

def _coerce_url(s: str) -> str:
    """Normalize a Caixin article URL (collapse mobile -> desktop, strip query)."""
    return normalize_article_url(s)


def _cleanup(client: CaixinClient, renderer) -> None:
    """Close the HTTP client and browser renderer (ignore errors)."""
    for obj in (renderer, client):
        try:
            if obj is not None:
                obj.close()
        except Exception:
            pass


def _print_article_table(articles) -> None:
    table = Table(title=f"目录文章  ({len(articles)} 篇)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("栏目", style="cyan")
    table.add_column("标题")
    for i, a in enumerate(articles, 1):
        table.add_row(str(i), a.section or "-", a.title)
    console.print(table)


def _print_channels() -> None:
    table = Table(title="可用频道（板块）")
    table.add_column("名称", style="cyan")
    table.add_column("栏目")
    table.add_column("URL", overflow="fold")
    for key, label, url in list_channels():
        table.add_row(key, label, url)
    console.print(table)
    console.print("[dim]用法：caixin channel <名称>  |  caixin channel <名称> --fetch[/dim]")


def _print_channel_table(articles) -> None:
    table = Table(title=f"频道文章  ({len(articles)} 篇)")
    table.add_column("日期", style="green")
    table.add_column("标题")
    table.add_column("URL", overflow="fold")
    for a in articles:
        table.add_row(a.date, a.title, a.url)
    console.print(table)


if __name__ == "__main__":
    app()
