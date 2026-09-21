import logging
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from scripts.metadata import RecordingStore
from scripts.transcribe import (
    Segment,
    build_flac_command,
    render_srt,
    render_txt,
    render_vtt,
    transcribe_recording,
    validate_transcript_set,
)
from scripts.utils import recording_id


class TranscriptFormatTests(unittest.TestCase):
    def test_renders_unicode_segment_in_all_three_formats(self):
        segments = [Segment(0.0, 8.125, "Ejecute ping 10.0.0.1 — señor.")]
        self.assertIn("[00:00:00 - 00:00:08]", render_txt(segments))
        self.assertIn("00:00:00,000 --> 00:00:08,125", render_srt(segments))
        self.assertIn("Ejecute ping 10.0.0.1 — señor.", render_srt(segments))
        self.assertTrue(render_vtt(segments).startswith("WEBVTT\n\n"))
        self.assertIn("00:00:00.000 --> 00:00:08.125", render_vtt(segments))

    def test_empty_segments_are_omitted(self):
        segments = [Segment(0, 1, "   "), Segment(1, 2, "texto")]
        self.assertNotIn("00:00:00", render_txt(segments))
        self.assertIn("texto", render_txt(segments))

    def test_empty_or_header_only_files_are_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            txt, srt, vtt = root / "a.txt", root / "a.srt", root / "a.vtt"
            txt.write_text("", encoding="utf-8")
            srt.write_text("", encoding="utf-8")
            vtt.write_text("WEBVTT\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_transcript_set(txt, srt, vtt)

    def test_validates_complete_transcript_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            txt, srt, vtt = root / "a.txt", root / "a.srt", root / "a.vtt"
            segments = [Segment(0, 2.5, "hola")]
            txt.write_text(render_txt(segments), encoding="utf-8")
            srt.write_text(render_srt(segments), encoding="utf-8")
            vtt.write_text(render_vtt(segments), encoding="utf-8")
            validate_transcript_set(txt, srt, vtt)


class FakeModel:
    def __init__(self, device, behavior, calls):
        self.device = device
        self.behavior = behavior
        self.calls = calls

    def transcribe(self, source, **kwargs):
        source = Path(source)
        self.calls.append((self.device, source.suffix.lower(), kwargs))
        if self.behavior == "direct-decode-fails" and source.suffix.lower() != ".flac":
            raise RuntimeError("Invalid data found when processing input")

        def segments():
            if self.behavior == "inference-fails":
                raise RuntimeError("CUDA driver error during inference")
            yield SimpleNamespace(start=0.0, end=2.25, text=" puerta de enlace 10.0.0.1 ")

        info = SimpleNamespace(language="es", language_probability=0.98, duration=2.25)
        return segments(), info


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = RecordingStore(self.root)
        self.url = "https://unab-cl.zoom.us/rec/play/transcribe-example"
        self.rid = recording_id(self.url)
        self.store.ensure([self.url])
        self.media = self.root / "downloads" / f"2026-09-20_CIBERSEGURIDAD_Clase_{self.rid}.mp4"
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b"media bytes")
        record = self.store.load(self.rid)
        record["download"] = {
            "file": self.media.relative_to(self.root).as_posix(),
            "info": None,
            "validated": True,
        }
        record["state"] = "downloaded"
        self.store.save(record)
        self.record = self.store.load(self.rid)
        self.config = {
            "whisper_model": "large-v3",
            "language": "es",
            "prefer_gpu": True,
            "delete_temporary_audio": True,
        }
        self.logger = logging.getLogger(f"test-transcribe-{id(self)}")
        self.logger.addHandler(logging.NullHandler())

    def tearDown(self):
        self.temporary.cleanup()

    def test_flac_command_is_mono_16khz_lossless(self):
        command = build_flac_command(Path("class.mp4"), Path("class.flac"))
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-c:a") + 1], "flac")
        self.assertTrue(str(command[-1]).endswith(".flac"))

    def test_cuda_inference_failure_retries_complete_transcription_on_cpu(self):
        created = []
        calls = []

        def factory(model, device, compute_type):
            created.append((model, device, compute_type))
            behavior = "inference-fails" if device == "cuda" else "success"
            return FakeModel(device, behavior, calls)

        result = transcribe_recording(
            self.root, self.store, self.record, self.config, self.logger, model_factory=factory
        )
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual((result.device, result.compute_type), ("cpu", "int8"))
        self.assertEqual(created, [("large-v3", "cuda", "float16"), ("large-v3", "cpu", "int8")])
        stored = self.store.load(self.rid)
        self.assertEqual(stored["state"], "completed")
        self.assertIn("CUDA driver error", stored["whisper"]["fallback_reason"])

    def test_direct_decode_failure_uses_flac_then_deletes_it_after_validation(self):
        self.config["prefer_gpu"] = False
        calls = []

        def factory(model, device, compute_type):
            return FakeModel(device, "direct-decode-fails", calls)

        def runner(command, **kwargs):
            Path(command[-1]).write_bytes(b"flac bytes")
            return CompletedProcess(command, 0, "", "")

        result = transcribe_recording(
            self.root,
            self.store,
            self.record,
            self.config,
            self.logger,
            model_factory=factory,
            runner=runner,
        )
        self.assertTrue(result.ok, result.error_message)
        self.assertTrue(result.used_flac)
        self.assertFalse((self.root / "temp" / self.rid / f"{self.rid}.flac").exists())
        self.assertTrue((self.root / result.txt).is_file())
        self.assertTrue((self.root / result.srt).is_file())
        self.assertTrue((self.root / result.vtt).is_file())

    def test_failed_flac_transcription_preserves_flac_for_retry(self):
        self.config["prefer_gpu"] = False

        class AlwaysFailModel:
            def transcribe(self, source, **kwargs):
                raise RuntimeError("decoder failed")

        def factory(model, device, compute_type):
            return AlwaysFailModel()

        def runner(command, **kwargs):
            Path(command[-1]).write_bytes(b"flac bytes")
            return CompletedProcess(command, 0, "", "")

        result = transcribe_recording(
            self.root,
            self.store,
            self.record,
            self.config,
            self.logger,
            model_factory=factory,
            runner=runner,
        )
        self.assertFalse(result.ok)
        self.assertTrue((self.root / "temp" / self.rid / f"{self.rid}.flac").is_file())
        self.assertEqual(self.store.load(self.rid)["state"], "transcription_failed")


if __name__ == "__main__":
    unittest.main()
