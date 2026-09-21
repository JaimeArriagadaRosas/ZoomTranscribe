from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from app.providers.base import DownloadedMedia


_MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


def _command(*args: str) -> list[str]:
    return [sys.executable, "-m", "yt_dlp", *args]


def _existing_video(job_dir: Path) -> Path | None:
    for path in sorted(job_dir.glob("video.*")):
        if path.suffix.lower() in _MEDIA_EXTENSIONS and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _load_info(job_dir: Path) -> dict:
    path = job_dir / "source.info.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def download_video(
    job_dir: Path,
    url: str,
    auth_args: list[str],
    max_height: int,
    logger,
    runner: Callable = subprocess.run,
) -> DownloadedMedia:
    existing = _existing_video(job_dir)
    if existing:
        info = _load_info(job_dir)
        return DownloadedMedia(
            title=str(info.get("title") or existing.stem),
            video_path=existing,
            duration=float(info["duration"]) if info.get("duration") else None,
            source_id=str(info.get("id") or "") or None,
        )

    job_dir.mkdir(parents=True, exist_ok=True)
    template = job_dir / "source.%(ext)s"
    selector = f"bestvideo[height<={int(max_height)}]+bestaudio/best[height<={int(max_height)}]/best"
    command = _command(
        "--continue",
        "--no-playlist",
        "--windows-filenames",
        "--no-progress",
        "--write-info-json",
        "--merge-output-format",
        "mp4",
        "-f",
        selector,
        "-o",
        str(template),
        "--print",
        "after_move:filepath",
        *auth_args,
        url,
    )
    logger.info("yt-dlp: iniciando descarga")
    completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "yt-dlp falló").replace(url, "<URL>")
        raise RuntimeError(detail[-4000:])

    candidates: list[Path] = []
    for line in (completed.stdout or "").splitlines():
        candidate = Path(line.strip().strip('"'))
        if candidate.is_file() and candidate.suffix.lower() in _MEDIA_EXTENSIONS:
            candidates.append(candidate)
    if not candidates:
        candidates = [
            p for p in job_dir.glob("source.*")
            if p.is_file() and p.suffix.lower() in _MEDIA_EXTENSIONS
        ]
    if not candidates:
        raise RuntimeError("yt-dlp terminó sin producir un archivo de video")

    source = max(candidates, key=lambda p: p.stat().st_mtime)
    target = job_dir / f"video{source.suffix.lower()}"
    if source.resolve() != target.resolve():
        source.replace(target)

    original_info = next(iter(job_dir.glob("source.info.json")), None)
    info = {}
    if original_info and original_info.is_file():
        info = _load_info(job_dir)

    return DownloadedMedia(
        title=str(info.get("title") or "Sin título"),
        video_path=target,
        duration=float(info["duration"]) if info.get("duration") else None,
        source_id=str(info.get("id") or "") or None,
    )
