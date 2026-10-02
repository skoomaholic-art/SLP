import unittest

from services.channel_normalization import (
    canonical_channel_name, channel_similarity, normalize_channel_name,
)


class ChannelNormalizationTests(unittest.TestCase):
    def test_known_aliases(self):
        self.assertEqual(canonical_channel_name("KZ | SETANTA SPORTS 1 FHD"), "SETANTA SPORTS 1")
        self.assertEqual(canonical_channel_name("Setanta Qazaqstan HD"), "SETANTA SPORTS KZ")
        self.assertEqual(canonical_channel_name("Viasat Sport HD"), "viju+ Sport")
        self.assertEqual(canonical_channel_name("Q Sport Arena [KZ]"), "Q ARENA")

    def test_channel_numbers_do_not_conflate(self):
        self.assertEqual(channel_similarity("Setanta Sports 1", "Setanta Sports 2"), 0.0)
        self.assertGreater(channel_similarity("Setanta Sports 1", "Setanta 1 HD"), 0.8)


if __name__ == "__main__":
    unittest.main()
