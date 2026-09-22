from __future__ import annotations

import unittest

from services.openrouter_client import OpenRouterClient, OpenRouterError, _extract_text


class OpenRouterClientTests(unittest.TestCase):
    def test_extracts_string_content(self) -> None:
        payload = {"choices": [{"message": {"content": "  OK  "}}]}
        self.assertEqual(_extract_text(payload), "OK")

    def test_extracts_text_parts(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "first"},
                            {"type": "text", "text": "second"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(_extract_text(payload), "first\nsecond")

    def test_missing_key_fails_before_network(self) -> None:
        client = OpenRouterClient(api_key="")
        self.assertFalse(client.enabled)
        with self.assertRaises(OpenRouterError):
            client._headers()


if __name__ == "__main__":
    unittest.main()
