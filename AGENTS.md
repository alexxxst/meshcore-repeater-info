# AGENTS.md — handoff / project context

This file is the durable memory for a fresh session. Read it first, then
`README.md`, `docs/protocol.md`, and the tests.

## What this project is

CLI tool that polls every MeshCore **repeater** from a MeshCore Companion
contact list and writes an Excel-friendly CSV report. It works over the
Companion serial/USB connection today; BLE/TCP were discussed but are **not
implemented**.

Repository: https://github.com/alexxxst/meshcore-repeater-info (public)

## Quick commands

```bash
uv sync
uv run pytest
uv run meshcore-repeater-info --list-ports
uv run meshcore-repeater-info -p /dev/cu.usbmodem1101 -o repeaters.csv
uv run meshcore-repeater-info -p /dev/cu.usbmodem1101 --alive
uv run meshcore-repeater-info -p /dev/cu.usbmodem1101 --dead
uv run meshcore-repeater-info -p /dev/cu.usbmodem1101 --broken
uv run meshcore-repeater-info -p /dev/cu.usbmodem1101 --force
uv run meshcore-repeater-info -p /dev/cu.usbmodem1101 --loop
uv run meshcore-repeater-info --input repeaters.csv --export-xlsx repeaters.xlsx
```

When testing on real hardware, the user's rule is: **do not poll more than
one repeater at a time** unless they explicitly ask for a full run.

## Architecture

```
src/meshcore_repeater_info/
  cli.py        argparse, inventory orchestration, retry modes, summary
  config.py     CLI config
  mesh.py       MeshCoreSession, login, binary requests, flood/path tracking
  poller.py     per-repeater login policy + post-login request retries
  storage.py    CSV schema, atomic writes, resume/retry selection
  model.py      PollResult
  formatting.py uptime, percentages, UTC, clock skew
  ports.py      serial port discovery
```

The poller and CSV layers are transport-agnostic. `MeshCoreSession` is the
only serial-specific part; adding BLE/TCP would mostly mean a connection
factory plus CLI/config/docs (see `meshcore.MeshCore.create_ble/create_tcp`).

## Current behavior

- Contacts with `type == 2` (`ADV_TYPE_REPEATER`) are polled.
- If `contact_name` contains `remove` or `delete` (case-insensitive), the
  repeater is skipped and its existing CSV row is deleted. XLSX export also
  filters such rows out.
- Login attempts: 3 × empty password, then 2 × each of
  `123456`, `guest`, `password`, `1234`, `1234567890`.
  Stop on the first success. **Admin is never hunted deliberately**; if a
  password happens to give admin, use it. A saved `login_password=admin`
  from an existing report is still tried first.
- Empty-password success is labelled `login_method=empty` internally in logs.
  This can mean either an ACL match or an empty guest password; firmware does
  not distinguish them. `login_method` is not a CSV column.
- Login packets use `--timeout`, default **15 s**. Requests after login use
  `--request-timeout`, default **25 s**.
- Every post-login request is attempted up to **5 times**
  (`--request-attempts`, minimum 5). These retries do **not** increment the
  login counter.
- Random pause 20–30 s before every login attempt and every request attempt.
- Login counter: cumulative **failed** login attempts since the last success
  or `--force`. When it reaches 35, `status=final_fail`; no further polling
  until `--force`/`--dead`.
- No logout is sent.

## Flood / path state machine

Do not call this mode “direct”: literal direct is zero-hop, and the tool does
not force zero-hop. Use **path** for “send using the companion's stored path”.

- Every repeater starts in `initial_flood` (`reset_path` → send flooded).
- If a flood request succeeds and the companion reports `PATH_UPDATE`, verify
  the route with the next request. If that path request succeeds, mode becomes
  `path`.
- If a path request fails, switch to `flood` for the rest of that repeater's
  poll.
- Prefix flood attempts with `reset_path`; answers can re-learn a path.
- A zero-hop path is allowed only if the path itself was learned that way; it
  is never forced.

## Data sources

- `GET_OWNER_INFO` (`0x07`) after login: firmware version, repeater name, owner
  info. Guest-accessible on simple repeater **v1.12.0+**
  (`fw_ver_level >= 2`).
- `STATUS` (`0x01`): uptime, TX/RX airtime, noise floor, debug flags
  (`err_events`, named `full_evts` in `meshcore_py`).
- Admin-only fallback if `GET_OWNER_INFO` fails: remote CLI `ver`,
  `get name`, `get owner.info`.
- `ANON_REQ_TYPE_OWNER` was removed: repeater answers it only via a direct
  route, so it is incompatible with flood fallback.
- Old firmware: version is marked `старая (FW < 1.12)` or
  `старая (версия недоступна)`, status `ok_old` when other supported data is
  complete.

## CSV

