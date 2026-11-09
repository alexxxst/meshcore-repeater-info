from datetime import datetime

import pytest
from openpyxl import load_workbook

from meshcore_repeater_info.export_xlsx import _firmware_is_old, export_csv_to_xlsx
from meshcore_repeater_info.model import PollResult
from meshcore_repeater_info.storage import CsvStore


NOW = 1_700_000_000
OLD = NOW - 11 * 86_400


def make_csv(path):
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="PDO-Strelna-NE",
            repeater_name="PDO-Strelna-NE",
            firmware_version="v1.18.0",
            uptime_seconds=100,
            tx_airtime_seconds=10,
            rx_airtime_seconds=5,
            advert_timestamp=NOW,
            login_timestamp=NOW,
            clock_skew_seconds=10,
            clock_status="ok",
            neighbours_count=3,
            noise_floor_dbm=-110,
            debug_flags=0,
            is_admin=False,
            login_password="(empty)",
            login_method="empty",
            login_attempts_last=1,
            login_attempts_total=0,
            status="ok",
        )
    )
    store.apply_result(
        PollResult(
            key="b" * 64,
            contact_name="NEV-Solidar-360☀️",
            repeater_name="LO-LOM-BULP",
            firmware_version="v1.15.0",
            uptime_seconds=100,
            tx_airtime_seconds=10,
            rx_airtime_seconds=5,
            advert_timestamp=OLD,
            login_timestamp=OLD,
            clock_skew_seconds=700,
            clock_status="suspect",
            neighbours_count=3,
            noise_floor_dbm=-120,
            debug_flags=2,
            is_admin=False,
            login_password="guest",
            login_method="password",
            login_attempts_last=1,
            login_attempts_total=0,
            status="partial",
            error="missing: neighbours",
        )
    )
    store.apply_result(
        PollResult(
            key="c" * 64,
            contact_name="Bad Name!",
            repeater_name="Parnas",
            login_attempts_last=15,
            login_attempts_total=15,
            status="timeout",
            error="15 login attempts failed",
        )
    )


def color(cell):
    value = cell.fill.fgColor.rgb
    return str(value).upper() if value else ""


def font_color(cell):
    value = cell.font.color.rgb if cell.font.color else None
    return str(value).upper() if value else ""


def test_export_xlsx_styles_and_formats(tmp_path):
    csv_path = tmp_path / "report.csv"
    xlsx_path = tmp_path / "report.xlsx"
    make_csv(csv_path)

    export_csv_to_xlsx(csv_path, xlsx_path, now=NOW)

    workbook = load_workbook(xlsx_path)
    sheet = workbook.active
    header = [cell.value for cell in sheet[1]]
    col = {name: index + 1 for index, name in enumerate(header)}

    assert header[0] == "repeater_key"
    assert header[1] == "contact_name"
    assert sheet.freeze_panes == "B2"
    assert sheet.auto_filter.ref is not None
    assert sheet.column_dimensions["A"].width > 20

    # Header and first column are visually distinct.
    assert color(sheet["A1"]).endswith("1F4E78")
    assert sheet["A1"].font.bold is True
    assert color(sheet.cell(2, 1)).endswith("E7E6E6")
    assert sheet.cell(2, 1).font.bold is True

    # ok row: green status.
    assert color(sheet.cell(2, col["status"])).endswith("C6EFCE")

    # partial row: yellow status, old version/date/clock red, noise/debug orange.
    assert color(sheet.cell(3, col["status"])).endswith("FFEB9C")
    assert font_color(sheet.cell(3, col["firmware_version"])).endswith("C00000")
    assert font_color(sheet.cell(3, col["last_advert_utc"])).endswith("C00000")
    assert font_color(sheet.cell(3, col["login_time_utc"])).endswith("C00000")
    assert font_color(sheet.cell(3, col["clock_skew_sec"])).endswith("C00000")
    assert font_color(sheet.cell(3, col["clock_status"])).endswith("C00000")
    assert font_color(sheet.cell(3, col["noise_floor_dbm"])).endswith("ED7D31")
    assert font_color(sheet.cell(3, col["debug_flags"])).endswith("ED7D31")

    # timeout row: light red row, red status with white text.
    assert color(sheet.cell(4, col["contact_name"])).endswith("FFE4E4")
    assert color(sheet.cell(4, col["status"])).endswith("C00000")
    assert font_color(sheet.cell(4, col["status"])).endswith("FFFFFF")

    # invalid names are red text.
    assert font_color(sheet.cell(4, col["contact_name"])).endswith("C00000")
    assert font_color(sheet.cell(4, col["repeater_name"])).endswith("C00000")

    # cell formats
    date_cell = sheet.cell(2, col["last_advert_utc"])
    assert isinstance(date_cell.value, datetime)
    assert "yyyy-mm-dd" in date_cell.number_format
    percent_cell = sheet.cell(2, col["tx_airtime_pct"])
    assert abs(percent_cell.value - 0.1) < 1e-9
    assert percent_cell.number_format == "0.00%"
    assert sheet.cell(2, col["neighbours_count"]).number_format == "0"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v1.15.0", True),
        ("v1.15.9-deadbeef", True),
        ("v1.16.0", False),
        ("v1.17.1-pr2343", False),
        ("v1.18.0-81dbae5", False),
        ("старая (FW < 1.12)", True),
    ],
)
def test_firmware_is_old_threshold(version, expected):
    assert _firmware_is_old(version) is expected
