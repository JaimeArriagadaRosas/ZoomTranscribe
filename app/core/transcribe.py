from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def _clock(seconds: float, milliseconds: bool = False, comma: bool = False) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if milliseconds:
        sep = "," if comma else "."
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def render_txt(segments: list[Segment]) -> str:
    return "\n\n".join(
        f"[{_clock(s.start)} - {_clock(s.end)}] {s.text.strip()}"
        for s in segments if s.text.strip()
    ) + "\n"


def render_srt(segments: list[Segment]) -> str:
    blocks = []
    for index, s in enumerate((s for s in segments if s.text.strip()), start=1):
        blocks.append(
            f"{index}\n{_clock(s.start, True, True)} --> {_clock(s.end, True, True)}\n{s.text.strip()}"
        )
    return "\n\n".join(blocks) + "\n"


def render_vtt(segments: list[Segment]) -> str:
    blocks = [
        f"{_clock(s.start, True)} --> {_clock(s.end, True)}\n{s.text.strip()}"
        for s in segments if s.text.strip()
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def _atomic_text(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _valid_outputs(txt: Path, srt: Path, vtt: Path) -> bool:
    if not all(path.is_file() and path.stat().st_size > 0 for path in (txt, srt, vtt)):
        return False
    try:
        return (
            bool(re.search(r"(?m)^\[\d{2}:\d{2}:\d{2} - \d{2}:\d{2}:\d{2}\] ", txt.read_text(encoding="utf-8-sig")))
            and "-->" in srt.read_text(encoding="utf-8-sig")
            and vtt.read_text(encoding="utf-8-sig").startswith("WEBVTT")
        )
    except OSError:
        return False


def _setup_cuda_path() -> None:
    import sys
    site = Path(sys.executable).parent.parent / "Lib" / "site-packages" / "nvidia"
    if not site.exists():
        return
    for component in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
        bin_dir = site / component / "bin"
        if bin_dir.exists():
            try:
                os.add_dll_directory(str(bin_dir))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def transcribe_audio(audio: Path, job_dir: Path, config: dict, logger) -> dict:
    txt = job_dir / "transcript.txt"
    srt = job_dir / "transcript.srt"
    vtt = job_dir / "transcript.vtt"
    if _valid_outputs(txt, srt, vtt):
        return {
            "txt": txt, "srt": srt, "vtt": vtt,
            "model": str(config.get("whisper_model", "medium")),
            "language": str(config.get("language", "auto")),
            "device": "existing",
            "compute_type": "existing",
        }

    _setup_cuda_path()
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    model_name = str(config.get("whisper_model", "medium"))
    requested_language = str(config.get("language", "auto")).strip().lower()
    language = None if requested_language == "auto" else requested_language
    prefer_gpu = bool(config.get("prefer_gpu", True))
    batch = max(1, int(config.get("batch_size", 4)))

    attempts: list[tuple[str, str, int]] = []
    if prefer_gpu:
        seen_batches: set[int] = set()
        for candidate in (batch, 2, 1):
            if candidate >= 1 and candidate not in seen_batches:
                attempts.append(("cuda", "int8_float16", candidate))
                seen_batches.add(candidate)
    attempts.append(("cpu", "int8", 1))

    last_error: Exception | None = None
    skip_remaining_cuda = False
    for device, compute_type, candidate_batch in attempts:
        if device == "cuda" and skip_remaining_cuda:
            continue
        try:
            logger.info(
                "Whisper %s %s/%s batch=%s",
                model_name, device, compute_type, candidate_batch,
            )
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            pipeline = BatchedInferencePipeline(model=model)
            raw_segments, info = pipeline.transcribe(
                str(audio),
                language=language,
                batch_size=candidate_batch,
            )
            segments = [
                Segment(float(seg.start), float(seg.end), str(seg.text).strip())
                for seg in raw_segments
                if str(seg.text).strip()
            ]
            if not segments:
                raise RuntimeError("Whisper no produjo texto")

            _atomic_text(txt, render_txt(segments))
            _atomic_text(srt, render_srt(segments))
            _atomic_text(vtt, render_vtt(segments))
            if not _valid_outputs(txt, srt, vtt):
                raise RuntimeError("Las transcripciones finales no pasaron validación")

            return {
                "txt": txt,
                "srt": srt,
                "vtt": vtt,
                "model": model_name,
                "language": getattr(info, "language", None) or requested_language,
                "device": device,
                "compute_type": compute_type,
            }
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Whisper falló con %s/%s batch=%s: %s",
                device, compute_type, candidate_batch, exc,
            )
            if device == "cuda":
                lowered = str(exc).lower()
                memory_related = any(token in lowered for token in (
                    "out of memory", "cuda_error_out_of_memory",
                    "cublas_status_alloc_failed", "failed to allocate",
                ))
                if not memory_related:
                    skip_remaining_cuda = True
            continue

    raise RuntimeError(f"No fue posible transcribir el audio: {last_error}")
