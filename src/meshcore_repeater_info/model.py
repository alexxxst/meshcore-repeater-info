"""Data structures shared by the radio poller and the CSV report."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PollResult:
    """Everything learned about one repeater during a single poll."""

    key: str
    contact_name: str
    repeater_name: str | None = None
    advert_timestamp: int | None = None
    firmware_version: str | None = None
    owner_info: str | None = None
    uptime_seconds: int | None = None
    tx_airtime_seconds: int | None = None
    rx_airtime_seconds: int | None = None
    login_timestamp: int | None = None
    clock_skew_seconds: int | None = None
    clock_status: str | None = None
    noise_floor_dbm: int | None = None
    debug_flags: int | None = None
    is_admin: bool | None = None
    login_password: str | None = None
    login_method: str | None = None
    login_attempts_last: int = 0
    login_attempts_total: int = 0
    status: str = "in_progress"
    error: str | None = None
