import io
import json
import unittest
from unittest.mock import patch

import verifiers.broadcast_occurrence as occurrence


class OpenSerpModeAnyTests(unittest.TestCase):
    def test_remote_reconciliation_uses_mode_any(self):
        payload = json.dumps({"results": [{"title": "ok"}], "meta": {}}).encode("utf-8")
        with (
            patch.object(occurrence, "OPENSERP_BASE_URL", "http://openserp.internal:7000"),
            patch.object(occurrence.urllib.request, "urlopen", return_value=io.BytesIO(payload)) as opener,
        ):
            results, meta = occurrence._openserp_search_any(
                "test event",
                ("yandex", "baidu", "ecosia"),
                25,
            )

        request = opener.call_args.args[0]
        self.assertIn("mode=any", request.full_url)
        self.assertIn("engines=yandex%2Cbaidu%2Cecosia", request.full_url)
        self.assertEqual(results, [{"title": "ok"}])
        self.assertEqual(meta["slp_mode"], "any")


if __name__ == "__main__":
    unittest.main()
