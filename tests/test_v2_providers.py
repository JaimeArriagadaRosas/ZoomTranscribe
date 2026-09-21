import unittest

from app.providers.youtube import YouTubeProvider
from app.providers.zoom import ZoomProvider


class ProviderValidationTests(unittest.TestCase):
    def test_zoom_accepts_institutional_recordings(self):
        ZoomProvider().validate_url("https://institucion.zoom.us/rec/play/example")

    def test_zoom_rejects_non_recording_urls(self):
        with self.assertRaises(ValueError):
            ZoomProvider().validate_url("https://institucion.zoom.us/j/123")

    def test_youtube_accepts_common_hosts(self):
        provider = YouTubeProvider()
        provider.validate_url("https://www.youtube.com/watch?v=abc")
        provider.validate_url("https://youtu.be/abc")

    def test_youtube_rejects_other_hosts(self):
        with self.assertRaises(ValueError):
            YouTubeProvider().validate_url("https://example.com/watch?v=abc")


if __name__ == "__main__":
    unittest.main()
