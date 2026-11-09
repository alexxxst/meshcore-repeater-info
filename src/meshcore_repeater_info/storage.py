"""Persistent, Excel-friendly CSV report storage."""

from __future__ import annotations

import csv
import os
from pathlib import Path

from .formatting import (
    format_clock_skew,
    format_percent,
    format_uptime,
    format_utc,
)
from .model import PollResult

FIELDS = [
    "repeater_key",
    "contact_name",
    "last_advert_utc",
    "firmware_version",
    "repeater_name",
    "owner_info",
    "uptime",
    "tx_airtime_pct",
    "rx_airtime_pct",
    "login_time_utc",
    "clock_skew_sec",
    "clock_status",
    "neighbours_count",
    "noise_floor_dbm",
    "debug_flags",
    "is_admin",
    "login_password",
    "login_method",
    "login_attempts_last",
    "login_attempts_total",
    "status",
    "error",
]

ALIVE_STATUSES = {"ok", "ok_old"}
SKIPPABLE_STATUSES = ALIVE_STATUSES | {"final_fail"}


class CsvFormatError(RuntimeError):
    """Raised when an existing report has an incompatible header."""


def should_skip(row: dict[str, str], force: bool) -> bool:
    """Return True when a repeater does not need to be polled again."""
    return not force and row.get("status", "") in SKIPPABLE_STATUSES


def should_process(row: dict[str, str] | None, mode: str) -> bool:
    """Select rows for the default, alive, dead or force retry mode."""
    status = row.get("status", "") if row else ""
    if mode == "force":
        return True
    if mode == "alive":
        return row is not None and status in ALIVE_STATUSES
    if mode == "dead":
        return row is None or status not in ALIVE_STATUSES
    if mode == "broken":
        return row is not None and status not in ALIVE_STATUSES
    return row is None or status not in SKIPPABLE_STATUSES


def _clean_owner_info(value: str) -> str:
    return (
        value.replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", " | ")
        .strip()
    )


class CsvStore:
    """CSV report with atomic writes after every repeater attempt."""

    def __init__(self, path: Path):
        self.path = path
        self.rows: dict[str, dict[str, str]] = {}

    def load(self) -> None:
        self.rows = {}
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            fieldnames = [name.strip() for name in (reader.fieldnames or [])]
            reader.fieldnames = fieldnames
            if "repeater_key" not in fieldnames:
                raise CsvFormatError(
                    f"{self.path} has no repeater_key column; refusing to overwrite it"
                )
            for raw in reader:
                key = (raw.get("repeater_key") or "").strip().lower()
                if not key:
                    continue
                row = {field: (raw.get(field) or "").strip() for field in FIELDS}
                row["repeater_key"] = key
                # Older reports used "acl" for any empty-password login.
                if row.get("login_method") == "acl":
                    row["login_method"] = "empty"
                self.rows[key] = row

    def get(self, key: str) -> dict[str, str] | None:
        return self.rows.get(key.lower())

    def ensure_row(self, key: str, contact_name: str, reset_total: bool) -> None:
        """Create a row before polling and optionally reset the attempt counter."""
        key = key.lower()
        row = self.rows.get(key)
        if row is None:
            row = {field: "" for field in FIELDS}
            row["repeater_key"] = key
            row["login_attempts_total"] = "0"
            row["login_attempts_last"] = "0"
        row["contact_name"] = contact_name
        if reset_total:
            row["login_attempts_total"] = "0"
            row["login_attempts_last"] = "0"
        row["status"] = "in_progress"
        row["error"] = ""
        self.rows[key] = row
        self._write()

    def record_attempt(self, key: str, total: int, last: int) -> None:
        row = self._row_for_update(key)
        row["login_attempts_total"] = str(total)
        row["login_attempts_last"] = str(last)
        row["status"] = "in_progress"
        self._write()

    def record_login_success(
        self,
        key: str,
        last: int,
        is_admin: bool,
        login_password: str,
        login_method: str,
    ) -> None:
        row = self._row_for_update(key)
        row["login_attempts_total"] = "0"
        row["login_attempts_last"] = str(last)
        row["is_admin"] = "yes" if is_admin else "no"
        row["login_password"] = login_password
        row["login_method"] = login_method
        row["status"] = "in_progress"
        row["error"] = ""
        self._write()

    def apply_result(self, result: PollResult) -> None:
        row = self._row_for_update(result.key)
        row["repeater_key"] = result.key.lower()
        row["contact_name"] = result.contact_name

        if result.repeater_name is not None:
            row["repeater_name"] = result.repeater_name
        if result.advert_timestamp is not None:
            row["last_advert_utc"] = format_utc(result.advert_timestamp)
        if result.firmware_version is not None:
            row["firmware_version"] = result.firmware_version
        if result.owner_info is not None:
            row["owner_info"] = _clean_owner_info(result.owner_info)

        if result.uptime_seconds is not None:
            row["uptime"] = format_uptime(result.uptime_seconds)
            row["tx_airtime_pct"] = format_percent(
                result.tx_airtime_seconds, result.uptime_seconds
            )
            row["rx_airtime_pct"] = format_percent(
                result.rx_airtime_seconds, result.uptime_seconds
            )

        if result.clock_status is not None:
            row["clock_status"] = result.clock_status
            if result.clock_status == "unknown":
                row["login_time_utc"] = ""
                row["clock_skew_sec"] = ""
            else:
                row["login_time_utc"] = format_utc(result.login_timestamp)
                row["clock_skew_sec"] = format_clock_skew(result.clock_skew_seconds)

        if result.neighbours_count is not None:
            row["neighbours_count"] = str(result.neighbours_count)
        if result.noise_floor_dbm is not None:
            row["noise_floor_dbm"] = str(result.noise_floor_dbm)
        if result.debug_flags is not None:
            row["debug_flags"] = str(result.debug_flags)

        if result.is_admin is not None:
            row["is_admin"] = "yes" if result.is_admin else "no"
        if result.login_password is not None:
            row["login_password"] = result.login_password
        if result.login_method is not None:
            row["login_method"] = result.login_method

        row["login_attempts_last"] = str(result.login_attempts_last)
        row["login_attempts_total"] = str(result.login_attempts_total)
        row["status"] = result.status
        row["error"] = result.error or ""
        self._write()

    def _row_for_update(self, key: str) -> dict[str, str]:
        key = key.lower()
        row = self.rows.get(key)
        if row is None:
            row = {field: "" for field in FIELDS}
            row["repeater_key"] = key
            self.rows[key] = row
        return row

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(self.path.name + ".tmp")
        with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=FIELDS,
                delimiter=";",
                lineterminator="\r\n",
                quoting=csv.QUOTE_MINIMAL,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(self.rows.values())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.path)
        try:
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is a durability nicety, not a correctness requirement.
            pass
