"""Times and days. The service stores every time in UTC."""

import datetime

UTC = datetime.timezone.utc


def parse_timestamp(text):
    """Return an aware UTC datetime for an ISO 8601 time such as ``2026-11-20T09:15:00Z``."""
    value = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_timestamp(value):
    """Return an aware datetime as ISO 8601 in UTC, with a ``Z`` suffix."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_day(text):
    """Return the date in ``YYYY-MM-DD`` text."""
    return datetime.date.fromisoformat(str(text))


def day_start(day):
    """Return 00:00:00 UTC at the start of a date."""
    return datetime.datetime(day.year, day.month, day.day, tzinfo=UTC)


def in_day(value, day):
    """True when the aware datetime falls in the UTC day."""
    start = day_start(day)
    end = start + datetime.timedelta(days=1)
    return start <= value <= end


def now():
    return datetime.datetime.now(UTC)
