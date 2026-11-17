from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx

from telltale.http import _retry_after


def status_error(headers: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/")
    response = httpx.Response(429, headers=headers, request=request)
    return httpx.HTTPStatusError("too many", request=request, response=response)


def test_retry_after_seconds_and_date():
    assert _retry_after(status_error({"Retry-After": "7"})) == 7.0
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    seconds = _retry_after(status_error({"Retry-After": format_datetime(when, usegmt=True)}))
    assert 25 <= seconds <= 30
    assert _retry_after(status_error({"Retry-After": "soon"})) is None
    assert _retry_after(status_error({})) is None
