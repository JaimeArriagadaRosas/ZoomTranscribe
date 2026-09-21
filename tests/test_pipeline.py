import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.check_dependencies import PrebootReport
from scripts.download import DownloadResult
from scripts.metadata import RecordingStore
from scripts.pipeline import (
    PipelineDependencies,
    build_parser,
    run_pipeline,
    select_records,
)
from scripts.transcribe import Segment, TranscriptResult, render_srt, render_txt, render_vtt


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.urls = [
            "https://unab-cl.zoom.us/rec/play/pipeline-a",
            "https://unab-cl.zoom.us/rec/play/pipeline-b",
        ]
        (self.root / "urls.txt").write_text("\n".join(self.urls) + "\n", encoding="utf-8")
        config = {
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
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def healthy_preboot(root, config, urls):
        return PrebootReport(ok=True, url_count=len(urls), versions={"yt-dlp": "2026.09.01"})

    @staticmethod
    def no_probe(path):
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise ValueError("invalid")
        return {"duration": 10.0}

    @staticmethod
    def validate_outputs(txt, srt, vtt):
        for path in (txt, srt, vtt):
            if not Path(path).is_file() or Path(path).stat().st_size == 0:
                raise ValueError("invalid")

    def dependencies(self, download, transcribe):
        return PipelineDependencies(
            preboot=self.healthy_preboot,
            download=download,
            transcribe=transcribe,
            probe_media=self.no_probe,
            validate_transcripts=self.validate_outputs,
        )

    def test_parser_accepts_windows_style_limit_and_modes(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["-Limit", "2"]).limit, 2)
        self.assertTrue(parser.parse_args(["-DownloadOnly"]).download_only)
        self.assertTrue(parser.parse_args(["-TranscribeOnly"]).transcribe_only)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["-DownloadOnly", "-TranscribeOnly"])

    def test_limit_one_never_advances_after_first_selected_failure(self):
        downloaded_ids = []

        def download(root, store, record, config, logger, position=0):
            downloaded_ids.append(record["id"])
            return DownloadResult(ok=False, error_kind="test", error_message="falló")

        deps = self.dependencies(download, lambda *args: TranscriptResult(ok=True))
        args = build_parser().parse_args(["-Limit", "1"])
        with redirect_stdout(io.StringIO()):
            code = run_pipeline(self.root, args, deps)
        self.assertEqual(code, 1)
        self.assertEqual(len(downloaded_ids), 1)

    def test_download_only_never_calls_transcriber(self):
        transcribed_ids = []

        def download(root, store, record, config, logger, position=0):
            media = root / "downloads" / f"class_{record['id']}.mp4"
            media.parent.mkdir(parents=True, exist_ok=True)
            media.write_bytes(b"media")
            store.transition(
                record["id"],
                "downloaded",
                download={"file": media.relative_to(root).as_posix(), "info": None, "validated": True},
            )
            return DownloadResult(ok=True, relative_file=media.relative_to(root).as_posix())

        def transcribe(root, store, record, config, logger):
            transcribed_ids.append(record["id"])
            return TranscriptResult(ok=True)

        args = build_parser().parse_args(["-DownloadOnly", "-Limit", "1"])
        with redirect_stdout(io.StringIO()):
            code = run_pipeline(self.root, args, self.dependencies(download, transcribe))
        self.assertEqual(code, 0)
        self.assertEqual(transcribed_ids, [])

    def test_transcribe_only_selects_only_nonempty_local_downloads(self):
        store = RecordingStore(self.root)
        records = store.ensure(self.urls)
        media = self.root / "downloads" / "ready.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"ready")
        first = records[0]
        first["download"] = {"file": "downloads/ready.mp4", "info": None, "validated": True}
        first["state"] = "downloaded"
        store.save(first)
        records = [store.load(record["id"]) for record in records]
        selected = select_records(records, "transcribe", None, self.root)
        self.assertEqual([record["id"] for record in selected], [first["id"]])

    def test_completed_record_with_valid_outputs_is_skipped(self):
        store = RecordingStore(self.root)
        record = store.ensure([self.urls[0]])[0]
        media = self.root / "downloads" / "complete.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"media")
        segment = [Segment(0, 1, "completo")]
        paths = {}
        for kind, content in (
            ("txt", render_txt(segment)),
            ("srt", render_srt(segment)),
            ("vtt", render_vtt(segment)),
        ):
            path = self.root / "transcripts" / kind / f"complete.{kind}"
            path.parent.mkdir(parents=True)
            path.write_text(content, encoding="utf-8")
            paths[kind] = path.relative_to(self.root).as_posix()
        record["download"] = {"file": "downloads/complete.mp4", "info": None, "validated": True}
        record["transcripts"] = {**paths, "validated": True}
        record["whisper"] = {
            "model": "large-v3", "language": "es", "device": "cpu", "compute_type": "int8"
        }
        record["state"] = "completed"
        store.save(record)
        self.assertEqual(select_records([record], "full", None, self.root), [])

    def test_completed_record_rejected_by_ffprobe_is_selected_for_recovery(self):
        store = RecordingStore(self.root)
        record = store.ensure([self.urls[0]])[0]
        media = self.root / "downloads" / "corrupt.mp4"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"not actually media")
        segment = [Segment(0, 1, "texto previo")]
        paths = {}
        for kind, content in (
            ("txt", render_txt(segment)),
            ("srt", render_srt(segment)),
            ("vtt", render_vtt(segment)),
        ):
            path = self.root / "transcripts" / kind / f"corrupt.{kind}"
            path.parent.mkdir(parents=True)
            path.write_text(content, encoding="utf-8")
            paths[kind] = path.relative_to(self.root).as_posix()
        record["download"] = {"file": "downloads/corrupt.mp4", "info": None, "validated": True}
        record["transcripts"] = {**paths, "validated": True}
        record["whisper"] = {
            "model": "large-v3", "language": "es", "device": "cpu", "compute_type": "int8"
        }
        record["state"] = "completed"
        store.save(record)

        attempted = []

        def bad_probe(path):
            raise ValueError("ffprobe rejected media")

        def download(root, store, record, config, logger, position=0):
            attempted.append(record["id"])
            return DownloadResult(ok=False, error_kind="test", error_message="recuperación pendiente")

        deps = self.dependencies(download, lambda *args: TranscriptResult(ok=True))
        deps.probe_media = bad_probe
        with redirect_stdout(io.StringIO()):
            code = run_pipeline(self.root, build_parser().parse_args(["-Limit", "1"]), deps)
        self.assertEqual(code, 1)
        self.assertEqual(attempted, [record["id"]])

    def test_unlimited_mode_continues_after_one_download_failure(self):
        attempted = []

        def download(root, store, record, config, logger, position=0):
            attempted.append(record["id"])
            return DownloadResult(ok=False, error_kind="test", error_message="falló")

        with redirect_stdout(io.StringIO()):
            code = run_pipeline(
                self.root,
                build_parser().parse_args([]),
                self.dependencies(download, lambda *args: TranscriptResult(ok=True)),
            )
        self.assertEqual(code, 1)
        self.assertEqual(len(attempted), 2)

    def test_preboot_failure_stops_before_download(self):
        attempted = []

        def preboot(root, config, urls):
            return PrebootReport(ok=False, errors=["ffprobe ausente"], url_count=len(urls))

        deps = self.dependencies(lambda *args: attempted.append(True), lambda *args: None)
        deps.preboot = preboot
        with redirect_stdout(io.StringIO()):
            code = run_pipeline(self.root, build_parser().parse_args([]), deps)
        self.assertEqual(code, 2)
        self.assertEqual(attempted, [])


class PipelineLauncherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_powershell_forwards_help_and_exit_code(self):
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "run.ps1"), "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DownloadOnly", completed.stdout)
        self.assertIn("máximo", completed.stdout)

    def test_batch_forwards_help_and_exit_code(self):
        completed = subprocess.run(
            ["cmd", "/c", str(self.root / "run.bat"), "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("TranscribeOnly", completed.stdout)
        self.assertIn("máximo", completed.stdout)

    def test_direct_preboot_output_is_utf8(self):
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.check_dependencies"],
            cwd=self.root,
            capture_output=True,
        )
        output = completed.stdout.decode("utf-8")
        self.assertIn("URLs encontradas", output)


if __name__ == "__main__":
    unittest.main()
