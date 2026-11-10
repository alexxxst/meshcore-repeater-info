"""Export the CSV report to a styled XLSX workbook."""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

import xlsxwriter

from .formatting import parse_utc
from .names import is_removed_repeater_name, is_valid_repeater_name
from .storage import CsvStore, FIELDS

HEADER_BG = "#1F4E78"
KEY_BG = "#E7E6E6"
OK_BG = "#C6EFCE"
LIGHT_GREEN = "#E2EFDA"
PARTIAL_BG = "#FFEB9C"
ERROR_BG = "#C00000"
LIGHT_RED = "#FFE4E4"
WHITE = "#FFFFFF"
RED_FONT = "#C00000"
ORANGE_FONT = "#ED7D31"
BORDER_COLOR = "#D9D9D9"

DATE_FIELDS = {"last_advert_utc", "login_time_utc"}
PERCENT_FIELDS = {"tx_airtime_pct", "rx_airtime_pct"}
INT_FIELDS = {
    "clock_skew_sec",
    "neighbours_count",
    "noise_floor_dbm",
    "debug_flags",
    "login_attempts_last",
    "login_attempts_total",
}
WRAP_FIELDS = {"owner_info", "error"}
ERROR_STATUSES = {"login_failed", "timeout", "error", "final_fail"}
STATUS_BG = {"ok": OK_BG, "partial": PARTIAL_BG}


class _Formats:
    def __init__(self, workbook):
        self.workbook = workbook
        self.cache = {}

    def get(
        self,
        *,
        bg: str | None = None,
        font: str | None = None,
        bold: bool = False,
        num_format: str | None = None,
        wrap: bool = False,
        align: str | None = None,
    ):
        key = (bg, font, bold, num_format, wrap, align)
        if key not in self.cache:
            options = {
                "border": 1,
                "border_color": BORDER_COLOR,
                "valign": "top",
            }
            if bg:
                options["bg_color"] = bg
                options["pattern"] = 1
            if font:
                options["font_color"] = font
            if bold:
                options["bold"] = True
            if num_format:
                options["num_format"] = num_format
            if wrap:
                options["text_wrap"] = True
            if align:
                options["align"] = align
            self.cache[key] = self.workbook.add_format(options)
        return self.cache[key]

    def header(self):
        return self.workbook.add_format(
            {
                "bold": True,
                "font_color": WHITE,
                "bg_color": HEADER_BG,
                "pattern": 1,
                "border": 1,
                "border_color": BORDER_COLOR,
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
            }
        )


def _firmware_is_old(value: str) -> bool:
    if not value:
        return False
    if "старая" in value.lower():
        return True
    match = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", value.strip(), re.IGNORECASE)
    if not match:
        return False
    version = tuple(int(part or 0) for part in match.groups())
    return version < (1, 16, 0)


def _date_is_far(value: str, now: int) -> bool:
    timestamp = parse_utc(value)
    return timestamp is not None and abs(timestamp - now) > 10 * 86_400


