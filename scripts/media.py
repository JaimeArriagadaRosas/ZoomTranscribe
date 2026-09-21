from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from .utils import to_relative


def _probe_audio(path: Path, runner: Callable = subprocess.run) -> float:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Audio ausente o vacío: {path}")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffprobe rechazó el audio").strip()
        raise ValueError(detail)
    try:
        duration = float((completed.stdout or "").strip())
    except ValueError as exc:
        raise ValueError("ffprobe no devolvió una duración de audio válida") from exc
    if duration <= 0:
        raise ValueError("El audio tiene duración no positiva")
    return duration


def ensure_mp3(
    root: Path,
    source: Path,
    logger,
    runner: Callable = subprocess.run,
) -> str:
    """Create a permanent MP3 alongside the downloaded video, if needed."""
    root = root.resolve()
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    final = audio_dir / f"{source.stem}.mp3"

    try:
        _probe_audio(final, runner)
        return to_relative(root, final)
    except (ValueError, OSError):
        pass

    token = uuid.uuid4().hex
    temporary = audio_dir / f".{source.stem}.{token}.tmp.mp3"
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(temporary),
    ]
    logger.info("Generando MP3 permanente: %s", final.name)
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"No se pudo ejecutar ffmpeg para crear MP3: {exc}") from exc

    try:
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "ffmpeg no produjo MP3").strip()
            raise RuntimeError(f"ffmpeg no pudo crear el MP3: {detail}")
        _probe_audio(temporary, runner)
        os.replace(temporary, final)
        _probe_audio(final, runner)
    finally:
        temporary.unlink(missing_ok=True)

    return to_relative(root, final)


def audio_is_valid(root: Path, record: dict, runner: Callable = subprocess.run) -> bool:
    relative = (record.get("audio") or {}).get("file")
    if not relative:
        return False
    try:
        path = (root.resolve() / relative).resolve()
        path.relative_to(root.resolve())
        _probe_audio(path, runner)
        return True
    except (ValueError, OSError):
        return False
