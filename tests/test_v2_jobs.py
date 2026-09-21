import json
import tempfile
import unittest
from pathlib import Path

from app.core.jobs import JobStore


class JobStoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jobs_are_numbered_and_persisted(self):
        store = JobStore(self.root)
        first = store.create("zoom", "https://example.zoom.us/rec/play/abc")
        second = store.create("youtube", "https://youtu.be/xyz")
        self.assertEqual(first.number, 1)
        self.assertEqual(second.number, 2)
        data = json.loads((self.root / "data" / "jobs.json").read_text(encoding="utf-8"))
        self.assertEqual(data["next_number"], 3)

    def test_find_by_url_returns_existing_job(self):
        store = JobStore(self.root)
        created = store.create("youtube", "https://youtu.be/abc")
        found = store.find_by_url("https://youtu.be/abc")
        self.assertEqual(found["number"], created.number)

    def test_finalize_directory_uses_number_provider_and_title(self):
        store = JobStore(self.root)
        job = store.create("zoom", "https://example.zoom.us/rec/play/abc")
        job = store.finalize_directory_name(job, "Clase: Redes / Semana 1")
        self.assertTrue(job.directory.name.startswith("001_zoom_"))
        self.assertNotIn(":", job.directory.name)
        self.assertNotIn("/", job.directory.name)


if __name__ == "__main__":
    unittest.main()
