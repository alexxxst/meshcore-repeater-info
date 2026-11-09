import csv
from pathlib import Path

import pytest

from meshcore_repeater_info.model import PollResult
from meshcore_repeater_info.storage import (
    CsvFormatError,
    CsvStore,
    should_process,
    should_skip,
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def test_new_file_is_excel_friendly_and_has_expected_order(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()

    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="Contact Name",
            repeater_name="Repeater Name",
            advert_timestamp=1_700_000_000,
            firmware_version="v1.18.0",
            owner_info="Admin: @foo\nPhone: 123",
            uptime_seconds=90_061,
            tx_airtime_seconds=45_030,
            rx_airtime_seconds=9_006,
            login_timestamp=1_700_000_000,
            clock_skew_seconds=42,
            clock_status="ok",
            neighbours_count=12,
            noise_floor_dbm=-115,
            debug_flags=2,
            is_admin=True,
            login_password="admin",
            login_method="password",
            login_attempts_last=1,
            login_attempts_total=0,
            status="ok",
            error=None,
        )
    )

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    header = raw.decode("utf-8-sig").splitlines()[0]
    assert header.split(";")[:6] == [
        "repeater_key",
        "contact_name",
        "last_advert_utc",
        "firmware_version",
        "repeater_name",
        "owner_info",
    ]

    row = read_rows(path)[0]
    assert row["repeater_name"] == "Repeater Name"
    assert row["last_advert_utc"] == "2023-11-14 22:13:20"
    assert row["contact_name"] == "Contact Name"
    assert row["owner_info"] == "Admin: @foo | Phone: 123"
    assert row["uptime"] == "1 дн 1 ч 1 м"
    assert row["tx_airtime_pct"] == "50,00"
    assert row["rx_airtime_pct"] == "10,00"
    assert row["login_time_utc"] == "2023-11-14 22:13:20"
    assert row["clock_skew_sec"] == "42"
    assert row["clock_status"] == "ok"
    assert row["is_admin"] == "yes"
    assert row["login_password"] == "admin"
    assert row["login_method"] == "password"


def test_failed_poll_preserves_previous_measurements(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="k" * 64,
            contact_name="R",
            repeater_name="Repeater",
            advert_timestamp=1_700_000_000,
            firmware_version="v1.18.0",
            uptime_seconds=100,
            tx_airtime_seconds=10,
            rx_airtime_seconds=5,
            login_timestamp=1_700_000_000,
            clock_skew_seconds=1,
            clock_status="ok",
            neighbours_count=3,
            noise_floor_dbm=-110,
            debug_flags=1,
            is_admin=False,
            login_password="guest",
            login_method="password",
            login_attempts_last=1,
            login_attempts_total=0,
            status="ok",
        )
    )

    store.apply_result(
        PollResult(
            key="k" * 64,
            contact_name="R",
            login_attempts_last=15,
            login_attempts_total=15,
            status="login_failed",
            error="all login attempts failed",
        )
    )

    row = read_rows(path)[0]
    assert row["repeater_name"] == "Repeater"
    assert row["last_advert_utc"] == "2023-11-14 22:13:20"
    assert row["firmware_version"] == "v1.18.0"
    assert row["uptime"] == "1 м"
    assert row["tx_airtime_pct"] == "10,00"
    assert row["rx_airtime_pct"] == "5,00"
    assert row["login_time_utc"] == "2023-11-14 22:13:20"
    assert row["clock_status"] == "ok"
    assert row["neighbours_count"] == "3"
    assert row["noise_floor_dbm"] == "-110"
    assert row["debug_flags"] == "1"
    assert row["is_admin"] == "no"
    assert row["login_password"] == "guest"
    assert row["login_method"] == "password"
    assert row["status"] == "login_failed"
    assert row["login_attempts_last"] == "15"
    assert row["login_attempts_total"] == "15"


def test_unknown_clock_clears_previous_time(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="c" * 64,
            contact_name="R",
            login_timestamp=1_700_000_000,
            clock_skew_seconds=1,
            clock_status="ok",
            status="ok",
        )
    )
    store.apply_result(
        PollResult(
            key="c" * 64,
            contact_name="R",
            clock_status="unknown",
            status="ok",
        )
    )

    row = read_rows(path)[0]
    assert row["login_time_utc"] == ""
    assert row["clock_skew_sec"] == ""
    assert row["clock_status"] == "unknown"


