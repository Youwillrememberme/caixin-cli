"""HTTP client for Caixin page fetching (metadata, issue index, search).

Note: the authenticated article BODY is no longer fetched via the gateway API
here -- that endpoint requires a signed request (`x-nonce`/`x-sign`) computed
by obfuscated JS and also does TLS-fingerprint bot-detection (httpx gets 401).
Full bodies are instead rendered via a headless browser; see ``browser.py``.
This client handles the plain, server-rendered pages (article metadata, weekly
index, search) which work fine over plain HTTP.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import httpx

from .config import Settings, cookie_jar_from_header


class CaixinError(Exception):
    """Base error for client failures."""


class CaixinClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        cookies = cookie_jar_from_header(settings.cookie)
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.caixin.com/",
        }
        if settings.cookie:
            headers["Cookie"] = settings.cookie
        self._client = httpx.Client(
            cookies=cookies,
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._last_request = 0.0

    # -- internals -----------------------------------------------------------

    def _throttle(self) -> None:
        gap = self.settings.delay
        if gap <= 0:
            return
        wait = gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, url: str, *, params: dict | None = None, extra_headers: dict | None = None,
             retries: int = 3) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            self._throttle()
            try:
                r = self._client.get(url, params=params, headers=extra_headers)
                if r.status_code in (429, 502, 503, 504):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return r
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                time.sleep(2.0 * (attempt + 1))
        raise CaixinError(f"request failed after {retries} retries: {last_exc}")

    def close(self) -> None:
        self._client.close()

    # -- public --------------------------------------------------------------

    def get_html(self, url: str) -> str:
        """Fetch a caixin.com page and return decoded UTF-8 text."""
        r = self._get(url, extra_headers={"Referer": url})
        if r.status_code >= 400:
            raise CaixinError(f"HTTP {r.status_code} fetching {url}")
        return r.content.decode("utf-8", errors="replace")

    def get_bytes(self, url: str, referer: str = "https://www.caixin.com/") -> bytes:
        """Fetch raw bytes (for image downloads)."""
        r = self._get(url, extra_headers={"Referer": referer})
        if r.status_code >= 400:
            raise CaixinError(f"HTTP {r.status_code} fetching {url}")
        return r.content

    def get_jsonp(self, url: str, params: dict | None = None, callback: str = "cb") -> dict:
        """Fetch a JSONP endpoint and return the parsed JSON object.

        The response is expected to be ``<callback>({...})`` (with possible
        leading whitespace). Returns the parsed ``{...}`` dict.
        """
        r = self._get(url, params=params, extra_headers={
            "Referer": "https://www.caixin.com/", "Accept": "*/*"})
        if r.status_code >= 400:
            raise CaixinError(f"HTTP {r.status_code} fetching {url}")
        text = r.content.decode("utf-8", errors="replace").strip()
        m = re.search(re.escape(callback) + r"\(\s*(\{.*\})\s*\)\s*;?\s*$", text, re.S)
        if not m:  # callback-agnostic fallback
            m = re.search(r"\(\s*(\{.*\})\s*\)\s*;?\s*$", text, re.S)
        if not m:
            raise CaixinError("could not parse JSONP response")
        return json.loads(m.group(1))
