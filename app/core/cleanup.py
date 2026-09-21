from __future__ import annotations

import shutil
from pathlib import Path

from app.core.jobs import JobStore


def delete_downloaded_videos(root: Path) -> int:
    store = JobStore(root)
    removed = 0
    for job in store.list_jobs():
        relative = (job.metadata.get("files") or {}).get("video")
        if not relative:
            continue
        video = job.directory / relative
        if video.is_file():
            video.unlink()
            removed += 1
        job.metadata["files"]["video"] = None
        store.save(job)
    return removed


def delete_all_generated(root: Path) -> None:
    root = root.resolve()
    for folder in ("output", "temp", "logs"):
        path = root / folder
        if path.exists():
            shutil.rmtree(path)
    data = root / "data"
    if data.exists():
        shutil.rmtree(data)
    JobStore(root)
