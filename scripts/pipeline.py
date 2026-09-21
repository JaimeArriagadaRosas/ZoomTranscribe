from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .auth import AuthFailure, prepare_auth
from .check_dependencies import load_and_validate_urls, load_config, run_preboot
from .download import download_recording, probe_media
from .metadata import RecordingStore
from .media import audio_is_valid, ensure_mp3
from .finalize import sync_final_transcripts
from .transcribe import transcribe_recording, validate_transcript_set
from .utils import recording_id, resolve_relative


@dataclass
class PipelineDependencies:
    preboot: Callable
    download: Callable
    transcribe: Callable
    probe_media: Callable
    validate_transcripts: Callable
    ensure_audio: Callable = ensure_mp3
    audio_valid: Callable = audio_is_valid
    sync_final: Callable = sync_final_transcripts
    prepare_auth: Callable = prepare_auth


def default_dependencies() -> PipelineDependencies:
    return PipelineDependencies(
        preboot=run_preboot,
        download=download_recording,
        transcribe=transcribe_recording,
        probe_media=probe_media,
        validate_transcripts=validate_transcript_set,
        ensure_audio=ensure_mp3,
        audio_valid=audio_is_valid,
        sync_final=sync_final_transcripts,
        prepare_auth=prepare_auth,
    )


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Limit debe ser un entero positivo") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("Limit debe ser un entero positivo")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Descarga y transcribe grabaciones autorizadas de Zoom de CIBERSEGURIDAD."
    )
    parser.add_argument("-Limit", "--limit", type=_positive_int, help="máximo de grabaciones pendientes")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-DownloadOnly", "--download-only", action="store_true", help="solo descargar")
    mode.add_argument("-TranscribeOnly", "--transcribe-only", action="store_true", help="solo transcribir")
    return parser


def _download_path(root: Path, record: dict) -> Path | None:
    relative = (record.get("download") or {}).get("file")
    if not relative:
        return None
    try:
        path = resolve_relative(root, relative)
    except ValueError:
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def _transcripts_valid(root: Path, record: dict) -> bool:
    transcripts = record.get("transcripts") or {}
    try:
        paths = [resolve_relative(root, transcripts[kind]) for kind in ("txt", "srt", "vtt")]
        validate_transcript_set(*paths)
        return True
    except (KeyError, TypeError, ValueError, OSError):
        return False


def _download_valid(root: Path, record: dict, probe_media_fn: Callable | None = None) -> bool:
    path = _download_path(root, record)
    if path is None:
        return False
    if probe_media_fn is not None:
        try:
            probe_media_fn(path)
        except (ValueError, OSError):
            return False
    return True


def select_records(
    records: list[dict],
    mode: str,
    limit: int | None,
    root: Path,
    probe_media_fn: Callable | None = None,
) -> list[dict]:
    selected: list[dict] = []
    for record in records:
        download_ready = _download_valid(root, record, probe_media_fn)
        transcripts_ready = _transcripts_valid(root, record)
        if mode == "download":
            pending = not download_ready
        elif mode == "transcribe":
            pending = download_ready and not transcripts_ready
        else:
            pending = not (download_ready and transcripts_ready and record.get("state") == "completed")
        if pending:
            selected.append(record)
            if limit is not None and len(selected) >= limit:
                break
    return selected


def validate_completed_record(
    root: Path,
    record: dict,
    dependencies: PipelineDependencies,
) -> None:
    if record.get("state") != "completed":
        raise ValueError("El registro no está marcado como completed")
    download = _download_path(root, record)
    if download is None:
        raise ValueError("El archivo descargado no existe o está vacío")
    dependencies.probe_media(download)
    transcripts = record.get("transcripts") or {}
    try:
        paths = [resolve_relative(root, transcripts[kind]) for kind in ("txt", "srt", "vtt")]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Faltan rutas de transcripción válidas") from exc
    dependencies.validate_transcripts(*paths)
    whisper = record.get("whisper") or {}
    missing = [name for name in ("model", "language", "device", "compute_type") if not whisper.get(name)]
    if missing:
        raise ValueError(f"Falta configuración efectiva de Whisper: {', '.join(missing)}")


