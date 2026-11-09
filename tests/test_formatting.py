import pytest

from meshcore_repeater_info.formatting import (
    clock_status,
    format_clock_skew,
    format_percent,
    format_uptime,
    format_utc,
)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, ""),
        (-1, ""),
        (0, "0 м"),
        (59, "59 с"),
        (60, "1 м"),
        (90, "1 м"),
        (3_599, "59 м"),
        (3_600, "1 ч 0 м"),
        (3_661, "1 ч 1 м"),
        (86_400, "1 дн 0 ч 0 м"),
        (90_061, "1 дн 1 ч 1 м"),
    ],
)
def test_format_uptime(seconds, expected):
    assert format_uptime(seconds) == expected


@pytest.mark.parametrize(
    ("part", "total", "expected"),
    [
        (None, 100, ""),
        (50, 0, ""),
        (50, -1, ""),
        (50, 100, "50,00"),
        (1, 3, "33,33"),
        (0, 100, "0,00"),
    ],
)
def test_format_percent(part, total, expected):
    assert format_percent(part, total) == expected


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (None, ""),
        (0, "1970-01-01 00:00:00"),
        (1_700_000_000, "2023-11-14 22:13:20"),
    ],
)
def test_format_utc(timestamp, expected):
    assert format_utc(timestamp) == expected


@pytest.mark.parametrize(
    ("skew", "threshold", "expected"),
    [
        (None, 600, "unknown"),
        (0, 600, "ok"),
        (600, 600, "ok"),
        (601, 600, "suspect"),
        (-601, 600, "suspect"),
        (301, 300, "suspect"),
    ],
)
def test_clock_status(skew, threshold, expected):
    assert clock_status(skew, threshold) == expected


@pytest.mark.parametrize(
    ("skew", "expected"),
    [(None, ""), (0, "0"), (42, "42"), (-42, "-42")],
)
def test_format_clock_skew(skew, expected):
    assert format_clock_skew(skew) == expected



