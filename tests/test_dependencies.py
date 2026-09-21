import json
import tempfile
import unittest
import sys
from subprocess import CompletedProcess
from types import SimpleNamespace
from pathlib import Path

from scripts.check_dependencies import (
    ensure_directories,
    load_and_validate_urls,
    load_config,
    run_preboot,
)


class DependencyValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.write_config(None)

    def tearDown(self):
        self.temporary.cleanup()

    def write_config(self, expected_url_count):
        config = {
            "whisper_model": "large-v3",
            "language": "es",
            "browser": "opera",
            "prefer_gpu": True,
            "keep_video": True,
            "delete_temporary_audio": True,
            "expected_url_count": expected_url_count,
            "minimum_free_space_gb": 10,
            "minimum_yt_dlp_version": "2025.01.26",
            "max_video_height": 720,
        }
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def write_urls(self, urls):
        (self.root / "urls.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")

    @staticmethod
    def zoom_url(suffix):
        return f"https://unab-cl.zoom.us/rec/play/{suffix}"

    def test_accepts_any_positive_count_when_expected_count_is_null(self):
        self.write_urls([self.zoom_url("a"), self.zoom_url("b")])
        self.assertEqual(len(load_and_validate_urls(self.root, load_config(self.root))), 2)

    def test_enforces_expected_count_only_when_configured(self):
        self.write_config(3)
        self.write_urls([self.zoom_url("a"), self.zoom_url("b")])
        with self.assertRaisesRegex(ValueError, "esperaban 3"):
            load_and_validate_urls(self.root, load_config(self.root))

    def test_rejects_empty_file(self):
        self.write_urls([])
        with self.assertRaisesRegex(ValueError, "al menos una"):
            load_and_validate_urls(self.root, load_config(self.root))

    def test_rejects_duplicate_url_after_trimming(self):
        url = self.zoom_url("same")
        self.write_urls([url, f"  {url}  "])
        with self.assertRaisesRegex(ValueError, "duplicad"):
            load_and_validate_urls(self.root, load_config(self.root))

    def test_rejects_wrong_host_or_path(self):
        self.write_urls(["https://example.com/rec/play/a"])
        with self.assertRaisesRegex(ValueError, "unab-cl.zoom.us"):
            load_and_validate_urls(self.root, load_config(self.root))
        self.write_urls(["https://unab-cl.zoom.us/meeting/a"])
        with self.assertRaisesRegex(ValueError, "/rec/play/"):
            load_and_validate_urls(self.root, load_config(self.root))

    def test_accepts_zoom_host_case_insensitively(self):
        url = "https://UNAB-CL.ZOOM.US/rec/play/a"
        self.write_urls([url])
        self.assertEqual(load_and_validate_urls(self.root, load_config(self.root)), [url])

    def test_rejects_invalid_config_types(self):
        self.write_config(None)
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config["max_video_height"] = 0
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "max_video_height"):
            load_config(self.root)


class PrebootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = {
            "whisper_model": "large-v3",
            "language": "es",
            "browser": "opera",
            "prefer_gpu": True,
            "keep_video": True,
            "delete_temporary_audio": True,
            "expected_url_count": None,
            "minimum_free_space_gb": 10,
            "minimum_yt_dlp_version": "2025.01.26",
            "max_video_height": 720,
        }
        self.urls = ["https://unab-cl.zoom.us/rec/play/a"]

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def importer(name):
        if name == "ctranslate2":
            return SimpleNamespace(get_cuda_device_count=lambda: 1)
        return SimpleNamespace(__version__="1.2.0")

    @staticmethod
    def healthy_runner(command, **kwargs):
        executable = command[0]
        if command[:3] == [sys.executable, "-m", "yt_dlp"]:
            return CompletedProcess(command, 0, "2026.09.01\n", "")
        if executable in {"ffmpeg", "ffprobe"}:
            return CompletedProcess(command, 0, f"{executable} version 8.0\n", "")
        if executable == "nvidia-smi":
            return CompletedProcess(command, 0, "GPU 0\n", "")
        raise AssertionError(command)

    @staticmethod
    def generous_disk(_path):
        gib = 1024**3
        return SimpleNamespace(total=100 * gib, used=20 * gib, free=80 * gib)

    def test_ensure_directories_creates_required_layout_and_archive(self):
        ensure_directories(self.root)
        for relative in (
            "downloads", "transcripts/txt", "transcripts/srt", "transcripts/vtt",
            "metadata/records", "artifacts", "logs", "temp",
        ):
            self.assertTrue((self.root / relative).is_dir(), relative)
        self.assertTrue((self.root / "metadata" / "download-archive.txt").is_file())

    def test_healthy_preboot_reports_versions_url_count_and_cuda_candidate(self):
        report = run_preboot(
            self.root,
            self.config,
            self.urls,
            runner=self.healthy_runner,
            importer=self.importer,
            disk_usage=self.generous_disk,
        )
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.url_count, 1)
        self.assertTrue(report.cuda_candidate)
        self.assertEqual(report.versions["yt-dlp"], "2026.09.01")

    def test_old_ytdlp_and_missing_ffprobe_are_fatal(self):
        def runner(command, **kwargs):
            if command[:3] == [sys.executable, "-m", "yt_dlp"]:
                return CompletedProcess(command, 0, "2024.01.01\n", "")
            if command[0] == "ffprobe":
                raise FileNotFoundError("ffprobe")
            return self.healthy_runner(command, **kwargs)

        report = run_preboot(
            self.root,
            self.config,
            self.urls,
            runner=runner,
            importer=self.importer,
            disk_usage=self.generous_disk,
        )
        self.assertFalse(report.ok)
        self.assertTrue(any("actualizar yt-dlp" in error for error in report.errors))
        self.assertTrue(any("ffprobe" in error for error in report.errors))

    def test_insufficient_space_is_fatal_but_no_cuda_is_only_warning(self):
        gib = 1024**3

        def disk_usage(_path):
            return SimpleNamespace(total=100 * gib, used=95 * gib, free=5 * gib)

        def importer(name):
            if name == "ctranslate2":
                return SimpleNamespace(get_cuda_device_count=lambda: 0)
            return SimpleNamespace(__version__="1.2.0")

        report = run_preboot(
            self.root,
            self.config,
            self.urls,
            runner=self.healthy_runner,
            importer=importer,
            disk_usage=disk_usage,
        )
        self.assertFalse(report.ok)
        self.assertTrue(any("espacio" in error.lower() for error in report.errors))
        self.assertFalse(report.cuda_candidate)
        self.assertTrue(any("CPU" in warning for warning in report.warnings))


if __name__ == "__main__":
    unittest.main()
