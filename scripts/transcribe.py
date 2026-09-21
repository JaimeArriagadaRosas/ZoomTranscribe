from __future__ import annotations

import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .metadata import RecordingStore
from .utils import resolve_relative, to_relative


def _setup_cuda_path() -> None:
    import sys, os
    from pathlib import Path
    venv_site = Path(sys.executable).parent.parent / "Lib" / "site-packages"
    nvidia_dir = venv_site / "nvidia"
    if nvidia_dir.exists():
        for component in ["cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"]:
            bin_dir = nvidia_dir / component / "bin"
            if bin_dir.exists() and str(bin_dir) not in os.environ.get("PATH", ""):
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]

_setup_cuda_path()



@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    ok: bool
    txt: str | None = None
    srt: str | None = None
    vtt: str | None = None
    effective_model: str | None = None
    language: str | None = None
    device: str | None = None
    compute_type: str | None = None
    used_flac: bool = False
    error_message: str | None = None


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_timestamp(seconds: float, separator: str) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"


def _clean_segments(segments: Iterable[Segment]) -> list[Segment]:
    return [Segment(float(segment.start), float(segment.end), segment.text.strip()) for segment in segments if segment.text.strip()]


def render_txt(segments: Iterable[Segment]) -> str:
    lines = [f"[{_clock(segment.start)} - {_clock(segment.end)}] {segment.text}" for segment in _clean_segments(segments)]
    return "\n\n".join(lines) + ("\n" if lines else "")


def render_srt(segments: Iterable[Segment]) -> str:
    blocks = []
    for index, segment in enumerate(_clean_segments(segments), start=1):
        blocks.append(
            f"{index}\n{format_timestamp(segment.start, ',')} --> "
            f"{format_timestamp(segment.end, ',')}\n{segment.text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(segments: Iterable[Segment]) -> str:
    blocks = []
    for segment in _clean_segments(segments):
        blocks.append(
            f"{format_timestamp(segment.start, '.')} --> "
            f"{format_timestamp(segment.end, '.')}\n{segment.text}"
        )
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


def validate_transcript_set(txt: Path, srt: Path, vtt: Path) -> None:
    for path in (txt, srt, vtt):
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"Transcripción ausente o vacía: {path}")
    txt_content = txt.read_text(encoding="utf-8-sig")
    srt_content = srt.read_text(encoding="utf-8-sig")
    vtt_content = vtt.read_text(encoding="utf-8-sig")
    if not re.search(r"(?m)^\[\d{2}:\d{2}:\d{2} - \d{2}:\d{2}:\d{2}\] \S", txt_content):
        raise ValueError("TXT no contiene segmentos con timestamps válidos")
    if not re.search(
        r"(?m)^1\r?\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\r?\n\S",
        srt_content,
    ):
        raise ValueError("SRT no contiene un primer segmento válido")
    if not vtt_content.startswith("WEBVTT") or not re.search(
        r"(?m)^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}\r?\n\S",
        vtt_content,
    ):
        raise ValueError("VTT no contiene segmentos válidos")


def build_flac_command(source: Path, target: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        str(target),
    ]


def _default_model_factory(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=device, compute_type=compute_type)



def _attempt_transcription(
    source: Path,
    model_name: str,
    language: str,
    device: str,
    compute_type: str,
    model_factory: Callable,
    batch_size: int = 4,
) -> tuple[list[Segment], object]:
    model = model_factory(model_name, device, compute_type)
    from faster_whisper import BatchedInferencePipeline
    
    batched_model = BatchedInferencePipeline(model=model)
    
    raw_segments, info = batched_model.transcribe(
        str(source),
        language=language,
        batch_size=max(1, int(batch_size)),
    )
    
    import sys
    total_duration = getattr(info, "duration", 0)
    total_str = _clock(total_duration) if total_duration else "??"

    segments = []
    for segment in raw_segments:
        text = str(segment.text).strip()
        if text:
            segments.append(Segment(float(segment.start), float(segment.end), text))
        
        current_str = _clock(segment.end)
        sys.stdout.write(f"\r  └─ Progreso: [{current_str} / {total_str}]")
        sys.stdout.flush()
        
    sys.stdout.write("\r" + " " * 50 + "\r")
    sys.stdout.flush()
    if not segments:
        raise ValueError("Whisper no produjo segmentos con texto")
    return segments, info