def _setup_logger(root: Path) -> tuple[logging.Logger, Path]:
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger = logging.getLogger(f"zoom-pipeline-{id(path)}-{time.time_ns()}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger, path


def _elapsed(started: float) -> str:
    total = int(time.monotonic() - started)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _print_summary(
    total: int,
    completed_before: int,
    downloaded_now: int,
    transcribed_now: int,
    failures: list[tuple[str, str]],
    pending: int,
    started: float,
    log_path: Path,
) -> None:
    print("\n==============================")
    print("RESUMEN")
    print("==============================")
    print(f"URLs encontradas: {total}")
    print(f"Ya completadas: {completed_before}")
    print(f"Descargadas ahora: {downloaded_now}")
    print(f"Transcritas ahora: {transcribed_now}")
    print(f"Fallidas: {len(failures)}")
    print(f"Pendientes: {pending}")
    print(f"Tiempo total: {_elapsed(started)}")
    print(f"Log: {log_path.relative_to(log_path.parents[1]).as_posix()}")
    if failures:
        print("\nFallidas:")
        for rid, reason in failures:
            print(f"- {rid}: {reason}")


def _run_pipeline_core(
    root: Path,
    args,
    dependencies: PipelineDependencies,
    started: float,
    logger: logging.Logger,
    log_path: Path,
) -> int:
    try:
        config = load_config(root)
        urls = load_and_validate_urls(root, config)
    except ValueError as exc:
        logger.error("Preboot de configuración: %s", exc)
        print(f"[ERROR] {exc}")
        return 2

    report = dependencies.preboot(root, config, urls)
    print(f"URLs encontradas: {len(urls)}")
    for warning in report.warnings:
        print(f"[AVISO] {warning}")
        logger.warning(warning)
    if not report.ok:
        for error in report.errors:
            print(f"[ERROR] {error}")
            logger.error(error)
        return 2

    store = RecordingStore(root)
    records = store.ensure(urls)
    store.recover_interrupted()
    records = [store.load(recording_id(url)) for url in urls]
    completed_before = 0
    for record in records:
        try:
            validate_completed_record(root, record, dependencies)
            completed_before += 1
        except (ValueError, OSError):
            pass

    mode = "download" if args.download_only else "transcribe" if args.transcribe_only else "full"
    
    downloaded_now = 0
    transcribed_now = 0
    failures: list[tuple[str, str]] = []
    interrupted = False

    auth_session = None
    auth_error = None
    downloads_enabled = True
    if mode in ("download", "full"):
        to_download = select_records(records, "download", args.limit, root, dependencies.probe_media)
        if to_download:
            print("\n--- PREPARANDO AUTENTICACIÓN DE ZOOM ---")
            try:
                auth_session = dependencies.prepare_auth(
                    root,
                    config,
                    to_download[0]["url"],
                    logger,
                )
                if auth_session.mode == "anonymous":
                    print("Autenticación: la URL funciona sin cookies.")
                else:
                    print(f"Autenticación: sesión preparada ({auth_session.source}).")
                    print("Las cookies se reutilizarán durante este lote; no se volverá a abrir la base del navegador.")
            except AuthFailure as exc:
                auth_error = str(exc)
                downloads_enabled = False
                print(f"[ERROR DE AUTENTICACIÓN] {exc}")
                print("No se intentarán las otras URLs con el mismo error.")
                if mode == "full":
                    print("Se continuará con MP3/transcripción de cualquier video que ya exista localmente.")
                logger.error("Autenticación de Zoom fallida: %s", exc)
                for detail in exc.attempts:
                    logger.debug("Auth intento: %s", detail)
        if to_download and downloads_enabled:
            print(f"\n--- INICIANDO FASE DE DESCARGAS ({len(to_download)} pendientes) ---")
        for record in (to_download if downloads_enabled else []):
            rid = record["id"]
            position = urls.index(record["url"]) + 1
            prefix = f"[{position}/{len(urls)}]"
            try:
                current = store.load(rid)
                print(f"{prefix} Descargando {rid}...")
                logger.info("%s descarga iniciada", rid)
                result = dependencies.download(root, store, current, config, logger, position=position, auth=auth_session)
                if not result.ok:
                    reason = result.error_message or "descarga fallida"
                    failures.append((rid, reason))
                    print(f"{prefix} Descarga fallida; se continúa con la siguiente.")
                    continue
                downloaded_now += 1
                print(f"{prefix} Descarga terminada.")
            except KeyboardInterrupt:
                interrupted = True
                error = {
                    "phase": "download",
                    "type": "interrupted",
                    "message": "Ejecución interrumpida por el usuario",
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                try:
                    store.transition(rid, "interrupted", error=error)
                except Exception:
                    pass
                print("\nInterrupción recibida; el trabajo parcial se conservará.")
                break
            except Exception as exc:
                message = str(exc).replace(str(root), "<PROJECT>")[:4000]
                failures.append((rid, message))
                error = {
                    "phase": "download",
                    "type": "unexpected_error",
                    "message": message,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                try:
                    store.transition(rid, "download_failed", error=error)
                except Exception:
                    pass
                print(f"{prefix} ERROR inesperado: {message}")

    if not interrupted and mode in ("download", "full"):
        records = [store.load(recording_id(url)) for url in urls]
        downloadable = [
            record
            for record in records
            if _download_valid(root, record, dependencies.probe_media)
        ]
        if downloadable:
            print(f"\n--- GENERANDO MP3 ({len(downloadable)} disponibles) ---")
        for record in downloadable:
            rid = record["id"]
            position = urls.index(record["url"]) + 1
            prefix = f"[{position}/{len(urls)}]"
            try:
                current = store.load(rid)
                if dependencies.audio_valid(root, current):
                    print(f"{prefix} MP3 existente; se conserva.")
                    continue
                source = _download_path(root, current)
                if source is None:
                    continue
                print(f"{prefix} Generando MP3...")
                relative_audio = dependencies.ensure_audio(root, source, logger)
                audio = {"file": relative_audio, "validated": True}
                store.transition(rid, current.get("state", "downloaded"), audio=audio)
                print(f"{prefix} MP3 listo.")
            except Exception as exc:
                message = str(exc).replace(str(root), "<PROJECT>")[:4000]
                failures.append((rid, f"MP3: {message}"))
                logger.exception("No se pudo generar MP3 para %s", rid)
                print(f"{prefix} Falló la generación de MP3; la transcripción continuará.")

    if not interrupted and mode in ("transcribe", "full"):
        records = [store.load(recording_id(url)) for url in urls]
        to_transcribe = select_records(records, "transcribe", args.limit, root, dependencies.probe_media)
        if to_transcribe:
            print(f"\n--- INICIANDO FASE DE TRANSCRIPCIÓN ({len(to_transcribe)} pendientes) ---")
        for record in to_transcribe:
            rid = record["id"]
            position = urls.index(record["url"]) + 1
            prefix = f"[{position}/{len(urls)}]"
            try:
                current = store.load(rid)
                print(f"{prefix} Transcribiendo {rid}...")
                logger.info("%s transcripción iniciada", rid)
                result = dependencies.transcribe(root, store, current, config, logger)
                if not result.ok:
                    reason = result.error_message or "transcripción fallida"
                    failures.append((rid, reason))
                    print(f"{prefix} Transcripción fallida; se continúa con la siguiente.")
                    continue
                transcribed_now += 1
                print(f"{prefix} TXT, SRT y VTT generados. Completado.")
            except KeyboardInterrupt:
                interrupted = True
                error = {
                    "phase": "transcription",
                    "type": "interrupted",
                    "message": "Ejecución interrumpida por el usuario",
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                try:
                    store.transition(rid, "interrupted", error=error)
                except Exception:
                    pass
                print("\nInterrupción recibida; el trabajo parcial se conservará.")
                break
            except Exception as exc:
                message = str(exc).replace(str(root), "<PROJECT>")[:4000]
                failures.append((rid, message))
                error = {
                    "phase": "transcription",
                    "type": "unexpected_error",
                    "message": message,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                try:
                    store.transition(rid, "transcription_failed", error=error)
                except Exception:
                    pass
                print(f"{prefix} Fallo inesperado; se continúa con la siguiente.")

    if not interrupted and mode in ("transcribe", "full"):
        try:
            published = dependencies.sync_final(root, store, urls)
            print(f"\nTranscripciones finales listas para Drive: {len(published)}/{len(urls)}")
            print("Carpeta: final_transcripts/")
        except Exception as exc:
            message = str(exc).replace(str(root), "<PROJECT>")[:4000]
            failures.append(("final_transcripts", message))
            logger.exception("No se pudo publicar la carpeta final de transcripciones")
            print(f"[ERROR] No se pudo preparar final_transcripts/: {message}")

    if auth_error:
        failures.append(("auth", auth_error))

    current_records = [store.load(recording_id(url)) for url in urls]
    complete_now = 0
    for record in current_records:
        try:
            validate_completed_record(root, record, dependencies)
            complete_now += 1
        except (ValueError, OSError):
            pass
    pending = max(0, len(urls) - complete_now - len(failures))
    _print_summary(
        len(urls),
        completed_before,
        downloaded_now,
        transcribed_now,
        failures,
        pending,
        started,
        log_path,
    )
    if interrupted:
        return 130
    return 1 if failures else 0


def run_pipeline(
    root: Path,
    args,
    dependencies: PipelineDependencies | None = None,
) -> int:
    dependencies = dependencies or default_dependencies()
    root = root.resolve()
    started = time.monotonic()
    logger, log_path = _setup_logger(root)
    try:
        return _run_pipeline_core(root, args, dependencies, started, logger, log_path)
    finally:
        for handler in list(logger.handlers):
            handler.flush()
            handler.close()
            logger.removeHandler(handler)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        
    if argv is None:
        argv = sys.argv[1:]
        
    if not argv:
        print("\n=== PIPELINE DE CIBERSEGURIDAD ===")
        print("1) Descargar todos los videos pendientes")
        print("2) Transcribir todos los videos locales pendientes")
        print("3) Ejecutar todo automáticamente (descargar y luego transcribir)")
        print("4) Salir")
        print("==================================\n")
        
        while True:
            try:
                choice = input("Elige una opción (1-4): ").strip()
            except (EOFError, KeyboardInterrupt):
                return 130
            if choice == "1":
                argv = ["-DownloadOnly"]
                break
            elif choice == "2":
                argv = ["-TranscribeOnly"]
                break
            elif choice == "3":
                argv = []
                break
            elif choice == "4":
                return 0
            else:
                print("Opción inválida.")

    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    return run_pipeline(root, args)

if __name__ == "__main__":
    raise SystemExit(main())
