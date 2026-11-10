"""Command-line interface for the repeater inventory tool."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .formatting import format_utc, parse_utc
from .mesh import MeshCoreSession
from .model import PollResult
from .names import is_removed_repeater_name
from .poller import PollConfig, Poller
from .ports import PortError, discover_ports, format_ports, select_port
from .storage import CsvFormatError, CsvStore, should_process

logger = logging.getLogger(__name__)

DEFAULT_PASSWORDS = ",123456,guest,password,1234,1234567890"


def _parse_passwords(value: str) -> tuple[str, ...]:
    return tuple(value.split(","))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meshcore-repeater-info",
        description=(
            "Poll every repeater in the MeshCore Companion contact list and "
            "write an Excel-friendly CSV report."
        ),
    )
    parser.add_argument("-p", "--port", help="Companion serial port")
    parser.add_argument("-b", "--baudrate", type=int, default=115_200)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("repeaters.csv"),
        help="CSV report path (default: repeaters.csv)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="CSV file to export from (defaults to --output)",
    )
    parser.add_argument(
        "--export-xlsx",
        type=Path,
        help="export the CSV to a styled XLSX file and exit",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="repeat the full inventory forever until Ctrl+C",
    )
    parser.add_argument(
        "--loop-interval",
        type=float,
        default=60.0,
        help="seconds to wait between loop passes (default: 60)",
    )
    retry_group = parser.add_mutually_exclusive_group()
    retry_group.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="re-poll every repeater, reset counters and final_fail",
    )
    retry_group.add_argument(
        "--alive",
        action="store_true",
        help="re-poll only repeaters with complete info (ok/ok_old)",
    )
    retry_group.add_argument(
        "--dead",
        action="store_true",
        help="re-poll only repeaters without complete info; resets final_fail",
    )
    retry_group.add_argument(
        "--broken",
        action="store_true",
        help="re-poll only existing rows with partial/broken statuses; resets final_fail",
    )
    parser.add_argument("--only", help="only one repeater by key prefix or name")
    parser.add_argument("--limit", type=int, help="maximum repeaters to poll")
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="timeout for every login packet (default: 15)",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=25.0,
        help="timeout for requests after login (default: 25)",
    )
    parser.add_argument("--min-delay", type=float, default=20.0)
    parser.add_argument("--max-delay", type=float, default=30.0)
    parser.add_argument("--clock-skew-threshold", type=int, default=600)
    parser.add_argument("--max-login-attempts", type=int, default=35)
    parser.add_argument(
        "--request-attempts",
        type=int,
        default=5,
        help="attempts per request after login (minimum 5)",
    )
    parser.add_argument(
        "--passwords",
        type=_parse_passwords,
        default=_parse_passwords(DEFAULT_PASSWORDS),
        help="comma-separated login candidates; empty first entry means blank password",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--list-ports", action="store_true")
    return parser


def build_config(argv: list[str] | None = None) -> Config:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.min_delay < 0:
        parser.error("--min-delay must be >= 0")
    if args.max_delay < args.min_delay:
        parser.error("--max-delay must be >= --min-delay")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be > 0")
    if args.loop_interval <= 0:
        parser.error("--loop-interval must be > 0")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.max_login_attempts < 1:
        parser.error("--max-login-attempts must be >= 1")
    if args.request_attempts < 5:
        parser.error("--request-attempts must be >= 5")
    if args.clock_skew_threshold < 0:
        parser.error("--clock-skew-threshold must be >= 0")

    return Config(
        port=args.port,
        baudrate=args.baudrate,
        output=args.output,
        input=args.input,
        export_xlsx=args.export_xlsx,
        loop=args.loop,
        loop_interval=args.loop_interval,
        force=args.force,
        alive=args.alive,
        dead=args.dead,
        broken=args.broken,
        only=args.only,
        limit=args.limit,
        timeout=args.timeout,
        request_timeout=args.request_timeout,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        clock_skew_threshold=args.clock_skew_threshold,
        max_login_attempts=args.max_login_attempts,
        request_attempts=args.request_attempts,
        passwords=args.passwords,
        debug=args.debug,
        list_ports=args.list_ports,
    )


def select_contacts(contacts: list[dict], only: str | None) -> list[dict]:
    if only is None:
        return list(contacts)

    needle = only.lower()
    matches = []
    for contact in contacts:
        key = contact.get("public_key", "").lower()
        name = contact.get("adv_name", "").lower()
        if key.startswith(needle) or name == needle or needle in name:
            matches.append(contact)

    if not matches:
        raise ValueError(f"No repeater matches {only!r}")
    if len(matches) > 1:
        names = ", ".join(contact.get("adv_name", "?") for contact in matches)
        raise ValueError(f"Ambiguous --only {only!r}: {names}")
    return matches


@dataclass(slots=True)
class RunSummary:
    polled: int = 0
    skipped: int = 0
    statuses: Counter = field(default_factory=Counter)


def _log_poll_result(result: PollResult) -> None:
    short_key = result.key[:12]
    if result.status in {"ok", "ok_old"}:
        logger.info(
            "%s (%s): status=%s admin=%s password=%s clock=%s attempts=%s",
            result.contact_name,
            short_key,
            result.status,
            "yes" if result.is_admin else "no",
            result.login_password or "-",
            result.clock_status or "?",
            result.login_attempts_last,
        )
    else:
        logger.warning(
            "%s (%s): status=%s attempts=%s total=%s error=%s",
            result.contact_name,
            short_key,
            result.status,
            result.login_attempts_last,
            result.login_attempts_total,
            result.error or "-",
        )


ADVERT_REFRESH_SECONDS = 86_400
FRESH_FINAL_FAIL_RESET = 25


def _advert_is_newer(contact: dict, row: dict[str, str]) -> bool:
    contact_ts = contact.get("last_advert")
    if not isinstance(contact_ts, (int, float)) or contact_ts <= 0:
        return False
    row_ts = parse_utc(row.get("last_advert_utc"))
    return row_ts is None or int(contact_ts) > row_ts + ADVERT_REFRESH_SECONDS


async def run_inventory(
    config: Config,
    session,
    store: CsvStore,
    poller: Poller | None = None,
) -> RunSummary:
    contacts = await session.get_repeater_contacts()
    selected = select_contacts(contacts, config.only)
    if poller is None:
        poller = Poller(
            session=session,
            config=PollConfig(
                timeout=config.timeout,
                request_timeout=config.request_timeout,
                min_delay=config.min_delay,
                max_delay=config.max_delay,
                clock_skew_threshold=config.clock_skew_threshold,
                max_login_attempts=config.max_login_attempts,
                request_attempts=config.request_attempts,
                passwords=config.passwords,
            ),
            tracker=store,
        )

    if config.force:
        mode = "force"
    elif config.alive:
        mode = "alive"
    elif config.dead:
        mode = "dead"
    elif config.broken:
        mode = "broken"
    else:
        mode = "default"

    summary = RunSummary()
    for contact in selected:
        key = contact["public_key"].lower()
        contact_name = contact.get("adv_name", "")
        if is_removed_repeater_name(contact_name):
            removed = store.remove(key)
            summary.skipped += 1
            logger.warning(
                "skip %s (%s): name contains remove/delete%s",
                contact_name,
                key[:12],
                "; row deleted" if removed else "",
            )
            continue
        existing = store.get(key)
        process = should_process(existing, mode)
        fresh_advert = bool(
            config.loop and existing is not None and _advert_is_newer(contact, existing)
        )
        # In the default loop mode a fresh advert can promote an otherwise
        # skipped ok/final_fail row. In explicit alive/broken/dead modes the
        # selected mode still wins.
        if mode == "default" and fresh_advert:
            process = True

        if not process:
            summary.skipped += 1
            status = existing.get("status", "") if existing else ""
            if mode in {"alive", "broken"}:
                logger.info(
                    "skip %s (%s): not %s (%s)",
                    contact_name,
                    key[:12],
                    mode,
                    status or "no row",
                )
            elif status == "final_fail" and mode != "dead":
                logger.warning(
                    "skip %s (%s): final_fail after %s attempts; use --dead or --force",
                    contact_name,
                    key[:12],
                    existing.get("login_attempts_total") or "?",
                )
            else:
                logger.info(
                    "skip %s (%s): already complete (%s)",
                    contact_name,
                    key[:12],
                    status,
                )
            continue

        if config.limit is not None and summary.polled >= config.limit:
            break

        if fresh_advert:
            logger.info(
                "%s: advert is over a day newer (%s -> %s), resetting attempts and re-polling",
                contact_name,
                existing.get("last_advert_utc") or "?",
                format_utc(int(contact["last_advert"])),
            )

        if config.force:
            reset_total, reset_value = True, 0
        elif fresh_advert:
            reset_total = True
            reset_value = (
                FRESH_FINAL_FAIL_RESET
                if existing.get("status") == "final_fail"
                else 0
            )
        elif config.dead or config.broken:
            reset_total, reset_value = True, 0
        else:
            reset_total, reset_value = False, 0

        store.ensure_row(
            key,
            contact_name,
            reset_total=reset_total,
            reset_value=reset_value,
        )
        existing = store.get(key) or {}
        try:
            result = await poller.poll(contact, existing)
        except Exception as exc:  # keep the report usable even if one node misbehaves
            logger.exception("poll failed for %s (%s)", contact_name, key[:12])
            result = PollResult(
                key=key,
                contact_name=contact_name,
                login_attempts_last=0,
                login_attempts_total=int(
                    existing.get("login_attempts_total") or 0
                ),
                status="error",
                error=str(exc),
            )

        store.apply_result(result)
        summary.polled += 1
        summary.statuses[result.status] += 1
        _log_poll_result(result)

    return summary


def _setup_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("meshcore").setLevel(
        logging.DEBUG if debug else logging.WARNING
    )


async def _run_loop(
    config: Config,
    store: CsvStore,
    connect,
    sleep=asyncio.sleep,
    max_passes: int | None = None,
) -> None:
    """Run inventory passes until interrupted (or max_passes in tests)."""
    pass_no = 0
    session = None
    try:
        while max_passes is None or pass_no < max_passes:
            pass_no += 1
            started = time.monotonic()
            try:
                if session is None:
                    logger.info("Connecting to Companion ...")
                    session = await connect()
                summary = await run_inventory(config, session, store)
                elapsed = time.monotonic() - started
                details = ", ".join(
                    f"{status}={count}"
                    for status, count in sorted(summary.statuses.items())
                )
                logger.info(
                    "Pass %s done in %.0f s: polled=%s skipped=%s%s",
                    pass_no,
                    elapsed,
                    summary.polled,
                    summary.skipped,
                    f" ({details})" if details else "",
                )
            except (CsvFormatError, ValueError):
                raise
            except Exception:
                logger.exception("Inventory pass %s failed; will retry", pass_no)
                if session is not None:
                    try:
                        await session.close()
                    except Exception:
                        pass
                    session = None

            if max_passes is not None and pass_no >= max_passes:
                break
            logger.info("Waiting %.0f s until the next pass...", config.loop_interval)
            await sleep(config.loop_interval)
    finally:
        if session is not None:
            await session.close()


async def _async_main(config: Config) -> int:
    if config.list_ports:
        print(format_ports(discover_ports()))
        return 0

    if config.export_xlsx:
        from .export_xlsx import export_csv_to_xlsx

        input_path = config.input or config.output
        export_csv_to_xlsx(input_path, config.export_xlsx)
        logger.info("Exported %s to %s", input_path, config.export_xlsx)
        return 0

    port = config.port or select_port(discover_ports())
    store = CsvStore(config.output)
    store.load()

    async def connect():
        return await MeshCoreSession.connect(
            port, config.baudrate, config.debug
        )

    if config.loop:
        await _run_loop(config, store, connect)
        return 0

    logger.info("Connecting to Companion on %s ...", port)
    session = await connect()
    started = time.monotonic()
    try:
        summary = await run_inventory(config, session, store)
    finally:
        await session.close()

    elapsed = time.monotonic() - started
    details = ", ".join(
        f"{status}={count}" for status, count in sorted(summary.statuses.items())
    )
    logger.info(
        "Done in %.0f s: polled=%s skipped=%s%s",
        elapsed,
        summary.polled,
        summary.skipped,
        f" ({details})" if details else "",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    config = build_config(argv)
    _setup_logging(config.debug)
    try:
        return asyncio.run(_async_main(config))
    except KeyboardInterrupt:
        logger.warning("Interrupted; last completed repeater is safely in the CSV")
        return 130
    except (PortError, CsvFormatError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("Fatal error")
        return 1
