import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.metadata import RecordingStore
from scripts.utils import recording_id


class RecordingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = RecordingStore(self.root)
        self.url = "https://unab-cl.zoom.us/rec/play/metadata-example"
        self.rid = recording_id(self.url)

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_one_record_and_artifact_directory_per_url(self):
        records = self.store.ensure([self.url])
        self.assertEqual(records[0]["id"], self.rid)
        self.assertEqual(records[0]["state"], "pending")
        self.assertTrue((self.root / "metadata" / "records" / f"{self.rid}.json").is_file())
        self.assertTrue((self.root / "artifacts" / self.rid).is_dir())

    def test_ensure_is_idempotent_and_preserves_existing_state(self):
        self.store.ensure([self.url])
        self.store.transition(self.rid, "downloaded", title="Clase repetida")
        records = self.store.ensure([self.url])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], "downloaded")
        self.assertEqual(records[0]["title"], "Clase repetida")

    def test_list_records_ignores_source_metadata_json(self):
        self.store.ensure([self.url])
        source = self.root / "metadata" / "records" / f"{self.rid}.source.json"
        source.write_text('{"title":"source"}', encoding="utf-8")
        self.assertEqual([record["id"] for record in self.store.list_records()], [self.rid])

    def test_indexes_contain_relative_paths_and_required_columns(self):
        self.store.ensure([self.url])
        record = self.store.load(self.rid)
        record.update({"title": "Clase uno", "date": "2026-09-21", "duration": 3600.5})
        record["download"] = {
            "file": f"downloads/2026_CIBERSEGURIDAD_{self.rid}.mp4",
            "info": f"metadata/records/{self.rid}.source.json",
            "validated": True,
        }
        record["transcripts"] = {
            "txt": f"transcripts/txt/{self.rid}.txt",
            "srt": f"transcripts/srt/{self.rid}.srt",
            "vtt": f"transcripts/vtt/{self.rid}.vtt",
            "validated": True,
        }
        record["whisper"] = {
            "model": "large-v3",
            "language": "es",
            "device": "cpu",
            "compute_type": "int8",
        }
        record["state"] = "completed"
        self.store.save(record)

        index = json.loads((self.root / "metadata" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index[0]["download_file"], record["download"]["file"])
        self.assertFalse(Path(index[0]["download_file"]).is_absolute())
        with (self.root / "metadata" / "index.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["id"], self.rid)
        self.assertEqual(row["whisper_device"], "cpu")

    def test_rejects_absolute_durable_paths(self):
        self.store.ensure([self.url])
        record = self.store.load(self.rid)
        record["download"]["file"] = str((self.root / "absolute.mp4").resolve())
        with self.assertRaisesRegex(ValueError, "relativa"):
            self.store.save(record)

    def test_transition_records_history_and_recovers_transient_states(self):
        self.store.ensure([self.url])
        self.store.transition(self.rid, "downloading", attempts={"download": 1, "transcription": 0})
        self.store.recover_interrupted()
        record = self.store.load(self.rid)
        self.assertEqual(record["state"], "interrupted")
        self.assertEqual(record["attempts"]["download"], 1)
        self.assertEqual(record["history"][-1]["state"], "interrupted")

    def test_rebuild_keeps_previous_json_index_if_atomic_replace_fails(self):
        self.store.ensure([self.url])
        index_path = self.root / "metadata" / "index.json"
        old = '[{"state":"old"}]\n'
        index_path.write_text(old, encoding="utf-8")
        with mock.patch("scripts.utils.os.replace", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                self.store.rebuild_indexes()
        self.assertEqual(index_path.read_text(encoding="utf-8"), old)


if __name__ == "__main__":
    unittest.main()
