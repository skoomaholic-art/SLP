import unittest
from bs4 import BeautifulSoup

from services.live_evidence import classify_live_evidence, extract_asset_hints


class LiveEvidenceTests(unittest.TestCase):
    def test_live_text_is_high_confidence(self):
        evidence = classify_live_evidence("LIVE 18:00 Матч")
        self.assertTrue(evidence.is_live)
        self.assertEqual(evidence.method, "official_live_text")
        self.assertEqual(evidence.confidence, "high")

    def test_replay_overrides_live(self):
        evidence = classify_live_evidence("LIVE 18:00 Матч. ПОВТОР")
        self.assertFalse(evidence.is_live)
        self.assertEqual(evidence.state, "not_live")

    def test_unknown_is_not_promoted_to_live(self):
        evidence = classify_live_evidence("18:00 Матч")
        self.assertFalse(evidence.is_live)
        self.assertEqual(evidence.state, "unknown")

    def test_asset_requires_source_allowlist(self):
        soup = BeautifulSoup('<a><img src="/assets/live-v3.gif"></a>', "html.parser")
        hints = extract_asset_hints(soup.a)
        self.assertFalse(classify_live_evidence("Матч", asset_hints=hints).is_live)
        evidence = classify_live_evidence(
            "Матч", asset_hints=hints, live_asset_patterns=("/assets/live-v3.gif",)
        )
        self.assertTrue(evidence.is_live)
        self.assertEqual(evidence.method, "official_live_asset")


if __name__ == "__main__":
    unittest.main()
