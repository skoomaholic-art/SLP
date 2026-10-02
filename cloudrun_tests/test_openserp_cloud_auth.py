import os
import unittest
from unittest.mock import patch

from verifiers import web_search


class OpenSerpCloudAuthTests(unittest.TestCase):
    def setUp(self):
        self.old_token = web_search._OPENSERP_ID_TOKEN
        self.old_expiry = web_search._OPENSERP_ID_TOKEN_EXPIRES_AT
        web_search._OPENSERP_ID_TOKEN = ""
        web_search._OPENSERP_ID_TOKEN_EXPIRES_AT = 0.0

    def tearDown(self):
        web_search._OPENSERP_ID_TOKEN = self.old_token
        web_search._OPENSERP_ID_TOKEN_EXPIRES_AT = self.old_expiry

    def test_no_cloud_audience_keeps_local_openserp_unauthenticated(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENSERP_AUTH_AUDIENCE", None)
            headers = web_search._openserp_headers()
        self.assertEqual(headers, {"Accept": "application/json"})

    def test_private_cloud_openserp_uses_google_oidc_token(self):
        with patch.dict(
            os.environ,
            {"OPENSERP_AUTH_AUDIENCE": "https://slp-openserp.example.run.app"},
        ), patch(
            "google.oauth2.id_token.fetch_id_token",
            return_value="oidc-token",
        ) as fetch:
            headers = web_search._openserp_headers(json_body=True)
            again = web_search._openserp_headers()

        self.assertEqual(headers["Authorization"], "Bearer oidc-token")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(again["Authorization"], "Bearer oidc-token")
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