def _extract_flac(source: Path, target: Path, runner: Callable, logger) -> None:
    if target.is_file() and target.stat().st_size > 0:
        logger.info("Reutilizando FLAC temporal existente: %s", target.name)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    command = build_flac_command(source, target)
    logger.info("Extrayendo audio temporal FLAC mono a 16 kHz")
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"No se pudo ejecutar ffmpeg: {exc}") from exc
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg no produjo FLAC").strip()
        raise RuntimeError(f"ffmpeg no pudo extraer el FLAC: {detail}")


def _publish_transcripts(root: Path, stem: str, segments: list[Segment]) -> tuple[str, str, str]:
    finals = {
        "txt": root / "transcripts" / "txt" / f"{stem}.txt",
        "srt": root / "transcripts" / "srt" / f"{stem}.srt",
        "vtt": root / "transcripts" / "vtt" / f"{stem}.vtt",
    }
    contents = {"txt": render_txt(segments), "srt": render_srt(segments), "vtt": render_vtt(segments)}
    token = uuid.uuid4().hex
    temporary: dict[str, Path] = {}
    try:
        for kind, final in finals.items():
            final.parent.mkdir(parents=True, exist_ok=True)
            path = final.with_name(f".{final.name}.{token}.tmp")
            path.write_text(contents[kind], encoding="utf-8")
            temporary[kind] = path
        validate_transcript_set(temporary["txt"], temporary["srt"], temporary["vtt"])
        for kind in ("txt", "srt", "vtt"):
            os.replace(temporary[kind], finals[kind])
        validate_transcript_set(finals["txt"], finals["srt"], finals["vtt"])
    finally:
        for path in temporary.values():
            path.unlink(missing_ok=True)
    return tuple(to_relative(root, finals[kind]) for kind in ("txt", "srt", "vtt"))


def _existing_transcripts(root: Path, record: dict) -> TranscriptResult | None:
    transcripts = record.get("transcripts") or {}
    if not all(transcripts.get(kind) for kind in ("txt", "srt", "vtt")):
        return None
    try:
        paths = [resolve_relative(root, transcripts[kind]) for kind in ("txt", "srt", "vtt")]
        validate_transcript_set(*paths)
    except ValueError:
        return None
    whisper = record.get("whisper") or {}
    return TranscriptResult(
        ok=True,
        txt=transcripts["txt"],
        srt=transcripts["srt"],
        vtt=transcripts["vtt"],
        effective_model=whisper.get("model"),
        language=whisper.get("language"),
        device=whisper.get("device"),
        compute_type=whisper.get("compute_type"),
    )