def _number(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _percent(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _cell_font(field: str, value: str, row: dict[str, str], now: int) -> str | None:
    if field == "contact_name":
        return RED_FONT if value and not is_valid_repeater_name(value) else None
    if field in DATE_FIELDS:
        return RED_FONT if value and _date_is_far(value, now) else None
    if field == "firmware_version":
        return RED_FONT if _firmware_is_old(value) else None
    if field in {"clock_skew_sec", "clock_status"}:
        clock_status = row.get("clock_status", "")
        return RED_FONT if clock_status and clock_status != "ok" else None
    if field == "noise_floor_dbm":
        return ORANGE_FONT if _number(value) == -120 else None
    if field == "debug_flags":
        number = _number(value)
        return ORANGE_FONT if number not in (None, 0) else None
    return None


def _write_value(worksheet, row: int, col: int, field: str, value: str, fmt) -> None:
    if field in DATE_FIELDS and value:
        try:
            worksheet.write_datetime(
                row,
                col,
                datetime.strptime(value, "%Y-%m-%d %H:%M:%S"),
                fmt,
            )
            return
        except ValueError:
            pass
    if field in PERCENT_FIELDS and value:
        number = _percent(value)
        if number is not None:
            worksheet.write_number(row, col, number / 100.0, fmt)
            return
    if field in INT_FIELDS and value:
        number = _number(value)
        if number is not None:
            worksheet.write_number(row, col, number, fmt)
            return
    if value:
        worksheet.write_string(row, col, str(value), fmt)
    else:
        worksheet.write_blank(row, col, None, fmt)


def _column_width(field: str, values: list[str]) -> float:
    longest = len(field)
    for value in values:
        if field in DATE_FIELDS and value:
            longest = max(longest, 19)
        else:
            longest = max(longest, len(str(value)))
    if field == "repeater_key":
        return float(max(66, longest + 2))
    return float(min(max(10, longest + 2), 60))


def export_csv_to_xlsx(
    csv_path: Path, xlsx_path: Path, now: int | None = None
) -> None:
    """Read a validated CSV report and write a styled XLSX workbook."""
    csv_path = Path(csv_path)
    xlsx_path = Path(xlsx_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    store = CsvStore(csv_path)
    store.load()
    rows = [
        row
        for row in store.rows.values()
        if not is_removed_repeater_name(row.get("contact_name", ""))
    ]
    now = int(time.time()) if now is None else int(now)

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(str(xlsx_path))
    worksheet = workbook.add_worksheet("repeaters")
    formats = _Formats(workbook)

    worksheet.write_row(0, 0, FIELDS, formats.header())
    worksheet.set_row(0, 30)

    for row_index, row in enumerate(rows, start=1):
        status = row.get("status", "")
        has_data = any(
            row.get(field)
            for field in (
                "firmware_version",
                "uptime",
                "neighbours_count",
                "noise_floor_dbm",
                "debug_flags",
            )
        )
        error_row = status in ERROR_STATUSES or not status or not has_data
        has_red_cell = any(
            _cell_font(field, row.get(field, "") or "", row, now) == RED_FONT
            for field in FIELDS
        )
        green_row = (
            status == "ok"
            and not (row.get("error") or "")
            and not error_row
            and not has_red_cell
        )
        if error_row:
            row_bg = LIGHT_RED
        elif green_row:
            row_bg = LIGHT_GREEN
        else:
            row_bg = None

        for col, field in enumerate(FIELDS):
            value = row.get(field, "") or ""
            bg = row_bg
            font = _cell_font(field, value, row, now)
            bold = False
            num_format = None
            wrap = field in WRAP_FIELDS
            align = None

            if col == 0:
                bg = KEY_BG
                bold = True
                num_format = "@"
            elif field == "status":
                if status in STATUS_BG:
                    bg = STATUS_BG[status]
                elif status in ERROR_STATUSES:
                    bg = ERROR_BG
                    font = WHITE
                    bold = True
                align = "center"
            elif field in DATE_FIELDS:
                num_format = "yyyy-mm-dd hh:mm:ss"
            elif field in PERCENT_FIELDS:
                num_format = "0.00%"
            elif field in INT_FIELDS:
                num_format = "0"
            else:
                num_format = "@"

            fmt = formats.get(
                bg=bg,
                font=font,
                bold=bold,
                num_format=num_format,
                wrap=wrap,
                align=align,
            )
            _write_value(worksheet, row_index, col, field, value, fmt)

    for col, field in enumerate(FIELDS):
        worksheet.set_column(
            col, col, _column_width(field, [row.get(field, "") or "" for row in rows])
        )

    if rows:
        worksheet.autofilter(0, 0, len(rows), len(FIELDS) - 1)
    worksheet.freeze_panes(1, 2)
    workbook.close()
