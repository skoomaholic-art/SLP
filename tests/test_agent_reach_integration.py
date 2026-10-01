import unittest
from unittest.mock import Mock, patch

from services import agent_reach_web
from verifiers.broadcast_occurrence import _extract_matching_pages


class AgentReachBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_bridge_uses_agent_reach_web_channel(self):
        fake_channel = Mock()
        fake_channel.read.return_value = "# Sports schedule\nLIVE 20:00 Team A - Team B"

        with patch.object(agent_reach_web, "WebChannel", return_value=fake_channel):
            text = await agent_reach_web.read_public_url(
                "https://example.com/sports",
                timeout_seconds=1,
            )

        self.assertIn("LIVE 20:00", text)
        fake_channel.read.assert_called_once_with("https://example.com/sports")


class AgentReachVerifierFallbackTests(unittest.TestCase):
    @patch(
        "verifiers.broadcast_occurrence.read_public_url_sync",
        return_value="September 12 2026 kickoff 5:00 PM EDT",
    )
    @patch(
        "verifiers.broadcast_occurrence.openserp_extract_batch",
        return_value=[],
    )
    def test_agent_reach_fills_missing_openserp_extraction(self, extract, reader):
        result = _extract_matching_pages([
            {
                "title": "Silva vs Delgado",
                "snippet": "Noche UFC",
                "url": "https://www.ufc.com/event/example",
                "rank": 1,
            }
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["extraction_transport"], "agent_reach")
        self.assertIn("kickoff", result[0]["content"])
        reader.assert_called_once_with("https://www.ufc.com/event/example")
        extract.assert_called_once()


if __name__ == "__main__":
    unittest.main()
