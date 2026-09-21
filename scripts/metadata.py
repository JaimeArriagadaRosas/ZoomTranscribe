from __future__ import annotations

import csv
import io
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .utils import atomic_write_json, atomic_write_text, recording_id, resolve_relative


VALID_STATES = {
    "pending",
    "downloading",
    "downloaded",
    "transcribing",
    "completed",
    "download_failed",
    "transcription_failed",
    "interrupted",
}
CANONICAL_RECORD = re.compile(r"^[0-9a-f]{20}\.json$")
INDEX_COLUMNS = [
    "url",
    "id",
    "title",
    "date",
    "duration",
    "download_file",
    "audio_file",
    "txt",
    "srt",
    "vtt",
    "final_transcript",
    "state",
    "error",
    "whisper_model",
    "whisper_language",
    "whisper_device",
    "whisper_compute_type",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecordingStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.records_dir = self.root / "metadata" / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)

    def _path(self, rid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{20}", rid):
            raise ValueError(f"ID de grabación inválido: {rid}")
        return self.records_dir / f"{rid}.json"

    @staticmethod
    def _new_record(url: str) -> dict:
        normalized = url.strip()
        rid = recording_id(normalized)
        now = _now()
        return {
            "schema_version": 1,
            "id": rid,
            "url": normalized,
            "title": None,
            "date": None,
            "duration": None,
            "state": "pending",
            "created_at": now,
            "updated_at": now,
            "attempts": {"download": 0, "transcription": 0},
            "download": {"file": None, "info": None, "validated": False},
            "audio": {"file": None, "validated": False},
            "final_transcript": None,
            "transcripts": {"txt": None, "srt": None, "vtt": None, "validated": False},
            "whisper": {
                "model": None,
                "language": None,
                "detected_language": None,
                "language_probability": None,
                "device": None,
                "compute_type": None,
                "fallback_reason": None,
                "segments": None,
            },
            "error": None,
            "history": [{"state": "pending", "at": now}],
        }

    def ensure(self, urls: list[str]) -> list[dict]:
        records: list[dict] = []
        changed = False
        for url in urls:
            normalized = url.strip()
            rid = recording_id(normalized)
            path = self._path(rid)
            if path.exists():
                record = self.load(rid)
                if record.get("url") != normalized:
                    raise ValueError(f"Colisión de ID para {rid}")
            else:
                record = self._new_record(normalized)
                self._write_record(record)
                changed = True
            (self.root / "artifacts" / rid).mkdir(parents=True, exist_ok=True)
            records.append(record)
        if changed or not (self.root / "metadata" / "index.json").exists():
            self.rebuild_indexes()
        return records

    def load(self, rid: str) -> dict:
        path = self._path(rid)
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Registro inválido {rid}: {exc}") from exc
        if not isinstance(value, dict) or value.get("id") != rid:
            raise ValueError(f"Registro incoherente: {path.name}")
        return value

    def list_records(self) -> list[dict]:
        paths = sorted(path for path in self.records_dir.glob("*.json") if CANONICAL_RECORD.fullmatch(path.name))
        return [self.load(path.stem) for path in paths]

    def _validate(self, record: dict) -> None:
        rid = record.get("id")
        url = record.get("url")
        if not isinstance(rid, str) or not isinstance(url, str) or recording_id(url) != rid:
            raise ValueError("El ID del registro no corresponde a su URL")
        if record.get("state") not in VALID_STATES:
            raise ValueError(f"Estado inválido: {record.get('state')}")
        candidates = [
            record.get("download", {}).get("file"),
            record.get("download", {}).get("info"),
            record.get("audio", {}).get("file"),
            record.get("final_transcript"),
            record.get("transcripts", {}).get("txt"),
            record.get("transcripts", {}).get("srt"),
            record.get("transcripts", {}).get("vtt"),
        ]
        for value in candidates:
            if value:
                path = Path(value)
                if path.is_absolute():
                    raise ValueError(f"La ruta persistida debe ser relativa: {value}")
                resolve_relative(self.root, value)

    def _write_record(self, record: dict) -> dict:
        stored = deepcopy(record)
        self._validate(stored)
        stored["updated_at"] = _now()
        stored["history"] = list(stored.get("history", []))[-100:]
        atomic_write_json(self._path(stored["id"]), stored)
        return stored

    def save(self, record: dict) -> dict:
        stored = self._write_record(record)
        self.rebuild_indexes()
        return stored

    def transition(self, rid: str, state: str, **changes) -> dict:
        if state not in VALID_STATES:
            raise ValueError(f"Estado inválido: {state}")
        record = self.load(rid)
        record.update(deepcopy(changes))
        record["state"] = state
        record.setdefault("history", []).append({"state": state, "at": _now()})
        return self.save(record)

    def recover_interrupted(self) -> None:
        changed = False
        for record in self.list_records():
            if record.get("state") in {"downloading", "transcribing"}:
                record["state"] = "interrupted"
                record.setdefault("history", []).append({"state": "interrupted", "at": _now()})
                self._write_record(record)
                changed = True
        if changed:
            self.rebuild_indexes()

    @staticmethod
    def _error_text(record: dict) -> str:
        error = record.get("error")
        if not error:
            return ""
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "")
        return str(error)

    @classmethod
    def _index_row(cls, record: dict) -> dict:
        download = record.get("download") or {}
        audio = record.get("audio") or {}
        transcripts = record.get("transcripts") or {}
        whisper = record.get("whisper") or {}
        return {
            "url": record.get("url") or "",
            "id": record.get("id") or "",
            "title": record.get("title") or "",
            "date": record.get("date") or "",
            "duration": record.get("duration") if record.get("duration") is not None else "",
            "download_file": download.get("file") or "",
            "audio_file": audio.get("file") or "",
            "txt": transcripts.get("txt") or "",
            "srt": transcripts.get("srt") or "",
            "vtt": transcripts.get("vtt") or "",
            "final_transcript": record.get("final_transcript") or "",
            "state": record.get("state") or "",
            "error": cls._error_text(record),
            "whisper_model": whisper.get("model") or "",
            "whisper_language": whisper.get("language") or "",
            "whisper_device": whisper.get("device") or "",
            "whisper_compute_type": whisper.get("compute_type") or "",
        }

    def rebuild_indexes(self) -> None:
        rows = [self._index_row(record) for record in self.list_records()]
        metadata_dir = self.root / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(metadata_dir / "index.json", rows)
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=INDEX_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_text(metadata_dir / "index.csv", buffer.getvalue(), encoding="utf-8-sig")