def test_force_resets_attempt_counter(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.ensure_row("f" * 64, "R", reset_total=True)
    store.record_attempt("f" * 64, total=35, last=15)

    store.ensure_row("f" * 64, "R", reset_total=True)

    row = store.get("f" * 64)
    assert row["login_attempts_total"] == "0"
    assert row["login_attempts_last"] == "0"
    assert row["status"] == "in_progress"


def test_skip_rules():
    assert should_skip({"status": "ok"}, force=False)
    assert should_skip({"status": "ok_old"}, force=False)
    assert should_skip({"status": "final_fail"}, force=False)
    assert not should_skip({"status": "partial"}, force=False)
    assert not should_skip({"status": "login_failed"}, force=False)
    assert not should_skip({"status": "ok"}, force=True)


@pytest.mark.parametrize(
    ("status", "mode", "expected"),
    [
        ("ok", "default", False),
        ("ok_old", "default", False),
        ("final_fail", "default", False),
        ("partial", "default", True),
        ("timeout", "default", True),
        (None, "default", True),
        ("ok", "alive", True),
        ("ok_old", "alive", True),
        ("partial", "alive", False),
        ("timeout", "alive", False),
        (None, "alive", False),
        ("ok", "dead", False),
        ("ok_old", "dead", False),
        ("partial", "dead", True),
        ("timeout", "dead", True),
        ("final_fail", "dead", True),
        (None, "dead", True),
        ("ok", "broken", False),
        ("ok_old", "broken", False),
        ("partial", "broken", True),
        ("timeout", "broken", True),
        ("final_fail", "broken", True),
        (None, "broken", False),
        ("ok", "force", True),
        ("timeout", "force", True),
        (None, "force", True),
    ],
)
def test_should_process_modes(status, mode, expected):
    row = None if status is None else {"status": status}
    assert should_process(row, mode) is expected


def test_write_keeps_previous_version_in_backup(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()

    store.apply_result(
        PollResult(key="a" * 64, contact_name="Alpha", status="ok")
    )
    first_version = path.read_bytes()

    store.apply_result(
        PollResult(key="b" * 64, contact_name="Beta", status="ok")
    )

    assert store.backup_path.exists()
    assert store.backup_path.read_bytes() == first_version
    assert path.read_bytes() != first_version


def test_load_recovers_from_backup_when_main_is_corrupt(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(key="a" * 64, contact_name="Alpha", status="ok")
    )
    store.apply_result(
        PollResult(key="b" * 64, contact_name="Beta", status="ok")
    )

    path.write_bytes(b"this is not a csv")

    recovered = CsvStore(path)
    recovered.load()

    assert recovered.get("a" * 64) is not None
    assert recovered.get("b" * 64) is None
    assert path.read_bytes() == recovered.backup_path.read_bytes()


def test_load_rejects_corrupt_main_and_backup(tmp_path):
    path = tmp_path / "report.csv"
    backup = path.with_name(path.name + ".bak")
    path.write_bytes(b"broken")
    backup.write_bytes(b"also broken")

    with pytest.raises(CsvFormatError):
        CsvStore(path).load()


def test_all_stored_values_are_strings(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.ensure_row("a" * 64, "Alpha", reset_total=False)

    row = store.get("a" * 64)
    assert all(isinstance(value, str) for value in row.values())
    assert row["owner_info"] == ""
    assert row["login_password"] == ""


def test_load_accepts_bom_and_missing_columns(tmp_path):
    path = tmp_path / "old.csv"
    path.write_bytes(
        (
            "\ufeffrepeater_key;contact_name;status\r\n"
            + "a" * 64
            + ";Old Name;ok\r\n"
        ).encode("utf-8")
    )

    store = CsvStore(path)
    store.load()

    row = store.get("a" * 64)
    assert row["contact_name"] == "Old Name"
    assert row["status"] == "ok"
    assert row["repeater_name"] == ""


def test_load_migrates_legacy_acl_login_method(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text(
        "repeater_key;login_method;status\r\n" + "a" * 64 + ";acl;ok\r\n",
        encoding="utf-8",
    )

    store = CsvStore(path)
    store.load()

    assert store.get("a" * 64)["login_method"] == "empty"


def test_header_without_key_is_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("name;status\r\nx;ok\r\n", encoding="utf-8")

    store = CsvStore(path)
    with pytest.raises(CsvFormatError):
        store.load()
