from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.core.audio import ensure_flac, ensure_mp3
from app.core.jobs import Job, JobStore
from app.core.transcribe import transcribe_audio
from app.providers.youtube import YouTubeProvider
from app.providers.zoom import ZoomProvider


PROVIDERS = {
    "zoom": ZoomProvider(),
    "youtube": YouTubeProvider(),
}


def load_config(root: Path) -> dict:
    path = root / "config.json"
    if not path.is_file():
        raise RuntimeError("No existe config.json")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def setup_logger(root: Path, number: int) -> logging.Logger:
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"mediatranscribe-{number}-{time.time_ns()}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.FileHandler(logs / f"job_{number:03d}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _relative(job: Job, path: Path) -> str:
    return path.resolve().relative_to(job.directory.resolve()).as_posix()


def process_url(root: Path, provider_name: str, url: str) -> Job:
    root = root.resolve()
    config = load_config(root)
    provider = PROVIDERS[provider_name]
    provider.validate_url(url)

    store = JobStore(root)
    existing = store.find_by_url(url)
    if existing:
        existing_job = store.load(int(existing["number"]))
        if existing_job.metadata.get("status") == "completed":
            print(f"Esta URL ya está completada como trabajo #{existing_job.number:03d}.")
            return existing_job
        print(f"Reanudando trabajo #{existing_job.number:03d}...")
        job = existing_job
    else:
        job = store.create(provider_name, url)

    logger = setup_logger(root, job.number)
    try:
        job.metadata["status"] = "downloading"
        job.metadata["error"] = None
        store.save(job)

        media = provider.download(root, job.directory, url, config, logger)
        job.metadata["title"] = media.title
        job.metadata["duration"] = media.duration
        job.metadata["source_id"] = media.source_id
        job.metadata["files"]["video"] = _relative(job, media.video_path)
        job.metadata["status"] = "downloaded"
        job = store.finalize_directory_name(job, media.title)
        store.save(job)

        video = job.directory / str(job.metadata["files"]["video"])
        if not video.exists():
            matches = list(job.directory.glob("video.*"))
            if not matches:
                raise RuntimeError("No se encontró el video descargado después de renombrar el trabajo")
            video = matches[0]
            job.metadata["files"]["video"] = _relative(job, video)

        print("[3/5] Generando MP3...")
        mp3 = ensure_mp3(video, job.directory / "audio.mp3")
        job.metadata["files"]["audio"] = _relative(job, mp3)
        job.metadata["status"] = "audio_ready"
        store.save(job)

        print("[4/5] Transcribiendo...")
        temp_dir = root / "temp" / f"{job.number:03d}"
        flac = ensure_flac(video, temp_dir / "audio.flac")
        result = transcribe_audio(flac, job.directory, config, logger)
        for kind in ("txt", "srt", "vtt"):
            job.metadata["files"][kind] = _relative(job, result[kind])
        job.metadata["transcription"] = {
            "model": result["model"],
            "language": result["language"],
            "device": result["device"],
            "compute_type": result["compute_type"],
        }
        job.metadata["status"] = "completed"
        job.metadata["error"] = None
        store.save(job)

        if bool(config.get("delete_temporary_audio", True)):
            flac.unlink(missing_ok=True)
            try:
                temp_dir.rmdir()
            except OSError:
                pass

        print("[5/5] Guardando resultados...")
        print(f"✓ Trabajo #{job.number:03d} completado")
        print(f"  {job.directory.relative_to(root)}")
        return job
    except KeyboardInterrupt:
        job.metadata["status"] = "interrupted"
        job.metadata["error"] = "Interrumpido por el usuario"
        store.save(job)
        raise
    except Exception as exc:
        job.metadata["status"] = "failed"
        job.metadata["error"] = str(exc)[:4000]
        store.save(job)
        logger.exception("Trabajo fallido")
        raise
    finally:
        for handler in list(logger.handlers):
            handler.flush()
            handler.close()
            logger.removeHandler(handler)
