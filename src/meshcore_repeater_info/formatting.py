"""Formatting helpers for the human-friendly CSV report."""

from __future__ import annotations

from datetime import datetime, timezone


def format_uptime(seconds: int | None) -> str:
    """Format seconds as a short Russian uptime string."""
    if seconds is None or seconds < 0:
        return ""
    if seconds == 0:
        return "0 м"

    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds_left = divmod(remainder, 60)

    if days:
        return f"{days} дн {hours} ч {minutes} м"
    if hours:
        return f"{hours} ч {minutes} м"
    if minutes:
        return f"{minutes} м"
    return f"{seconds_left} с"


def format_percent(part: int | float | None, total: int | float | None) -> str:
    """Format part/total as a percent value with a comma decimal separator."""
    if part is None or total is None or total <= 0:
        return ""
    return f"{part / total * 100:.2f}".replace(".", ",")


def format_utc(timestamp: int | None) -> str:
    """Format a Unix timestamp as UTC without a timezone suffix (Excel-friendly)."""
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def format_clock_skew(skew: int | None) -> str:
    """Return a signed integer as text, or an empty string when unknown."""
    return "" if skew is None else str(skew)


def clock_status(skew: int | None, threshold: int = 600) -> str:
    """Classify the repeater clock difference against a threshold."""
    if skew is None:
        return "unknown"
    return "suspect" if abs(skew) > threshold else "ok"
