"""Scan-to-login: drive a real browser through Caixin's QR-code login flow.

The login API (``gateway.caixin.com/api/ucenter/scan/...``) binds each QR code
to the browser's session/fingerprint, so a plain ``httpx`` client can't poll
its own QR code -- ``checkQRCodeStatus`` returns "二维码不存在" for any code
it didn't generate through a real browser (TLS-fingerprint bot detection, same
family as the article-body gateway).

So instead of reverse-engineering the login, we drive a **headed** browser
(your Edge/Chrome via Playwright) through the official ``u.caixin.com/web/``
login page: the page's own JS generates the QR code, the user scans it with
the Caixin App, and once the page confirms login (its ``userinfo/get`` call
returns ``code:0``) we harvest the cookies straight from the browser context.

The result is a ``k=v; k=v`` Cookie header string identical to what you'd
manually copy from devtools -- plus the user-info dict.
"""
from __future__ import annotations

import time

from .config import DEFAULT_USER_AGENT

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover
    _HAS_PLAYWRIGHT = False
    sync_playwright = None  # type: ignore

LOGIN_URL = "https://u.caixin.com/web/"
# The page calls this (with cookies) right after a successful login.
_USERINFO_HINT = "api/ucenter/userinfo/get"
_COOKIE_DOMAIN = "caixin.com"


class LoginError(Exception):
    """Raised when QR-code login fails or times out."""


def _launch(pw, headless: bool):
    """Launch a Chromium-based browser, preferring installed Edge then Chrome."""
    last_err = None
    for ch in ("msedge", "chrome"):
        try:
            return pw.chromium.launch(channel=ch, headless=headless)
        except Exception as e:
            last_err = e
    try:
        return pw.chromium.launch(headless=headless)
    except Exception as e:
        raise LoginError(
            "No usable browser for Playwright. Install Microsoft Edge or "
            "Google Chrome, or run `python -m playwright install chromium`. "
            f"Last error: {last_err or e}"
        )


def _cookies_to_header(cookies: list[dict]) -> str:
    """Join all caixin.com cookies into a `k=v; k=v` header (dedup by name)."""
    pairs: list[str] = []
    seen: set[str] = set()
    for c in cookies:
        if _COOKIE_DOMAIN not in c.get("domain", ""):
            continue
        name = c.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={c.get('value', '')}")
    return "; ".join(pairs)


def login_with_qrcode(timeout: int = 180, headless: bool = False) -> tuple[str, dict]:
    """Open the Caixin login page, wait for the user to scan the QR code with
    the Caixin App, and return ``(cookie_header, user_info)``.

    Raises :class:`LoginError` on timeout or if Playwright/a browser is
    unavailable. ``headless=False`` (the default) shows the browser window so
    the user can see and scan the QR code.
    """
    if not _HAS_PLAYWRIGHT:
        raise LoginError("Playwright is not installed. Run `pip install playwright`.")

    pw = sync_playwright().start()
    try:
        browser = _launch(pw, headless)
        ctx = browser.new_context(user_agent=DEFAULT_USER_AGENT)
        page = ctx.new_page()

        state = {"done": False, "info": {}}

        def _on_response(resp):
            if state["done"] or _USERINFO_HINT not in resp.url:
                return
            try:
                if resp.status != 200:
                    return
                obj = resp.json()
            except Exception:
                return
            if isinstance(obj, dict) and obj.get("code") == 0:
                state["done"] = True
                state["info"] = obj.get("data") or {}

        page.on("response", _on_response)

        try:
            page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            raise LoginError(f"failed to open login page: {e}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not state["done"]:
            try:
                page.wait_for_timeout(500)
            except Exception:
                # Browser/page closed by user -- stop waiting.
                break

        if not state["done"]:
            raise LoginError(
                f"login not detected within {timeout}s "
                "(did you scan and confirm in the Caixin App?)."
            )

        cookie_header = _cookies_to_header(ctx.cookies())
        if not cookie_header:
            raise LoginError("login succeeded but no caixin.com cookies were set.")

        for obj in (ctx, browser):
            try:
                obj.close()
            except Exception:
                pass
        return cookie_header, state["info"]
    finally:
        try:
            pw.stop()
        except Exception:
            pass
