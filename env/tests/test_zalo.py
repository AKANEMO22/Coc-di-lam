import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
import zalo


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.data


class ZaloTests(unittest.TestCase):
    @patch("zalo.urlopen")
    def test_send_uses_string_chat_and_bounded_plain_text(self, request):
        request.return_value = Response({"ok": True, "result": {"message_id": "1"}})
        self.assertEqual(zalo.send_message("secret", 123, "x" * 2500), {"message_id": "1"})
        call = request.call_args.args[0]
        payload = json.loads(call.data)
        self.assertEqual(payload, {"chat_id": "123", "text": "x" * 2000})
        self.assertEqual(call.method, "POST")

    @patch("zalo.urlopen")
    def test_http_success_with_api_failure_is_not_success(self, request):
        request.return_value = Response({"ok": False, "error_code": 401, "description": "secret"})
        with self.assertRaises(zalo.ZaloError) as error:
            zalo.api_call("secret", "getMe")
        self.assertNotIn("secret", str(error.exception))
        self.assertIn("401", str(error.exception))
        self.assertEqual(request.call_count, 1)

    @patch("zalo.time.sleep")
    @patch("zalo.urlopen")
    def test_transient_network_failure_is_bounded_and_redacted(self, request, sleep):
        request.side_effect = URLError("https://bot-api.zaloplatforms.com/botsecret/sendMessage")
        with self.assertRaises(zalo.ZaloError) as error:
            zalo.send_message("secret", "group", "build failed")
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(request.call_count, 3)

    @patch("zalo.time.sleep")
    @patch("zalo.urlopen")
    def test_rate_limit_logical_error_retries(self, request, sleep):
        request.side_effect = [Response({"ok": False, "error_code": 429}), Response({"ok": True, "result": {}})]
        self.assertEqual(zalo.api_call("secret", "getMe"), {})
        self.assertEqual(request.call_count, 2)

    @patch("zalo.urlopen")
    def test_http_unauthorized_never_retries_or_leaks_url(self, request):
        request.side_effect = HTTPError("https://example/botsecret", 401, "secret", {}, io.BytesIO(b"secret"))
        with self.assertRaises(zalo.ZaloError) as error:
            zalo.api_call("secret", "getMe")
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(request.call_count, 1)

    @patch("zalo.urlopen")
    def test_missing_recipient_never_sends(self, request):
        with self.assertRaises(zalo.ZaloError):
            zalo.send_message("secret", "", "build")
        request.assert_not_called()

    @patch("zalo.urlopen")
    def test_get_updates_logical_timeout_is_an_empty_poll(self, request):
        request.return_value = Response({"ok": False, "error_code": 408})
        self.assertEqual(zalo.api_call("secret", "getUpdates", {"timeout": "10"}), {})
        self.assertEqual(request.call_count, 1)

    @patch("zalo.urlopen")
    def test_get_updates_http_timeout_is_an_empty_poll(self, request):
        request.side_effect = HTTPError("https://example/botsecret", 408, "secret", {}, io.BytesIO(b"secret"))
        self.assertEqual(zalo.api_call("secret", "getUpdates", {"timeout": "10"}), {})
        self.assertEqual(request.call_count, 1)

    @patch("zalo.urlopen")
    def test_send_message_timeout_is_never_reported_as_success(self, request):
        for response in (Response({"ok": False, "error_code": 408}),
                         HTTPError("https://example/botsecret", 408, "secret", {}, io.BytesIO(b"secret"))):
            with self.subTest(response_type=type(response).__name__):
                request.reset_mock()
                request.side_effect = response if isinstance(response, Exception) else None
                request.return_value = response
                with self.assertRaises(zalo.ZaloError) as error:
                    zalo.send_message("secret", "group", "build")
                self.assertIn("408", str(error.exception))
                self.assertNotIn("secret", str(error.exception))
                self.assertEqual(request.call_count, 1)

    @patch("zalo.urlopen")
    def test_missing_webhook_logical_response_means_no_webhook(self, request):
        request.return_value = Response({"ok": False, "error_code": 404, "description": "Not Found"})
        self.assertEqual(zalo.api_call("secret", "getWebhookInfo"), {})
        self.assertEqual(request.call_count, 1)

    @patch("zalo.urlopen")
    def test_missing_webhook_http_response_means_no_webhook(self, request):
        request.side_effect = HTTPError("https://example/botsecret", 404, "secret", {}, io.BytesIO(b"secret"))
        self.assertEqual(zalo.api_call("secret", "getWebhookInfo"), {})
        self.assertEqual(request.call_count, 1)

    @patch("zalo.urlopen")
    def test_other_methods_404_and_webhook_auth_errors_remain_fatal(self, request):
        for method, code in (("getMe", 404), ("getUpdates", 404), ("sendMessage", 404),
                             ("getWebhookInfo", 401), ("getWebhookInfo", 403)):
            with self.subTest(method=method, code=code):
                request.reset_mock()
                request.return_value = Response({"ok": False, "error_code": code})
                with self.assertRaises(zalo.ZaloError):
                    zalo.api_call("secret", method)
                self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
