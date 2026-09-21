from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from app.core.browser import prepare_browser_session
from app.providers.base import DownloadedMedia
from app.providers.ytdlp import download_video


class ZoomProvider:
    name = "zoom"

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (host == "zoom.us" or host.endswith(".zoom.us")):
            raise ValueError("La URL no pertenece a Zoom")
        if "/rec/play/" not in parsed.path:
            raise ValueError("La URL de Zoom debe corresponder a una grabación /rec/play/")

    def download(self, root: Path, job_dir: Path, url: str, config: dict, logger) -> DownloadedMedia:
        self.validate_url(url)
        print("[1/5] Preparando acceso a Zoom...")
        session = prepare_browser_session(root, url, self.name, config, logger)
        print(f"      Acceso: {session.source}")
        print("[2/5] Descargando video...")
        return download_video(
            job_dir,
            url,
            session.yt_dlp_args,
            int(config.get("max_video_height", 720)),
            logger,
        )
