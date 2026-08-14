from datetime import UTC, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from bot.services.timezones import normalize_timezone, user_local_date


def test_normalize_timezone_accepts_valid_name() -> None:
    assert normalize_timezone("UTC") == "UTC"


def test_normalize_timezone_rejects_invalid_name() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        normalize_timezone("Not/AZone")


def test_user_local_date_uses_timezone() -> None:
    value = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)

    assert user_local_date(value, "America/Sao_Paulo").isoformat() == "2025-12-31"
