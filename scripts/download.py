from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .metadata import RecordingStore
from .utils import atomic_write_json, safe_component, to_relative, yt_dlp_command


@dataclass
class DownloadError:
    kind: str
    message: str


@dataclass
class DownloadResult:
    ok: bool
    relative_file: str | None = None
    info_file: str | None = None
    title: str | None = None
    date: str | None = None
    duration: float | None = None
    error_kind: str | None = None
    error_message: str | None = None


class DownloadProcessError(RuntimeError):
    def __init__(self, output: str):
        super().__init__(output)
        self.output = output


def build_yt_dlp_command(
    root: Path,
    record: dict,
    config: dict,
    use_archive: bool = True,
    browser_override: str | None = None,
) -> list[str]:
    rid = record["id"]
    temp_dir = root.resolve() / "temp" / rid
    temp_dir.mkdir(parents=True, exist_ok=True)
    output = temp_dir / f"%(title).120B [%(id)s]_{rid}.%(ext)s"
    height = int(config["max_video_height"])
    if config.get("keep_video", True):
        selector = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
    else:
        selector = "bestaudio/best"
    browser = browser_override or str(config.get("browser", "chrome"))
    command = yt_dlp_command(
        "--cookies-from-browser",
        browser,
        "--continue",
        "--no-overwrites",
        "--windows-filenames",
        "--trim-filenames",
        "180",
        "--no-playlist",
        "--format",
        selector,
    )
    if config.get("keep_video", True):
        command.extend(["--merge-output-format", "mp4"])
    command.extend(
        [
            "--write-info-json",
            "--print",
            "after_move:filepath",
            "--newline",
            "--no-progress",
            "--output",
            str(output),
        ]
    )
    if use_archive:
        command.extend(["--download-archive", str(root.resolve() / "metadata" / "download-archive.txt")])
    command.append(record["url"])
    return command


def _browser_candidates(config: dict) -> list[str]:
    preferred = str(config.get("browser", "chrome")).strip().lower() or "chrome"
    candidates: list[str] = []
    for browser in (preferred, "chrome", "opera", "edge", "firefox"):
        if browser not in candidates:
            candidates.append(browser)
    return candidates


