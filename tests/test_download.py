import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess

from scripts.download import (
    build_yt_dlp_command,
    classify_download_error,
    download_recording,
    probe_media,
)
from scripts.metadata import RecordingStore
from scripts.utils import recording_id


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = RecordingStore(self.root)
        self.url = "https://unab-cl.zoom.us/rec/play/download-example"
        self.record = self.store.ensure([self.url])[0]
        self.rid = recording_id(self.url)
        self.config = {
            "browser": "opera",
            "keep_video": True,
            "max_video_height": 720,
        }
        self.logger = logging.getLogger(f"test-download-{id(self)}")
        self.logger.addHandler(logging.NullHandler())

    def tearDown(self):
        self.temporary.cleanup()

    def test_command_uses_opera_archive_resume_windows_names_and_height_cap(self):
        command = build_yt_dlp_command(self.root, self.record, self.config)
        self.assertEqual(command[:3], [sys.executable, "-m", "yt_dlp"])
        self.assertEqual(command[command.index("--cookies-from-browser") + 1], "opera")
        self.assertIn("--download-archive", command)
        self.assertIn("--continue", command)
        self.assertIn("--windows-filenames", command)
        self.assertIn("--write-info-json", command)
        self.assertTrue(any("height<=720" in item for item in command))
        self.assertEqual(command[-1], self.url)

    def test_archive_can_be_disabled_for_controlled_recovery(self):
        command = build_yt_dlp_command(self.root, self.record, self.config, use_archive=False)
        self.assertNotIn("--download-archive", command)

    def test_probe_rejects_empty_or_unreadable_media(self):
        media = self.root / "empty.mp4"
        media.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "vacío"):
            probe_media(media, runner=lambda *args, **kwargs: None)

        media.write_bytes(b"not-empty")

        def failed_probe(command, **kwargs):
            return CompletedProcess(command, 1, "", "Invalid data")

        with self.assertRaisesRegex(ValueError, "ffprobe"):
            probe_media(media, runner=failed_probe)

    def test_probe_returns_positive_duration(self):
        media = self.root / "class.mp4"
        media.write_bytes(b"media")

        def runner(command, **kwargs):
            return CompletedProcess(command, 0, json.dumps({"format": {"duration": "3600.25"}}), "")

        self.assertEqual(probe_media(media, runner=runner)["duration"], 3600.25)

    def test_locked_opera_cookie_database_gets_specific_error(self):
        error = classify_download_error(
            "ERROR: Could not copy Chrome cookie database. The process cannot access the file"
        )
        self.assertEqual(error.kind, "opera_cookies_locked")
        self.assertIn("base de cookies", error.message)

    def test_success_moves_media_and_source_metadata_to_stable_relative_paths(self):
        def runner(command, **kwargs):
            if command[0] == "ffprobe":
                return CompletedProcess(command, 0, '{"format":{"duration":"42.5"}}', "")
            temp_dir = self.root / "temp" / self.rid
            temp_dir.mkdir(parents=True, exist_ok=True)
            media = temp_dir / "Zoom title [zoom-id].mp4"
            media.write_bytes(b"real media bytes")
            info = temp_dir / "Zoom title [zoom-id].info.json"
            info.write_text(
                json.dumps({"title": "Redes: clase / uno", "upload_date": "20260920", "duration": 42.5}),
                encoding="utf-8",
            )
            return CompletedProcess(command, 0, str(media) + "\n", "")

        result = download_recording(
            self.root, self.store, self.record, self.config, self.logger, runner=runner
        )
        self.assertTrue(result.ok, result.error_message)
        self.assertTrue((self.root / result.relative_file).is_file())
        self.assertIn(self.rid, Path(result.relative_file).name)
        self.assertEqual(result.date, "2026-09-20")
        self.assertTrue((self.root / result.info_file).is_file())
        stored = self.store.load(self.rid)
        self.assertEqual(stored["state"], "downloaded")
        self.assertEqual(stored["download"]["file"], result.relative_file)

    def test_missing_local_file_retries_once_without_archive(self):
        ytdlp_commands = []

        def runner(command, **kwargs):
            if command[0] == "ffprobe":
                return CompletedProcess(command, 0, '{"format":{"duration":"3600"}}', "")
            ytdlp_commands.append(command)
            if len(ytdlp_commands) == 1:
                return CompletedProcess(command, 0, "[download] archive: already recorded\n", "")
            temp_dir = self.root / "temp" / self.rid
            temp_dir.mkdir(parents=True, exist_ok=True)
            media = temp_dir / "Recovered.mp4"
            media.write_bytes(b"recovered media")
            (temp_dir / "Recovered.info.json").write_text(
                '{"title":"Recovered","upload_date":"20260919","duration":3600}',
                encoding="utf-8",
            )
            return CompletedProcess(command, 0, str(media) + "\n", "")

        result = download_recording(
            self.root, self.store, self.record, self.config, self.logger, runner=runner
        )
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(len(ytdlp_commands), 2)
        self.assertIn("--download-archive", ytdlp_commands[0])
        self.assertNotIn("--download-archive", ytdlp_commands[1])

    def test_download_failure_is_persisted_without_exposing_full_url(self):
        def runner(command, **kwargs):
            return CompletedProcess(command, 1, "", f"ERROR auth failed for {self.url}")

        result = download_recording(
            self.root, self.store, self.record, self.config, self.logger, runner=runner
        )
        self.assertFalse(result.ok)
        stored = self.store.load(self.rid)
        self.assertEqual(stored["state"], "download_failed")
        self.assertNotIn(self.url, stored["error"]["message"])
        self.assertIn(f"<URL:{self.rid}>", stored["error"]["message"])


if __name__ == "__main__":
    unittest.main()
