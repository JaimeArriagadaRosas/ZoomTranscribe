from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(value: str, fallback: str = "media", max_length: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"_+", "_", value)
    value = value[:max_length].rstrip(" .")
    return value or fallback


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class Job:
    number: int
    provider: str
    url: str
    directory: Path
    metadata: dict


class JobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.data_dir = self.root / "data"
        self.output_dir = self.root / "output"
        self.index_path = self.data_dir / "jobs.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index({"next_number": 1, "jobs": []})

    def _read_index(self) -> dict:
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"No se pudo leer {self.index_path}: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("jobs"), list):
            raise RuntimeError("data/jobs.json tiene una estructura inválida")
        value.setdefault("next_number", 1)
        return value

    def _write_index(self, value: dict) -> None:
        _atomic_json(self.index_path, value)

    def find_by_url(self, url: str) -> dict | None:
        normalized = url.strip()
        for item in self._read_index()["jobs"]:
            if item.get("url") == normalized:
                return item
        return None

    def create(self, provider: str, url: str) -> Job:
        index = self._read_index()
        number = int(index.get("next_number") or 1)
        while any(int(item.get("number", -1)) == number for item in index["jobs"]):
            number += 1

        slug = f"{number:03d}_{provider}_pending"
        directory = self.output_dir / slug
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {
            "number": number,
            "provider": provider,
            "url": url.strip(),
            "title": None,
            "duration": None,
            "source_id": None,
            "status": "created",
            "created_at": _now(),
            "updated_at": _now(),
            "files": {
                "video": None,
                "audio": None,
                "txt": None,
                "srt": None,
                "vtt": None,
            },
            "transcription": {
                "model": None,
                "language": None,
                "device": None,
                "compute_type": None,
            },
            "error": None,
        }
        _atomic_json(directory / "metadata.json", metadata)
        index["jobs"].append({
            "number": number,
            "provider": provider,
            "url": url.strip(),
            "directory": directory.relative_to(self.root).as_posix(),
            "status": "created",
            "title": None,
        })
        index["next_number"] = number + 1
        self._write_index(index)
        return Job(number, provider, url.strip(), directory, metadata)

    def load(self, number: int) -> Job:
        index = self._read_index()
        item = next((row for row in index["jobs"] if int(row.get("number", -1)) == int(number)), None)
        if item is None:
            raise KeyError(f"No existe el trabajo #{number:03d}")
        directory = (self.root / str(item["directory"])).resolve()
        directory.relative_to(self.root)
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8-sig"))
        return Job(int(number), str(item["provider"]), str(item["url"]), directory, metadata)

    def save(self, job: Job) -> Job:
        job.metadata["updated_at"] = _now()
        _atomic_json(job.directory / "metadata.json", job.metadata)
        index = self._read_index()
        for item in index["jobs"]:
            if int(item.get("number", -1)) == job.number:
                item["directory"] = job.directory.relative_to(self.root).as_posix()
                item["status"] = job.metadata.get("status")
                item["title"] = job.metadata.get("title")
                break
        self._write_index(index)
        return job

    def finalize_directory_name(self, job: Job, title: str) -> Job:
        safe_title = _safe_name(title, "media")
        target = self.output_dir / f"{job.number:03d}_{job.provider}_{safe_title}"
        if job.directory.resolve() != target.resolve():
            if target.exists() and target.resolve() != job.directory.resolve():
                target = self.output_dir / f"{job.number:03d}_{job.provider}_{safe_title}_{uuid.uuid4().hex[:6]}"
            job.directory.rename(target)
            job.directory = target
        return job

    def list_jobs(self) -> list[Job]:
        index = self._read_index()
        return [self.load(int(item["number"])) for item in sorted(index["jobs"], key=lambda x: int(x["number"]))]

    def reset(self) -> None:
        self._write_index({"next_number": 1, "jobs": []})
