from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def normalize_timezone(value: str) -> str:
    value = value.strip()
    if not value:
        raise ZoneInfoNotFoundError("empty timezone")
    ZoneInfo(value)
    return value


def user_today(timezone_name: str | None) -> date:
    zone = _zone(timezone_name)
    return datetime.now(zone).date()


def user_day_start_utc(timezone_name: str | None, day: date | None = None) -> datetime:
    zone = _zone(timezone_name)
    local_day = day or datetime.now(zone).date()
    local_start = datetime.combine(local_day, time.min, tzinfo=zone)
    return local_start.astimezone(UTC)


def user_local_date(value: datetime, timezone_name: str | None) -> date:
    return user_local_datetime(value, timezone_name).date()


def user_local_datetime(value: datetime, timezone_name: str | None) -> datetime:
    zone = _zone(timezone_name)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(zone)


def _zone(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
