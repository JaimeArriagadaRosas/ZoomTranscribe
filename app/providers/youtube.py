from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from app.core.browser import prepare_browser_session
from app.providers.base import DownloadedMedia
from app.providers.ytdlp import download_video


class YouTubeProvider:
    name = "youtube"

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        allowed = {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "music.youtube.com",
        }
        if parsed.scheme not in {"http", "https"} or host not in allowed:
            raise ValueError("La URL no pertenece a YouTube")

    def download(self, root: Path, job_dir: Path, url: str, config: dict, logger) -> DownloadedMedia:
        self.validate_url(url)
        print("[1/5] Comprobando acceso a YouTube...")
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
