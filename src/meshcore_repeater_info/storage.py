"""Persistent, Excel-friendly CSV report storage."""

from __future__ import annotations

import csv
import logging
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
    "owner_info",
    "uptime",
    "tx_airtime_pct",
    "rx_airtime_pct",
    "login_time_utc",
    "clock_skew_sec",
    "clock_status",
    "noise_floor_dbm",
    "debug_flags",
    "is_admin",
    "login_password",
    "login_attempts_last",
    "login_attempts_total",
    "status",
    "error",
]

ALIVE_STATUSES = {"ok", "ok_old"}
SKIPPABLE_STATUSES = ALIVE_STATUSES | {"final_fail"}


logger = logging.getLogger(__name__)


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
        self.backup_path = path.with_name(path.name + ".bak")
        self.rows: dict[str, dict[str, str]] = {}

    def _validate_path(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                fieldnames = [name.strip() for name in (reader.fieldnames or [])]
                reader.fieldnames = fieldnames
                if "repeater_key" not in fieldnames:
                    return False
                seen: set[str] = set()
                for raw in reader:
                    if None in raw:
                        return False
                    key = (raw.get("repeater_key") or "").strip().lower()
                    if not key or key in seen:
                        return False
                    seen.add(key)
            return True
        except (OSError, UnicodeError, csv.Error):
            return False

    def _read_rows(self, path: Path) -> dict[str, dict[str, str]]:
        rows: dict[str, dict[str, str]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            reader.fieldnames = [
                name.strip() for name in (reader.fieldnames or [])
            ]
            for raw in reader:
                key = (raw.get("repeater_key") or "").strip().lower()
                if not key:
                    continue
                row = {field: (raw.get(field) or "").strip() for field in FIELDS}
                row["repeater_key"] = key
                rows[key] = row
        return rows

    def load(self) -> None:
        self.rows = {}
        if self.path.exists() and self.path.stat().st_size == 0:
            if self._validate_path(self.backup_path):
                logger.warning(
                    "%s is empty; restoring previous version from %s",
                    self.path,
                    self.backup_path,
                )
                self._atomic_copy(self.backup_path, self.path)
                self.rows = self._read_rows(self.path)
                return
            return  # a brand-new empty report

        if self._validate_path(self.path):
            self.rows = self._read_rows(self.path)
            return

        if self._validate_path(self.backup_path):
            logger.warning(
                "%s is invalid; restoring previous version from %s",
                self.path,
                self.backup_path,
            )
            self._atomic_copy(self.backup_path, self.path)
            self.rows = self._read_rows(self.path)
            return

        if self.path.exists():
            raise CsvFormatError(
                f"{self.path} is not a valid report and no valid backup exists"
            )

    def get(self, key: str) -> dict[str, str] | None:
        return self.rows.get(key.lower())

    def remove(self, key: str) -> bool:
        """Delete a row and persist the report. Returns True if it existed."""
        key = key.lower()
        if key not in self.rows:
            return False
        del self.rows[key]
        self._write()
        return True

    def ensure_row(
        self,
        key: str,
        contact_name: str,
        reset_total: bool,
        reset_value: int = 0,
    ) -> None:
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
            row["login_attempts_total"] = str(reset_value)
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
        row["login_password"] = str(login_password)
        row["status"] = "in_progress"
        row["error"] = ""
        self._write()

    def apply_result(self, result: PollResult) -> None:
        row = self._row_for_update(result.key)
        row["repeater_key"] = result.key.lower()
        row["contact_name"] = str(result.contact_name)

        if result.advert_timestamp is not None:
            row["last_advert_utc"] = format_utc(result.advert_timestamp)
        if result.firmware_version is not None:
            row["firmware_version"] = str(result.firmware_version)
        if result.owner_info is not None:
            row["owner_info"] = _clean_owner_info(str(result.owner_info))

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

        if result.noise_floor_dbm is not None:
            row["noise_floor_dbm"] = str(result.noise_floor_dbm)
        if result.debug_flags is not None:
            row["debug_flags"] = str(result.debug_flags)

        if result.is_admin is not None:
            row["is_admin"] = "yes" if result.is_admin else "no"
        if result.login_password is not None:
            row["login_password"] = str(result.login_password)

        row["login_attempts_last"] = str(result.login_attempts_last)
        row["login_attempts_total"] = str(result.login_attempts_total)
        row["status"] = str(result.status)
        row["error"] = str(result.error or "")
        self._write()

    def _row_for_update(self, key: str) -> dict[str, str]:
        key = key.lower()
        row = self.rows.get(key)
        if row is None:
            row = {field: "" for field in FIELDS}
            row["repeater_key"] = key
            self.rows[key] = row
        return row

    def _atomic_copy(self, source: Path, destination: Path) -> None:
        data = source.read_bytes()
        temp_path = destination.with_name(destination.name + ".tmp")
        try:
            with temp_path.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(self.path.name + ".tmp")
        try:
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

            if not self._validate_path(temp_path):
                raise CsvFormatError(f"New report {temp_path} failed validation")

            # Keep exactly one previous valid version.
            if self._validate_path(self.path):
                self._atomic_copy(self.path, self.backup_path)

            os.replace(temp_path, self.path)
            if not self._validate_path(self.path):
                if self._validate_path(self.backup_path):
                    self._atomic_copy(self.backup_path, self.path)
                raise CsvFormatError(
                    f"Report {self.path} failed validation after writing"
                )
            self._fsync_dir()
        finally:
            temp_path.unlink(missing_ok=True)

    def _fsync_dir(self) -> None:
        try:
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is a durability nicety, not a correctness requirement.
            pass
