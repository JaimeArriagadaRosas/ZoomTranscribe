from __future__ import annotations

import sys
from pathlib import Path

from app.core.cleanup import delete_all_generated, delete_downloaded_videos
from app.core.pipeline import process_url


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_url(label: str) -> str:
    value = input(label).strip()
    if not value:
        raise ValueError("La URL no puede estar vacía")
    return value


def _process(provider: str) -> None:
    title = "ZOOM" if provider == "zoom" else "YOUTUBE"
    print(f"\n---------------- {title} ----------------")
    url = _read_url("Pega la URL: ")
    try:
        process_url(_root(), provider, url)
    except KeyboardInterrupt:
        print("\nInterrumpido. El trabajo queda guardado para reanudarlo.")
    except Exception as exc:
        print(f"\n[ERROR] {exc}")


def _cleanup_menu() -> None:
    root = _root()
    while True:
        print("\n------------- LIMPIEZA -------------")
        print("1) Eliminar solo videos descargados")
        print("2) Limpiar todos los trabajos generados")
        print("3) Volver")
        choice = input("\nElige una opción (1-3): ").strip()
        if choice == "1":
            removed = delete_downloaded_videos(root)
            print(f"Videos eliminados: {removed}")
        elif choice == "2":
            confirmation = input("Escribe LIMPIAR para confirmar: ").strip()
            if confirmation == "LIMPIAR":
                delete_all_generated(root)
                print("Contenido generado eliminado. La numeración vuelve a 001.")
            else:
                print("Operación cancelada.")
        elif choice == "3":
            return
        else:
            print("Opción inválida.")


def menu() -> int:
    while True:
        print("\n========================================")
        print("            MEDIATRANSCRIBE")
        print("========================================")
        print("1) Zoom")
        print("2) YouTube")
        print("3) Limpiar / administrar archivos")
        print("4) Salir")
        choice = input("\nSelecciona una opción (1-4): ").strip()
        if choice == "1":
            _process("zoom")
        elif choice == "2":
            _process("youtube")
        elif choice == "3":
            _cleanup_menu()
        elif choice == "4":
            return 0
        else:
            print("Opción inválida.")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        return menu()
    except (EOFError, KeyboardInterrupt):
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
