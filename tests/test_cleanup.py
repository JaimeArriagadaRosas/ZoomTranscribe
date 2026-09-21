import tempfile
import unittest
from pathlib import Path

from app.core.cleanup import delete_all_generated, delete_downloaded_videos
from app.core.jobs import JobStore


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_delete_videos_preserves_audio_and_transcripts(self):
        store = JobStore(self.root)
        job = store.create("youtube", "https://youtu.be/example")
        video = job.directory / "video.mp4"
        audio = job.directory / "audio.mp3"
        txt = job.directory / "transcript.txt"
        video.write_bytes(b"video")
        audio.write_bytes(b"audio")
        txt.write_text("texto", encoding="utf-8")
        job.metadata["files"]["video"] = "video.mp4"
        job.metadata["files"]["audio"] = "audio.mp3"
        job.metadata["files"]["txt"] = "transcript.txt"
        store.save(job)

        removed = delete_downloaded_videos(self.root)
        self.assertEqual(removed, 1)
        self.assertFalse(video.exists())
        self.assertTrue(audio.exists())
        self.assertTrue(txt.exists())

    def test_full_cleanup_resets_numbering_but_preserves_private(self):
        store = JobStore(self.root)
        store.create("zoom", "https://example.zoom.us/rec/play/a")
        private = self.root / "private"
        private.mkdir()
        secret = private / "zoom.cookies.txt"
        secret.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        delete_all_generated(self.root)
        self.assertTrue(secret.exists())
        fresh = JobStore(self.root).create("youtube", "https://youtu.be/new")
        self.assertEqual(fresh.number, 1)


if __name__ == "__main__":
    unittest.main()
