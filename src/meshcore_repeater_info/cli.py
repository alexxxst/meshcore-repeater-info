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
from .mesh import MeshCoreSession
from .model import PollResult
from .poller import PollConfig, Poller
from .ports import PortError, discover_ports, format_ports, select_port
from .storage import CsvFormatError, CsvStore, should_process

logger = logging.getLogger(__name__)

DEFAULT_PASSWORDS = ",123456,guest,1234,admin,1234567890,password"


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
        help="timeout for every login or radio request attempt (default: 15)",
    )
    parser.add_argument("--min-delay", type=float, default=2.0)
    parser.add_argument("--max-delay", type=float, default=4.0)
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
        force=args.force,
        alive=args.alive,
        dead=args.dead,
        broken=args.broken,
        only=args.only,
        limit=args.limit,
        timeout=args.timeout,
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
        existing = store.get(key)

        if not should_process(existing, mode):
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

        store.ensure_row(
            key,
            contact_name,
            reset_total=config.force or config.dead or config.broken,
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


async def _async_main(config: Config) -> int:
    if config.list_ports:
        print(format_ports(discover_ports()))
        return 0

    port = config.port or select_port(discover_ports())
    store = CsvStore(config.output)
    store.load()

    logger.info("Connecting to Companion on %s ...", port)
    session = await MeshCoreSession.connect(port, config.baudrate, config.debug)
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
