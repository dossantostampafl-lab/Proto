from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

_HEALTH_URL = "http://127.0.0.1:18000/health"
_STATUS_URL = "http://127.0.0.1:18000/live/status"


def wait_for_health() -> dict[str, object]:
    for _ in range(30):
        try:
            with urllib.request.urlopen(_HEALTH_URL, timeout=2) as response:
                payload = json.load(response)
                assert "no-store" in response.headers["Cache-Control"]
                return payload
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise AssertionError("standalone live image did not become healthy")


def assert_read_only() -> None:
    request = urllib.request.Request(_STATUS_URL, method="POST")
    try:
        urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as error:
        assert error.code == 405
        return
    raise AssertionError("standalone live API accepted a mutating HTTP method")


def main() -> None:
    payload = wait_for_health()
    assert payload["status"] == "ok"
    assert payload["mode"] == "LIVE_MONITORING"
    assert payload["financial_connectivity"] is False
    assert payload["real_money_execution"] is False
    assert_read_only()


if __name__ == "__main__":
    main()