Written as `utf-8-sig` (BOM), delimiter `;`, CRLF, comma decimal separator.
Atomic rewrite after every repeater (`temp` → `fsync` → `os.replace`), so
interruption does not corrupt the report.

Column order:

```
repeater_key;contact_name;last_advert_utc;firmware_version;owner_info;
uptime;tx_airtime_pct;rx_airtime_pct;login_time_utc;
clock_skew_sec;clock_status;noise_floor_dbm;
debug_flags;is_admin;login_password;login_attempts_last;
login_attempts_total;status;error
```

- `contact_name` is the companion contact's `adv_name`.
- `last_advert_utc` is the contact's `last_advert` timestamp formatted UTC.
- `clock_skew_sec = repeater login time − our time`; `clock_status=suspect`
  when `abs(skew) > 600`.
- `status`: `ok`, `ok_old`, `partial`, `login_failed`, `timeout`, `error`,
  `final_fail`, `in_progress`.
- `login_password` is stored in **plain text** (dictionary-style candidates).
  Do not commit a real report.

## Retry modes

- default: poll new contacts; skip `ok`/`ok_old`/`final_fail`; retry
  `partial`/`login_failed`/`timeout`/`error`/`in_progress`.
- `--alive`: poll only rows with `status=ok` or `ok_old`.
- `--dead`: poll only rows without complete info (`partial`,
  `login_failed`, `timeout`, `error`, `final_fail`, `in_progress`) and new
  contacts; reset login counter and `final_fail`.
- `--broken`: same as `--dead`, but only for **existing rows**; new contacts
  are not added. Useful for retrying only known broken/partial records.
- `--force`: poll everything, reset counters and `final_fail`.
- `--alive`, `--dead`, `--broken`, `--force` are mutually exclusive.
- `--limit N` counts actual polls after skips.

## Loop mode

- `--loop` repeats full passes until Ctrl+C; `--loop-interval` defaults to 60 s.
- Each pass re-fetches contacts, so new adverts/repeaters are picked up.
- If `contact.last_advert` is more than 24 hours newer than the row's
  `last_advert_utc`, `login_attempts_total` is reset and the repeater is polled
  again with its known password/ACL. A `final_fail` row is reset to **25**
  (10 attempts remain), not 0. In explicit `--alive`/`--broken` modes only
  rows selected by that mode are re-polled; in default loop mode a fresh
  advert can promote an `ok`/`final_fail` row.
- On connection/pass failure the loop logs, closes, waits, and reconnects.

## Backups and validation

- Every write validates the temp CSV, keeps the previous valid version in
  `repeaters.csv.bak`, atomically replaces the main file and validates it again.
- On load, if the main file is corrupt/empty and `.bak` is valid, the backup is
  restored. Duplicate keys / missing `repeater_key` are invalid.
- `owner_info` and `login_password` are always stored as strings (empty string
  when absent).

## XLSX export

- `--input CSV --export-xlsx XLSX` is an export-only mode; no radio connection.
- Styling: dark header, gray/bold key column, green `ok` status, yellow
  `partial`, red error statuses with white text, very light red error rows.
- A whole row is light green only when `status == "ok"`, `error` is empty and
  no cell is red. Orange noise/debug cells do not block the green row.
- Red text: old dates (>10 days from now), old firmware (<1.16 or `старая`),
  `clock_status != ok`, invalid `contact_name`.
- Orange text: `noise_floor_dbm == -120`, `debug_flags != 0`.
- Auto column widths, date/percent/number formats, autofilter.
- Frozen panes: `C2` — header plus the first two columns.
- Percentages are written as real numeric fractions (`0.1234`) with the
  `0.00%` format, so the CSV comma decimal separator does not leak into XLSX.
- Name validation: city 2–4 blocks, region `LO` + 3-char code 3–5 blocks; ASCII
  letters/digits/`-`/`_` plus `☀`/`☀️`.

## Repository hygiene

- `repeaters.csv`, `*.csv`, logs, `.venv`, `.idea`, caches are in
  `.gitignore`; never commit real reports or passwords.
- Entry point: `meshcore-repeater-info = "meshcore_repeater_info:main"`.
- `src/main.py` was a PyCharm sample and was removed.
- `meshcore` is pinned `>=2.3.17,<3`; `cffi` is an explicit dependency to avoid
  a broken namespace-package shadowing on macOS.

## Testing quirks

- Unit tests use fake sessions; no radio is needed.
- Real-hardware tests must stay at one repeater per command unless the user
  explicitly requests a full run.
- A background process started with `&` from a non-interactive shell may
  inherit `SIGINT` ignored, so `kill -INT` will not stop it; use a foreground
  run/PTY or `SIGTERM` for test harnesses.
- `--force` on a real repeater can take a long time because of 15/25 s
  timeouts, 20–30 s pauses and 5 request attempts.