def probe_media(path: Path, runner: Callable = subprocess.run) -> dict:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"El archivo multimedia está vacío o no existe: {path}")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        raise ValueError(f"No se pudo ejecutar ffprobe: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "error desconocido").strip()
        raise ValueError(f"ffprobe rechazó el archivo: {detail}")
    try:
        payload = json.loads(completed.stdout or "{}")
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("ffprobe no devolvió una duración válida") from exc
    if duration <= 0:
        raise ValueError("ffprobe devolvió una duración no positiva")
    return {"duration": duration}


def classify_download_error(output: str) -> DownloadError:
    lowered = output.lower()
    if "cookie" in lowered and any(
        marker in lowered
        for marker in ("could not copy", "cannot access", "being used", "database is locked", "permission denied")
    ):
        return DownloadError(
            "opera_cookies_locked",
            "No se pudo acceder a la base de cookies del navegador; ciérralo completamente y vuelve a ejecutar.",
        )
    if any(marker in lowered for marker in ("drm", "encrypted media", "protected content")):
        return DownloadError(
            "access_restricted",
            "La grabación presenta una restricción que yt-dlp no puede procesar mediante el acceso normal.",
        )
    if any(marker in lowered for marker in ("http error 401", "http error 403", "sign in", "authentication", "password")):
        return DownloadError(
            "authentication_failed",
            "Zoom rechazó la autenticación disponible en la sesión de Opera.",
        )
    return DownloadError("yt_dlp_failed", "yt-dlp no pudo descargar la grabación.")


def _sanitize(text: str, record: dict) -> str:
    return text.replace(record["url"], f"<URL:{record['id']}>")[:4000]


def _run_ytdlp(command: list[str], record: dict, logger, runner: Callable) -> subprocess.CompletedProcess:
    display = subprocess.list2cmdline(command[:-1] + [f"<URL:{record['id']}>"])
    logger.info("Comando: %s", display)
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        raise DownloadProcessError(str(exc)) from exc
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    if combined:
        logger.debug("yt-dlp: %s", _sanitize(combined, record))
    if completed.returncode != 0:
        raise DownloadProcessError(combined or f"yt-dlp terminó con código {completed.returncode}")
    return completed


def _media_from_output(root: Path, temp_dir: Path, stdout: str) -> Path | None:
    for line in reversed((stdout or "").splitlines()):
        candidate_text = line.strip().strip('"')
        if not candidate_text or candidate_text.startswith("["):
            continue
        candidate = Path(candidate_text)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file() and not candidate.name.endswith((".info.json", ".part")):
            return candidate.resolve()
    ignored = {".json", ".part", ".ytdl", ".srt", ".vtt", ".txt"}
    candidates = [
        path
        for path in temp_dir.glob("*")
        if path.is_file() and path.suffix.lower() not in ignored and not path.name.endswith(".info.json")
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime).resolve() if candidates else None


def _load_info(temp_dir: Path) -> tuple[dict, Path | None]:
    candidates = sorted(temp_dir.glob("*.info.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return {}, None
    path = candidates[0]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}, path
    return payload if isinstance(payload, dict) else {}, path


def _metadata_date(info: dict) -> str | None:
    for key in ("upload_date", "release_date"):
        value = info.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{8}", value):
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    for key in ("release_timestamp", "timestamp"):
        value = info.get(key)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    return None


def _unique_destination(downloads: Path, stem: str, suffix: str) -> Path:
    destination = downloads / f"{stem}{suffix}"
    number = 2
    while destination.exists():
        destination = downloads / f"{stem}_{number}{suffix}"
        number += 1
    return destination


def _existing_valid_download(root: Path, record: dict, runner: Callable) -> DownloadResult | None:
    relative = (record.get("download") or {}).get("file")
    if not relative:
        return None
    path = (root / relative).resolve()
    try:
        probe = probe_media(path, runner=runner)
    except ValueError:
        return None
    return DownloadResult(
        ok=True,
        relative_file=relative,
        info_file=(record.get("download") or {}).get("info"),
        title=record.get("title"),
        date=record.get("date"),
        duration=probe["duration"],
    )


def download_recording(
    root: Path,
    store: RecordingStore,
    record: dict,
    config: dict,
    logger,
    runner: Callable = subprocess.run,
    position: int = 0,
) -> DownloadResult:
    root = root.resolve()
    current = store.load(record["id"])
    existing = _existing_valid_download(root, current, runner)
    if existing:
        return existing
    attempts = dict(current.get("attempts") or {})
    attempts["download"] = int(attempts.get("download", 0)) + 1
    attempts.setdefault("transcription", 0)
    store.transition(current["id"], "downloading", attempts=attempts, error=None)
    temp_dir = root / "temp" / current["id"]
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = None
        media = None
        successful_browser = None
        last_error: DownloadProcessError | None = None

        for browser in _browser_candidates(config):
            try:
                logger.info("Intentando autenticación de Zoom con navegador: %s", browser)
                completed = _run_ytdlp(
                    build_yt_dlp_command(root, current, config, True, browser_override=browser),
                    current,
                    logger,
                    runner,
                )
                successful_browser = browser
                media = _media_from_output(root, temp_dir, completed.stdout or "")
                break
            except DownloadProcessError as exc:
                last_error = exc
                classified = classify_download_error(exc.output)
                logger.warning(
                    "Intento con %s falló (%s); probando siguiente navegador si corresponde",
                    browser,
                    classified.kind,
                )
                if classified.kind not in {"opera_cookies_locked", "authentication_failed", "yt_dlp_failed"}:
                    raise

        if completed is None:
            raise last_error or DownloadProcessError("No fue posible ejecutar yt-dlp con ningún navegador disponible")

        if media is None:
            logger.warning("El historial indicó una descarga previa, pero no existe el archivo local; reintentando sin archive")
            completed = _run_ytdlp(
                build_yt_dlp_command(
                    root,
                    current,
                    config,
                    False,
                    browser_override=successful_browser,
                ),
                current,
                logger,
                runner,
            )
            media = _media_from_output(root, temp_dir, completed.stdout or "")
        if media is None:
            raise DownloadProcessError("yt-dlp terminó sin producir un archivo multimedia local")
        initial_probe = probe_media(media, runner=runner)
        info, info_path = _load_info(temp_dir)
        title = str(info.get("title") or current.get("title") or "Clase")
        date = _metadata_date(info) or current.get("date")
        date_component = date or "fecha-desconocida"
        title_component = safe_component(title, "clase", max_length=80)
        suffix = media.suffix.lower() or ".bin"
        prefix = f"{position:02d}_" if position > 0 else ""
        stem = f"{prefix}{date_component}_CIBERSEGURIDAD_{title_component}_{current['id']}"
        downloads = root / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        destination = _unique_destination(downloads, stem, suffix)
        shutil.move(str(media), str(destination))
        final_probe = probe_media(destination, runner=runner)

        relative_info = None
        if info:
            source_destination = root / "metadata" / "records" / f"{current['id']}.source.json"
            atomic_write_json(source_destination, info)
            relative_info = to_relative(root, source_destination)
            if info_path is not None:
                info_path.unlink(missing_ok=True)
        relative_file = to_relative(root, destination)
        duration = final_probe.get("duration") or initial_probe.get("duration") or info.get("duration")
        download_metadata = {
            "file": relative_file,
            "info": relative_info,
            "validated": True,
        }
        store.transition(
            current["id"],
            "downloaded",
            title=title,
            date=date,
            duration=float(duration) if duration is not None else None,
            download=download_metadata,
            error=None,
        )
        return DownloadResult(
            ok=True,
            relative_file=relative_file,
            info_file=relative_info,
            title=title,
            date=date,
            duration=float(duration) if duration is not None else None,
        )
    except Exception as exc:
        raw = exc.output if isinstance(exc, DownloadProcessError) else str(exc)
        classified = classify_download_error(raw)
        detail = _sanitize(raw, current).strip()
        message = classified.message + (f" Detalle: {detail}" if detail else "")
        error = {
            "phase": "download",
            "type": classified.kind,
            "message": message,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            store.transition(current["id"], "download_failed", error=error)
        except Exception:
            logger.exception("No se pudo persistir el error de descarga para %s", current["id"])
        logger.error("Descarga fallida para %s: %s", current["id"], message)
        return DownloadResult(
            ok=False,
            error_kind=classified.kind,
            error_message=message,
        )
