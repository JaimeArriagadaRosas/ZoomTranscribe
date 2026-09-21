from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def recording_id(url: str) -> str:
    normalized = url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def safe_component(text: str, fallback: str, max_length: int = 120) -> str:
    value = _INVALID_FILENAME.sub("_", str(text)).strip(" .")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"_+", "_", value)
    if not value or not value.strip("_"):
        value = fallback
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        value = f"_{value}"
    value = value[:max_length].rstrip(" .")
    return value or fallback


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def to_relative(root: Path, path: Path) -> str:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not _inside(resolved_root, resolved_path):
        raise ValueError(f"La ruta queda fuera del proyecto: {path}")
    return resolved_path.relative_to(resolved_root).as_posix()


def resolve_relative(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"Se esperaba una ruta relativa: {value}")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if not _inside(resolved_root, resolved):
        raise ValueError(f"La ruta queda fuera del proyecto: {value}")
    return resolved


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding=encoding)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content)


def yt_dlp_command(*arguments: str) -> list[str]:
    return [sys.executable, "-m", "yt_dlp", *arguments]
