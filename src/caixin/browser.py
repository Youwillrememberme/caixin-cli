"""Headless-browser body renderer.

Caixin's article body is gated behind an authenticated, **signed** request
(`x-nonce`/`x-sign`) computed by heavily-obfuscated JS (`cx-pay-layer/pc.js`),
and the gateway also does TLS-fingerprint bot-detection (httpx gets 401).

Rather than reverse-engineer the signing, we drive a real browser (your
installed Edge/Chrome via Playwright) which runs the JS natively: it computes
the sign, fetches the body, and renders it into `#Main_Content_Val`. We then
extract that element (stripping AI-annotation noise) across all pages.

Requires: `pip install playwright` + a Chromium-based browser (Edge or Chrome).
If neither `msedge` nor `chrome` channel is found, falls back to the bundled
Chromium (`playwright install chromium`).
"""
from __future__ import annotations

import re
import time
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover
    _HAS_PLAYWRIGHT = False
    PWTimeout = TimeoutError  # type: ignore

# Elements injected by the AI feature (ai_pc.js) and paywall UI, not article body.
_NOISE = (
    ".aitt, .bd_block, .chargeWall, #chargeWall, #content_msg, .pip_none, "
    "#pageBtn, #loadinWall, script, style, iframe, noscript, .qr_code, .qrcode, "
    ".wx-share, .share, .relate-reading, .xgydBox, "
    "#cxLogoHead, img[src*='editorIcon'], img[src*='favicon']"
)


class BrowserRenderer:
    """Renders a Caixin article page with a login cookie and extracts the body."""

    def __init__(self, cookie: str, user_agent: str, channels=("msedge", "chrome")):
        self.cookie = cookie
        self.user_agent = user_agent
        self.channels = channels
        self._pw = None
        self._browser = None
        self._ctx = None
        self.channel: Optional[str] = None

    @staticmethod
    def available() -> bool:
        return _HAS_PLAYWRIGHT

    def _ensure(self) -> None:
        if self._ctx is not None:
            return
        self._pw = sync_playwright().start()
        last_err: Optional[Exception] = None
        for ch in self.channels:
            try:
                self._browser = self._pw.chromium.launch(channel=ch, headless=True)
                self.channel = ch
                break
            except Exception as e:
                last_err = e
        if self._browser is None:
            try:
                self._browser = self._pw.chromium.launch(headless=True)
                self.channel = "chromium"
            except Exception as e:
                raise RuntimeError(
                    "No usable browser for Playwright. Install Microsoft Edge or "
                    "Google Chrome, or run `playwright install chromium`. "
                    f"Last error: {last_err or e}"
                )
        self._ctx = self._browser.new_context(user_agent=self.user_agent)
        # inject the cookie jar across *.caixin.com
        cookies = []
        for part in self.cookie.split(";"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                cookies.append({"name": k, "value": v, "domain": ".caixin.com", "path": "/"})
        if cookies:
            self._ctx.add_cookies(cookies)

    def _wait_settled(self, page, timeout: float) -> None:
        """Wait for the signed body fetch to finish: #Main_Content_Val grows
        past the ~318-char teaser into the full text.

        chargeWall flashes during load *even for accessible articles* (the
        auth JS hides it only after authorizing), so it can't be the settle
        signal -- returning on it races the body injection and yields the
        teaser. Only the body actually growing indicates done. Times out for
        paywalled content (body stays short) or genuinely short free articles.
        """
        try:
            page.wait_for_function(
                "() => {"
                "  const mc = document.querySelector('#Main_Content_Val');"
                "  return mc && mc.innerText.replace(/\\s+/g,'').length > 800;"
                "}", timeout=timeout)
        except PWTimeout:
            pass

    def _is_paywalled(self, page) -> bool:
        """Accessible once the body grew past the teaser; paywalled only when
        the body stayed short AND chargeWall is still showing."""
        return page.evaluate("""() => {
            const cw = document.querySelector('#chargeWall');
            const mc = document.querySelector('#Main_Content_Val');
            const bodyLen = mc ? mc.innerText.replace(/\\s+/g,'').length : 0;
            const cwShown = cw && (cw.offsetHeight > 5
                || (cw.innerText||'').replace(/\\s+/g,'').length > 0);
            return bodyLen < 800 && !!cwShown;
        }""")

    def _extract(self, page) -> str:
        page.eval_on_selector_all(_NOISE, "els => els.forEach(e => e.remove())")
        return page.eval_on_selector("#Main_Content_Val", "e => e ? e.innerHTML : ''")

    def render_article(self, url: str, nav_pages: int = 1) -> tuple[str, int, bool]:
        """Render all pages of an article; return (body_html, total_pages, paywalled)."""
        self._ensure()
        page = self._ctx.new_page()
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            self._wait_settled(page, timeout=10000)
            time.sleep(0.6)
            paywalled = self._is_paywalled(page)
            # discover pagination (?p1..?pN)
            links = page.eval_on_selector_all("a[href*='?p']", "els => els.map(e => e.href)")
            pnums = sorted({int(m.group(1)) for h in links if (m := re.search(r"\?p(\d+)", h))})
            total = max(pnums) if pnums else (nav_pages or 1)
            parts = [self._extract(page)]
            for n in pnums:
                if n <= 1:  # page 1 == base url already extracted
                    continue
                page.goto(f"{url}?p{n}", timeout=60000, wait_until="domcontentloaded")
                self._wait_settled(page, timeout=8000)
                time.sleep(0.3)
                parts.append(self._extract(page))
            return "".join(parts), total, paywalled
        finally:
            page.close()

    def close(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._ctx = self._browser = self._pw = None
