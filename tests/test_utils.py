import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.utils import (
    atomic_write_json,
    atomic_write_text,
    recording_id,
    resolve_relative,
    safe_component,
    to_relative,
)


class UtilityTests(unittest.TestCase):
    def test_recording_id_is_first_twenty_sha256_hex_characters(self):
        url = "https://unab-cl.zoom.us/rec/play/example"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        self.assertEqual(recording_id(f"  {url}  "), expected)

    def test_recording_id_preserves_url_text_except_surrounding_whitespace(self):
        url = "https://UNAB-CL.ZOOM.US/rec/play/example"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        self.assertEqual(recording_id(url), expected)

    def test_safe_component_handles_windows_reserved_and_unicode_titles(self):
        value = safe_component("CON", "clase")
        self.assertTrue(value)
        self.assertNotEqual(value.upper(), "CON")
        title = safe_component("clase: redes / ping áéí", "clase")
        self.assertNotIn(":", title)
        self.assertNotIn("/", title)
        self.assertIn("áéí", title)

    def test_safe_component_uses_fallback_for_only_invalid_characters(self):
        self.assertEqual(safe_component('<>:"/\\|?*', "clase"), "clase")

    def test_relative_paths_are_portable_and_project_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "downloads" / "class.mp4"
            target.parent.mkdir()
            target.write_bytes(b"video")
            relative = to_relative(root, target)
            self.assertEqual(relative, "downloads/class.mp4")
            self.assertEqual(resolve_relative(root, relative), target)
            with self.assertRaises(ValueError):
                to_relative(root, root.parent / "outside.mp4")
            with self.assertRaises(ValueError):
                resolve_relative(root, "../outside.mp4")

    def test_atomic_writes_replace_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_path = root / "state.txt"
            json_path = root / "state.json"
            text_path.write_text("old", encoding="utf-8")
            atomic_write_text(text_path, "new")
            atomic_write_json(json_path, {"status": "ok"})
            self.assertEqual(text_path.read_text(encoding="utf-8"), "new")
            self.assertEqual(json_path.read_text(encoding="utf-8"), '{\n  "status": "ok"\n}\n')


if __name__ == "__main__":
    unittest.main()
