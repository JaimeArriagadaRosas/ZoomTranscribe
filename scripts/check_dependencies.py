from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .utils import yt_dlp_command


REQUIRED_CONFIG = {
    "whisper_model",
    "language",
    "browser",
    "prefer_gpu",
    "keep_video",
    "delete_temporary_audio",
    "expected_url_count",
    "minimum_free_space_gb",
    "minimum_yt_dlp_version",
    "max_video_height",
}

REQUIRED_DIRECTORIES = (
    "downloads",
    "transcripts/txt",
    "transcripts/srt",
    "transcripts/vtt",
    "metadata/records",
    "artifacts",
    "logs",
    "temp",
)


@dataclass
class PrebootReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    url_count: int = 0
    cuda_candidate: bool = False
    versions: dict[str, str] = field(default_factory=dict)


def load_config(root: Path) -> dict:
    path = root / "config.json"
    if not path.is_file():
        raise ValueError("No existe config.json")
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"config.json no es válido: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config.json debe contener un objeto JSON")
    missing = sorted(REQUIRED_CONFIG - config.keys())
    if missing:
        raise ValueError(f"Faltan opciones en config.json: {', '.join(missing)}")
    for name in ("whisper_model", "language", "browser"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f"{name} debe ser texto no vacío")
    for name in ("prefer_gpu", "keep_video", "delete_temporary_audio"):
        if not isinstance(config[name], bool):
            raise ValueError(f"{name} debe ser true o false")
    expected = config["expected_url_count"]
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 1):
        raise ValueError("expected_url_count debe ser null o un entero positivo")
    free_space = config["minimum_free_space_gb"]
    if isinstance(free_space, bool) or not isinstance(free_space, (int, float)) or free_space <= 0:
        raise ValueError("minimum_free_space_gb debe ser positivo")
    height = config["max_video_height"]
    if isinstance(height, bool) or not isinstance(height, int) or height < 1:
        raise ValueError("max_video_height debe ser un entero positivo")
    version = config["minimum_yt_dlp_version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", version):
        raise ValueError("minimum_yt_dlp_version debe usar YYYY.MM.DD")
    return config


def load_and_validate_urls(root: Path, config: dict) -> list[str]:
    path = root / "urls.txt"
    if not path.is_file():
        raise ValueError("No existe urls.txt")
    urls = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not urls:
        raise ValueError("urls.txt debe contener al menos una URL válida")
    seen: set[str] = set()
    for index, url in enumerate(urls, start=1):
        if url in seen:
            raise ValueError(f"URL duplicada en urls.txt (línea {index})")
        seen.add(url)
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname != "unab-cl.zoom.us":
            raise ValueError(f"La URL de la línea {index} no pertenece a unab-cl.zoom.us")
        if "/rec/play/" not in parsed.path:
            raise ValueError(f"La URL de la línea {index} no contiene /rec/play/")
    expected = config.get("expected_url_count")
    if expected is not None and len(urls) != expected:
        raise ValueError(f"Se esperaban {expected} URLs, pero se encontraron {len(urls)}")
    return urls


def ensure_directories(root: Path) -> None:
    for relative in REQUIRED_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "metadata" / "download-archive.txt").touch(exist_ok=True)


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", value)
    if not match:
        raise ValueError(value)
    return tuple(int(part) for part in match.groups())


def _probe(
    command: list[str],
    name: str,
    report: PrebootReport,
    runner: Callable,
) -> str | None:
    try:
        completed = runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        report.errors.append(f"No se encontró {name} en PATH")
        return None
    except OSError as exc:
        report.errors.append(f"No se pudo ejecutar {name}: {exc}")
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "error desconocido").strip()
        report.errors.append(f"{name} devolvió un error: {detail}")
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    first_line = output.splitlines()[0] if output else "desconocida"
    report.versions[name] = first_line
    return first_line


def run_preboot(
    root: Path,
    config: dict,
    urls: list[str],
    runner: Callable = subprocess.run,
    importer: Callable[[str], object] = importlib.import_module,
    disk_usage: Callable = shutil.disk_usage,
) -> PrebootReport:
    report = PrebootReport(ok=False, url_count=len(urls))
    if sys.version_info < (3, 10):
        report.errors.append("Se requiere Python 3.10 o posterior")

    try:
        ensure_directories(root)
        marker = root / "temp" / f".write-test-{uuid.uuid4().hex}"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink()
    except OSError as exc:
        report.errors.append(f"No se pueden preparar las carpetas del proyecto: {exc}")

    yt_version = _probe(yt_dlp_command("--version"), "yt-dlp", report, runner)
    if yt_version is not None:
        try:
            if _version_tuple(yt_version) < _version_tuple(config["minimum_yt_dlp_version"]):
                report.errors.append(
                    "Es necesario actualizar yt-dlp: "
                    f"instalado {yt_version}, mínimo {config['minimum_yt_dlp_version']}"
                )
        except ValueError:
            report.errors.append(f"No se pudo interpretar la versión de yt-dlp: {yt_version}")

    _probe(["ffmpeg", "-version"], "ffmpeg", report, runner)
    _probe(["ffprobe", "-version"], "ffprobe", report, runner)

    ctranslate = None
    for module_name in ("faster_whisper", "ctranslate2"):
        try:
            module = importer(module_name)
            version = getattr(module, "__version__", "instalado")
            report.versions[module_name] = str(version)
            if module_name == "ctranslate2":
                ctranslate = module
        except (ImportError, OSError) as exc:
            report.errors.append(f"No se puede importar {module_name}: {exc}")

    nvidia_visible = False
    if config.get("prefer_gpu"):
        try:
            completed = runner(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            nvidia_visible = completed.returncode == 0 and bool((completed.stdout or "").strip())
        except (FileNotFoundError, OSError):
            nvidia_visible = False
        cuda_count = 0
        if ctranslate is not None:
            try:
                cuda_count = int(ctranslate.get_cuda_device_count())
            except (AttributeError, RuntimeError, ValueError, OSError) as exc:
                report.warnings.append(f"CTranslate2 no pudo consultar CUDA: {exc}")
        report.cuda_candidate = nvidia_visible and cuda_count > 0
        if not report.cuda_candidate:
            report.warnings.append("CUDA no aparece disponible; la transcripción usará CPU si la prueba real falla")

    try:
        usage = disk_usage(root)
        free_gb = usage.free / (1024**3)
        report.versions["free_space_gb"] = f"{free_gb:.1f}"
        if free_gb < float(config["minimum_free_space_gb"]):
            report.errors.append(
                f"Espacio libre insuficiente: {free_gb:.1f} GiB; "
                f"se requieren {config['minimum_free_space_gb']} GiB"
            )
    except OSError as exc:
        report.errors.append(f"No se pudo comprobar el espacio libre: {exc}")

    report.ok = not report.errors
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    root = Path(__file__).resolve().parents[1]
    try:
        config = load_config(root)
        urls = load_and_validate_urls(root, config)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2
    report = run_preboot(root, config, urls)
    print(f"URLs encontradas: {report.url_count}")
    for name, version in sorted(report.versions.items()):
        print(f"- {name}: {version}")
    for warning in report.warnings:
        print(f"[AVISO] {warning}")
    for error in report.errors:
        print(f"[ERROR] {error}")
    print("Preboot correcto." if report.ok else "Preboot con errores.")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