def transcribe_recording(
    root: Path,
    store: RecordingStore,
    record: dict,
    config: dict,
    logger,
    model_factory: Callable = _default_model_factory,
    runner: Callable = subprocess.run,
) -> TranscriptResult:
    root = root.resolve()
    current = store.load(record["id"])
    existing = _existing_transcripts(root, current)
    if existing:
        return existing
    relative_source = (current.get("download") or {}).get("file")
    if not relative_source:
        message = "La grabación no tiene un archivo descargado"
        return TranscriptResult(ok=False, error_message=message)
    try:
        source = resolve_relative(root, relative_source)
    except ValueError as exc:
        return TranscriptResult(ok=False, error_message=str(exc))
    if not source.is_file() or source.stat().st_size <= 0:
        return TranscriptResult(ok=False, error_message="El archivo descargado no existe o está vacío")

    attempts = dict(current.get("attempts") or {})
    attempts["transcription"] = int(attempts.get("transcription", 0)) + 1
    attempts.setdefault("download", 0)
    store.transition(current["id"], "transcribing", attempts=attempts, error=None)

    model_name = str(config["whisper_model"])
    language = str(config["language"])
    flac_path = root / "temp" / current["id"] / f"{current['id']}.flac"
    used_flac = False
    fallback_reason = None

    device = "cuda" if config.get("prefer_gpu", True) else "cpu"
    compute_type = "int8_float16" if device == "cuda" else "int8"
    configured_batch = max(1, int(config.get("batch_size", 4)))
    
    try:
        segments = None
        info = None
        
        # 1. ALWAYS extract FLAC first for determinism
        _extract_flac(source, flac_path, runner, logger)
        used_flac = True
        
        # 2. Try GPU with progressively smaller batches; then one CPU fallback.
        if device == "cuda":
            gpu_batches = []
            for candidate in (configured_batch, 2, 1):
                if candidate not in gpu_batches and candidate <= configured_batch:
                    gpu_batches.append(candidate)
            gpu_error = None
            for candidate in gpu_batches:
                try:
                    logger.info(
                        "Inicializando Whisper %s con cuda/%s (batch=%s)",
                        model_name,
                        compute_type,
                        candidate,
                    )
                    segments, info = _attempt_transcription(
                        flac_path,
                        model_name,
                        language,
                        device,
                        compute_type,
                        model_factory,
                        batch_size=candidate,
                    )
                    break
                except Exception as exc:
                    gpu_error = exc
                    logger.warning("CUDA batch=%s falló: %s", candidate, exc)
            if segments is None:
                fallback_reason = str(gpu_error) if gpu_error else "CUDA no produjo resultado"
                logger.warning("CUDA no fue utilizable; intentando CPU/int8")
                device, compute_type = "cpu", "int8"
                segments, info = _attempt_transcription(
                    flac_path,
                    model_name,
                    language,
                    device,
                    compute_type,
                    model_factory,
                    batch_size=1,
                )
        else:
            logger.info("Inicializando Whisper %s con cpu/int8", model_name)
            segments, info = _attempt_transcription(
                flac_path,
                model_name,
                language,
                device,
                compute_type,
                model_factory,
                batch_size=1,
            )

        txt, srt, vtt = _publish_transcripts(root, source.stem, segments)
        validate_transcript_set(resolve_relative(root, txt), resolve_relative(root, srt), resolve_relative(root, vtt))
        detected_language = getattr(info, "language", None)
        probability = getattr(info, "language_probability", None)
        duration = getattr(info, "duration", None) or current.get("duration")
        whisper = {
            "model": model_name,
            "language": language,
            "detected_language": detected_language,
            "language_probability": float(probability) if probability is not None else None,
            "device": device,
            "compute_type": compute_type,
            "fallback_reason": fallback_reason,
            "segments": len(segments),
        }
        transcripts = {"txt": txt, "srt": srt, "vtt": vtt, "validated": True}
        store.transition(
            current["id"],
            "completed",
            duration=float(duration) if duration is not None else current.get("duration"),
            transcripts=transcripts,
            whisper=whisper,
            error=None,
        )
        if used_flac and config.get("delete_temporary_audio", True):
            flac_path.unlink(missing_ok=True)
        return TranscriptResult(
            ok=True,
            txt=txt,
            srt=srt,
            vtt=vtt,
            effective_model=model_name,
            language=language,
            device=device,
            compute_type=compute_type,
            used_flac=used_flac,
        )
    except Exception as exc:
        message = str(exc).replace(str(root), "<PROJECT>")[:4000]
        error = {
            "phase": "transcription",
            "type": "transcription_failed",
            "message": message,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            store.transition(current["id"], "transcription_failed", error=error)
        except Exception:
            logger.exception("No se pudo persistir el error de transcripción para %s", current["id"])
        logger.error("Transcripción fallida para %s: %s", current["id"], message)
        return TranscriptResult(ok=False, used_flac=used_flac, error_message=message)
