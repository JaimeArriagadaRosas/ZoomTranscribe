from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_mp3(video: Path, target: Path, runner=subprocess.run) -> Path:
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video),
        "-vn", "-map", "0:a:0",
        "-ac", "1",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(target),
    ]
    completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg no produjo MP3").strip()
        raise RuntimeError(f"No se pudo generar MP3: {detail}")
    return target


def ensure_flac(video: Path, target: Path, runner=subprocess.run) -> Path:
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "flac",
        str(target),
    ]
    completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg no produjo FLAC").strip()
        raise RuntimeError(f"No se pudo generar FLAC: {detail}")
    return target
