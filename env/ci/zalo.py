"""Small Zalo Bot API client; never exposes token-bearing URLs in errors."""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ZaloError(RuntimeError):
    pass


def api_call(token, method, payload=None, timeout=15):
    if not token or not method.isalnum():
        raise ZaloError("Zalo credentials or method missing")
    request = Request(
        "https://bot-api.zaloplatforms.com/bot" + token + "/" + method,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        retry = False
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read())
            if body.get("ok") is True:
                return body.get("result", {})
            code = body.get("error_code", "unknown")
            # A timed-out long poll has no update. Keep this defensive handling
            # method-specific: a timed-out send must never count as delivered.
            if method == "getUpdates" and code == 408:
                return {}
            # A verified bot without a registered webhook returns Not Found.
            # This is a configuration lookup only; other methods retain 404 errors.
            if method == "getWebhookInfo" and code == 404:
                return {}
            # API descriptions and URL-bearing exception text are intentionally omitted.
            error = "Zalo API rejected request (code {})".format(code if isinstance(code, int) else "unknown")
            retry = isinstance(code, int) and (code == 429 or code >= 500)
        except HTTPError as exc:
            error = "Zalo HTTP request failed (status {})".format(exc.code)
            retry = exc.code == 429 or exc.code >= 500
            exc.close()
            if method == "getUpdates" and exc.code == 408:
                return {}
            if method == "getWebhookInfo" and exc.code == 404:
                return {}
        except (URLError, TimeoutError, OSError):
            error = "Zalo network request failed"
            retry = True
        except (ValueError, TypeError, AttributeError):
            error = "Zalo returned an invalid response"
        if not retry or attempt == 2:
            raise ZaloError(error) from None
        time.sleep(2 ** attempt)


def send_message(token, chat_id, text):
    if chat_id is None or not str(chat_id).strip() or not text:
        raise ZaloError("Zalo recipient or message missing")
    return api_call(token, "sendMessage", {"chat_id": str(chat_id), "text": str(text)[:2000]})
