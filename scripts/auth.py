from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .utils import to_relative, yt_dlp_command


@dataclass
class AuthSession:
    mode: str
    cookies_file: str | None = None
    source: str | None = None


@dataclass
class AuthFailure(RuntimeError):
    message: str
    attempts: list[str] = field(default_factory=list)
    running_browsers: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


def _sanitize(text: str, url: str) -> str:
    return (text or "").replace(url, "<ZOOM_URL>")[:4000]


def _cookie_path(root: Path, config: dict) -> Path:
    configured = str(config.get("cookies_file") or "private/zoom.cookies.txt")
    path = (root.resolve() / configured).resolve()
    path.relative_to(root.resolve())
    return path


def _valid_cookie_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return False
    return first in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}


def _probe_command(url: str, auth_args: list[str]) -> list[str]:
    return yt_dlp_command(
        "--simulate",
        "--skip-download",
        "--no-playlist",
        "--no-progress",
        "--print",
        "title",
        *auth_args,
        url,
    )


def _run_probe(
    url: str,
    auth_args: list[str],
    runner: Callable,
) -> tuple[bool, str]:
    command = _probe_command(url, auth_args)
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        return False, str(exc)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode == 0, _sanitize(output, url)


def _browser_candidates(config: dict) -> list[str]:
    preferred = str(config.get("browser") or "opera").strip().lower()
    configured = config.get("browser_priority")
    candidates: list[str] = []
    if isinstance(configured, list):
        for item in configured:
            value = str(item).strip().lower()
            if value and value not in candidates:
                candidates.append(value)
    for value in (preferred, "opera", "chrome", "edge", "firefox"):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def running_browser_processes(runner: Callable = subprocess.run) -> list[str]:
    names = {
        "chrome.exe": "Chrome",
        "opera.exe": "Opera",
        "msedge.exe": "Edge",
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
    except (FileNotFoundError, OSError):
        return []
    output = (completed.stdout or "").lower()
    return [label for exe, label in names.items() if exe in output]


def prepare_auth(
    root: Path,
    config: dict,
    url: str,
    logger,
    runner: Callable = subprocess.run,
) -> AuthSession:
    """Resolve one authentication strategy before a batch starts.

    Order:
    1) existing private Netscape cookie file
    2) anonymous access
    3) export browser cookies once to the private cookie file

    The exported file is then reused for every recording, so the browser database is
    not reopened for each URL.
    """
    root = root.resolve()
    cookie_file = _cookie_path(root, config)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[str] = []

    if _valid_cookie_file(cookie_file):
        ok, detail = _run_probe(url, ["--cookies", str(cookie_file)], runner)
        if ok:
            logger.info("Autenticación: reutilizando cookie file privado")
            return AuthSession(
                mode="cookies_file",
                cookies_file=to_relative(root, cookie_file),
                source="existing_cookie_file",
            )
        attempts.append(f"cookie file existente: {detail}")

    ok, detail = _run_probe(url, [], runner)
    if ok:
        logger.info("Autenticación: la grabación no requiere cookies")
        return AuthSession(mode="anonymous", source="anonymous")
    attempts.append(f"sin cookies: {detail}")

    locked = False
    for browser in _browser_candidates(config):
        temporary = cookie_file.with_name(f".{cookie_file.name}.{uuid.uuid4().hex}.tmp")
        temporary.unlink(missing_ok=True)
        args = ["--cookies-from-browser", browser, "--cookies", str(temporary)]
        ok, detail = _run_probe(url, args, runner)
        if ok and _valid_cookie_file(temporary):
            os.replace(temporary, cookie_file)
            logger.info("Autenticación preparada desde %s; cookie file privado listo", browser)
            return AuthSession(
                mode="cookies_file",
                cookies_file=to_relative(root, cookie_file),
                source=f"browser:{browser}",
            )
        temporary.unlink(missing_ok=True)
        lowered = detail.lower()
        if "could not copy chrome cookie database" in lowered or "permission denied" in lowered or "database is locked" in lowered:
            locked = True
        attempts.append(f"{browser}: {detail}")

    running = running_browser_processes(runner)
    if locked:
        if running:
            message = (
                "No se pudo preparar la autenticación porque Windows mantiene bloqueada la base de cookies. "
                f"Procesos de navegador detectados: {', '.join(running)}. "
                "Cierra esos navegadores por completo y vuelve a ejecutar la opción 3."
            )
        else:
            message = (
                "La base de cookies sigue bloqueada aunque no se detectan navegadores abiertos. "
                "Como alternativa estable, exporta SOLO una vez cookies de Zoom en formato Netscape a "
                f"{to_relative(root, cookie_file)} y vuelve a ejecutar. El programa reutilizará ese archivo "
                "para las 12 clases y no volverá a tocar la base de cookies del navegador."
            )
    else:
        message = (
            "No se pudo autenticar la primera grabación con acceso anónimo, cookie file ni sesiones de navegador. "
            "Revisa que una sesión local tenga acceso a la grabación."
        )
    raise AuthFailure(message=message, attempts=attempts, running_browsers=running)
