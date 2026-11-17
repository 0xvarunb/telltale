"""HTTP requests with a bounded retry on rate limits, server errors and dropped connections."""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx

USER_AGENT = "telltale/0.1 (briefings from published maritime accident reports)"
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 60.0

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Tests replace this so they never wait.
_sleep = time.sleep


def _retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    # Timeouts, refused or dropped connections. A bad URL scheme will not fix itself.
    return isinstance(error, httpx.TransportError) and not isinstance(
        error, httpx.UnsupportedProtocol
    )


def _retry_after(error: Exception) -> float | None:
    """Seconds asked for by a `Retry-After` header (seconds or an HTTP date), if any."""
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    value = error.response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def describe(error: Exception) -> str:
    """A short description of an HTTP error, for logs and reports."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code} for {error.request.url}"
    try:
        return f"{type(error).__name__} for {error.request.url}"
    except RuntimeError:  # an error raised without its request attached
        return type(error).__name__


def with_retry(request: Callable[[], T], attempts: int = MAX_ATTEMPTS) -> T:
    """Call `request`, retrying a 429, a 5xx or a transport error up to `attempts` tries in all.

    `request` must raise for a bad status (`raise_for_status`). Waits 1, 2, 4, 8, 16 seconds
    between tries (31 s in all), or what `Retry-After` asks for (at most a minute). Other
    errors, including a 4xx other than 429, are raised at once; the last error is raised when
    the tries run out.
    """
    for attempt in range(1, attempts + 1):
        try:
            return request()
        except (httpx.HTTPStatusError, httpx.TransportError) as error:
            if attempt == attempts or not _retryable(error):
                raise
            delay = _retry_after(error)
            if delay is None:
                delay = BACKOFF_SECONDS * 2 ** (attempt - 1)
            delay = min(delay, MAX_RETRY_AFTER_SECONDS)
            logger.warning(
                "%s; try %d of %d in %.0f s", describe(error), attempt + 1, attempts, delay
            )
            _sleep(delay)
    raise AssertionError("unreachable")


def get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """GET with the Telltale User-Agent, following redirects, retried by `with_retry`."""

    def send() -> httpx.Response:
        response = client.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        response.raise_for_status()
        return response

    return with_retry(send)
