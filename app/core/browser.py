from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class BrowserSession:
    mode: str
    source: str
    cookie_file: Path | None = None

    @property
    def yt_dlp_args(self) -> list[str]:
        if self.mode == "cookies_file" and self.cookie_file is not None:
            return ["--cookies", str(self.cookie_file)]
        return []


class AuthenticationError(RuntimeError):
    pass


def _yt_dlp(*args: str) -> list[str]:
    return [sys.executable, "-m", "yt_dlp", *args]


def _valid_cookie_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return False
    return first in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}


def _probe(url: str, auth_args: list[str], runner: Callable) -> tuple[bool, str]:
    command = _yt_dlp(
        "--simulate",
        "--skip-download",
        "--no-playlist",
        "--no-progress",
        "--print",
        "title",
        *auth_args,
        url,
    )
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, FileNotFoundError) as exc:
        return False, str(exc)
    detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode == 0, detail.replace(url, "<URL>")[:3000]


def browser_candidates(config: dict) -> list[str]:
    configured = config.get("browser_priority") or []
    candidates: list[str] = []
    for raw in [*configured, config.get("browser"), "chrome", "edge", "opera", "firefox"]:
        value = str(raw or "").strip().lower()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def running_browsers(runner: Callable = subprocess.run) -> list[str]:
    mapping = {
        "chrome.exe": "Chrome",
        "msedge.exe": "Edge",
        "opera.exe": "Opera",
        "firefox.exe": "Firefox",
        "brave.exe": "Brave",
    }
    try:
        completed = runner(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, FileNotFoundError):
        return []
    output = (completed.stdout or "").lower()
    return [label for process, label in mapping.items() if process in output]


def prepare_browser_session(
    root: Path,
    url: str,
    provider: str,
    config: dict,
    logger,
    runner: Callable = subprocess.run,
) -> BrowserSession:
    """Resolve access once and cache only a local Netscape cookie export.

    Public URLs never touch browser cookies. Authenticated URLs reuse
    private/<provider>.cookies.txt when possible. No credentials are stored.
    """
    private_dir = root / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = private_dir / f"{provider}.cookies.txt"

    if _valid_cookie_file(cookie_file):
        ok, _ = _probe(url, ["--cookies", str(cookie_file)], runner)
        if ok:
            return BrowserSession("cookies_file", "cached", cookie_file)

    ok, anonymous_detail = _probe(url, [], runner)
    if ok:
        return BrowserSession("anonymous", "public")

    locked = False
    failures: list[str] = []
    for browser in browser_candidates(config):
        temporary = private_dir / f".{provider}.{uuid.uuid4().hex}.cookies.tmp"
        temporary.unlink(missing_ok=True)
        args = ["--cookies-from-browser", browser, "--cookies", str(temporary)]
        ok, detail = _probe(url, args, runner)
        if ok and _valid_cookie_file(temporary):
            os.replace(temporary, cookie_file)
            logger.info("Sesión %s preparada desde %s", provider, browser)
            return BrowserSession("cookies_file", browser, cookie_file)

        temporary.unlink(missing_ok=True)
        lowered = detail.lower()
        if (
            "could not copy chrome cookie database" in lowered
            or "database is locked" in lowered
            or "permission denied" in lowered
        ):
            locked = True
        failures.append(f"{browser}: {detail[:400]}")

    active = running_browsers(runner)
    if locked:
        suffix = f" Procesos detectados: {', '.join(active)}." if active else ""
        raise AuthenticationError(
            "Windows bloqueó la base de cookies del navegador."
            + suffix
            + " Cierra completamente los navegadores con sesión válida y vuelve a intentarlo. "
              f"Si persiste, puedes exportar una vez cookies Netscape a {cookie_file}."
        )

    logger.debug("Acceso anónimo falló: %s", anonymous_detail[:500])
    for failure in failures:
        logger.debug("Acceso navegador falló: %s", failure)
    raise AuthenticationError(
        "No se pudo acceder al recurso con acceso público, cookie cache ni sesiones de navegador disponibles."
    )
