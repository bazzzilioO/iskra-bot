import unittest

from helpers import (
    build_canonical_smartlink_url,
    build_canonical_smartlink_url_from_smartlink,
)


class CanonicalLinkTests(unittest.TestCase):
    def test_canonical_url_from_slugs(self):
        url = build_canonical_smartlink_url("test-artist", "test-release")
        self.assertEqual(url, "https://go.sreda.pw/test-artist/test-release")

    def test_canonical_url_stable_on_cover_updates(self):
        smartlink = {
            "artist": "Test Artist",
            "title": "Test Release",
            "artist_slug": "test-artist",
            "slug": "test-release",
        }
        canonical = build_canonical_smartlink_url_from_smartlink(smartlink)
        smartlink.update(
            {
                "cover_url": "https://example.com/cover.jpg",
                "cover_version": 2,
                "cover_source": {"type": "telegram", "file_id": "file_123"},
            }
        )
        self.assertEqual(canonical, build_canonical_smartlink_url_from_smartlink(smartlink))

    def test_canonical_url_stable_on_link_and_caption_updates(self):
        smartlink = {
            "artist": "Test Artist",
            "title": "Test Release",
            "artist_slug": "test-artist",
            "slug": "test-release",
        }
        canonical = build_canonical_smartlink_url_from_smartlink(smartlink)
        smartlink.update(
            {
                "links": {"spotify": "https://open.spotify.com/track/xyz"},
                "caption_text": "New caption",
                "comments": ["note"],
            }
        )
        self.assertEqual(canonical, build_canonical_smartlink_url_from_smartlink(smartlink))


if __name__ == "__main__":
    unittest.main()
