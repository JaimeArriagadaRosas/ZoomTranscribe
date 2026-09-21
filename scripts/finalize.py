from __future__ import annotations

from pathlib import Path

from .metadata import RecordingStore
from .utils import atomic_write_text, recording_id, resolve_relative, safe_component, to_relative


def _duration_text(value) -> str:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return "desconocida"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def publish_final_transcript(root: Path, record: dict, position: int) -> str | None:
    transcripts = record.get("transcripts") or {}
    relative_txt = transcripts.get("txt")
    if not relative_txt:
        return None
    try:
        source = resolve_relative(root, relative_txt)
    except ValueError:
        return None
    if not source.is_file() or source.stat().st_size <= 0:
        return None

    title = str(record.get("title") or f"Clase {position:02d}")
    safe_title = safe_component(title, f"clase-{position:02d}", max_length=90)
    output_dir = root / "final_transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{position:02d}_{safe_title}_{record['id']}.txt"

    body = source.read_text(encoding="utf-8-sig").strip()
    header = (
        f"CLASE {position:02d}\n"
        f"Título: {title}\n"
        f"Fecha: {record.get('date') or 'desconocida'}\n"
        f"Duración: {_duration_text(record.get('duration'))}\n"
        f"ID: {record['id']}\n"
        "\n"
    )
    atomic_write_text(target, header + body + "\n")
    return to_relative(root, target)


def sync_final_transcripts(root: Path, store: RecordingStore, urls: list[str]) -> list[str]:
    published: list[str] = []
    index_lines = [
        "ZOOMTRANSCRIBE — TRANSCRIPCIONES FINALES",
        "",
        "Esta carpeta está ordenada según urls.txt y está preparada para subirla a Drive.",
        "",
    ]

    for position, url in enumerate(urls, start=1):
        record = store.load(recording_id(url))
        relative = publish_final_transcript(root, record, position)
        if relative:
            published.append(relative)
            record["final_transcript"] = relative
            store.save(record)
            index_lines.append(
                f"{position:02d}. {record.get('title') or 'Sin título'} — {Path(relative).name}"
            )
        else:
            index_lines.append(
                f"{position:02d}. PENDIENTE — {record.get('title') or record['id']}"
            )

    output_dir = root / "final_transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_dir / "00_INDICE.txt", "\n".join(index_lines) + "\n")
    return published
