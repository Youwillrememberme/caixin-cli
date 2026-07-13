"""Configuration: cookie + output settings, loaded from flag/env/config file.

Precedence (highest first):
    1. CLI flags (--cookie, --cookie-file, --out, --delay)
    2. Environment variables (CAIXIN_COOKIE, CAIXIN_OUT, CAIXIN_DELAY)
    3. ~/.caixin/config.toml
    4. Built-in defaults
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w  # for writing config back
except ModuleNotFoundError:  # pragma: no cover
    tomli_w = None  # type: ignore

DEFAULT_CONFIG_DIR = pathlib.Path.home() / ".caixin"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.toml"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Settings:
    cookie: str = ""
    output_dir: pathlib.Path = pathlib.Path("caixin-downloads")
    delay: float = 1.5
    user_agent: str = DEFAULT_USER_AGENT
    config_path: Optional[pathlib.Path] = None

    @property
    def has_cookie(self) -> bool:
        return bool(self.cookie.strip())


def _read_config_file(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _parse_cookie_file(text: str) -> str:
    """Accept either a raw `Cookie:` header string or Netscape cookies.txt.

    Returns a single `k=v; k=v` cookie header string.
    """
    text = text.strip()
    if not text:
        return ""
    # Netscape cookies.txt: tab-separated lines starting with domain (ignore # comments)
    if "\t" in text and not text.lower().startswith("cookie:"):
        pairs = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 7:
                pairs.append(f"{fields[5]}={fields[6]}")
        return "; ".join(pairs)
    # Raw Cookie header (optionally prefixed with "Cookie:")
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):].strip()
    return text


def load_settings(
    cookie: Optional[str] = None,
    cookie_file: Optional[str] = None,
    output_dir: Optional[str] = None,
    delay: Optional[float] = None,
    config_path: Optional[pathlib.Path] = None,
) -> Settings:
    cfg_path = pathlib.Path(config_path) if config_path else DEFAULT_CONFIG_FILE
    cfg = _read_config_file(cfg_path)
    auth = cfg.get("auth", {}) if isinstance(cfg, dict) else {}
    out_cfg = cfg.get("output", {}) if isinstance(cfg, dict) else {}

    # 1. cookie
    cookie_val = cookie or os.environ.get("CAIXIN_COOKIE") or auth.get("cookie", "")
    if not cookie_val and cookie_file:
        cookie_val = _parse_cookie_file(pathlib.Path(cookie_file).read_text(encoding="utf-8"))
    elif not cookie_val and (auth_file := auth.get("cookie_file")):
        cookie_val = _parse_cookie_file(pathlib.Path(auth_file).read_text(encoding="utf-8"))

    # 2. output dir
    out = output_dir or os.environ.get("CAIXIN_OUT") or out_cfg.get("dir", "caixin-downloads")

    # 3. delay
    if delay is None:
        env_delay = os.environ.get("CAIXIN_DELAY")
        delay = float(env_delay) if env_delay else float(out_cfg.get("delay", 1.5))

    return Settings(
        cookie=cookie_val,
        output_dir=pathlib.Path(out),
        delay=delay,
        user_agent=auth.get("user_agent", DEFAULT_USER_AGENT),
        config_path=cfg_path,
    )


def cookie_jar_from_header(cookie_header: str) -> dict:
    """Parse a `k=v; k=v` cookie header into a dict for httpx."""
    jar: dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        jar[k.strip()] = v.strip()
    return jar


def save_cookie_to_config(path: pathlib.Path, cookie: str) -> pathlib.Path:
    """Persist the login cookie into the config file (creates it if absent).

    Reads any existing config, sets ``[auth].cookie``, and writes back
    atomically (``.tmp`` + rename), preserving all other fields. Requires
    ``tomli-w``.
    """
    if tomli_w is None:  # pragma: no cover
        raise RuntimeError(
            "tomli-w is not installed (needed to write config). "
            "Run `pip install tomli-w`."
        )
    path = pathlib.Path(path)
    cfg = _read_config_file(path) if path.is_file() else {}
    if not isinstance(cfg, dict):
        cfg = {}
    auth = cfg.setdefault("auth", {})
    auth["cookie"] = cookie
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(cfg, f)
    tmp.replace(path)
    return path
