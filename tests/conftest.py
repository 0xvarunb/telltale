import pytest

from telltale import http


@pytest.fixture(autouse=True)
def retry_waits(monkeypatch) -> list[float]:
    """Retries never wait in tests; the waits they asked for are recorded here."""
    waits: list[float] = []
    monkeypatch.setattr(http, "_sleep", waits.append)
    return waits
