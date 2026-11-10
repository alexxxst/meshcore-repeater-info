"""Per-repeater login policy and data collection."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .formatting import clock_status
from .mesh import LoginOutcome, MeshSession, OwnerInfo, StatusInfo
from .model import PollResult

DEFAULT_PASSWORDS = (
    "",
    "123456",
    "guest",
    "password",
    "1234",
    "1234567890",
)


@dataclass(frozen=True)
class PollConfig:
    timeout: float = 15.0
    request_timeout: float = 25.0
    min_delay: float = 20.0
    max_delay: float = 30.0
    clock_skew_threshold: int = 600
    max_login_attempts: int = 35
    request_attempts: int = 5
    passwords: tuple[str, ...] = DEFAULT_PASSWORDS
    empty_attempts: int = 3
    password_attempts: int = 2


class RouteMode(Enum):
    INITIAL_FLOOD = "initial_flood"
    PATH = "path"
    FLOOD = "flood"


@dataclass(slots=True)
class RequestResult:
    value: Any | None
    attempts: int
    used_flood: bool
    mode: RouteMode
    last_reason: str | None = None

    @property
    def transport(self) -> str:
        return "flood" if self.used_flood else "path"


class AttemptTracker(Protocol):
    def record_attempt(self, key: str, total: int, last: int) -> None: ...

    def record_login_success(
        self,
        key: str,
        last: int,
        is_admin: bool,
        login_password: str,
        login_method: str,
    ) -> None: ...


def _password_label(password: str) -> str:
    return "(empty)" if password == "" else password


def _saved_password(row: dict | None) -> str | None:
    value = (row or {}).get("login_password", "").strip()
    if value == "(empty)":
        return ""
    return value or None


def build_login_candidates(
    saved_password: str | None, config: PollConfig
) -> list[str]:
    """Build the expanded attempt list, preferring ACL and the known password."""
    if saved_password == "(empty)":
        saved_password = ""

    order: list[str] = [""]
    if saved_password:
        order.append(saved_password)
    for password in config.passwords:
        if password not in order:
            order.append(password)

    attempts: list[str] = []
    for password in order:
        count = config.empty_attempts if password == "" else config.password_attempts
        attempts.extend([password] * count)
    return attempts


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Poller:
    def __init__(
        self,
        session: MeshSession,
        config: PollConfig,
        tracker: AttemptTracker,
        logger: logging.Logger | None = None,
        sleep=None,
        now=None,
        rng: random.Random | None = None,
    ):
        self.session = session
        self.config = config
        self.tracker = tracker
        self.logger = logger or logging.getLogger(__name__)
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.time
        self._rng = rng or random.Random()

    async def _pause(self) -> None:
        delay = self._rng.uniform(self.config.min_delay, self.config.max_delay)
        await self._sleep(delay)

    def _advert_timestamp(self, contact: dict) -> int | None:
        last_advert = contact.get("last_advert")
        if not isinstance(last_advert, (int, float)) or last_advert <= 0:
            return None
        return int(last_advert)

    def _failure_result(
        self,
        key: str,
        contact_name: str,
        *,
        status: str,
        error: str,
        last: int,
        total: int,
        advert_timestamp: int | None = None,
    ) -> PollResult:
        return PollResult(
            key=key,
            contact_name=contact_name,
            advert_timestamp=advert_timestamp,
            login_attempts_last=last,
            login_attempts_total=total,
            status=status,
            error=error,
        )

    async def _request_with_retries(
        self,
        contact: dict,
        contact_name: str,
        request_name: str,
        operation,
        mode: RouteMode,
    ) -> RequestResult:
        used_flood = False
        attempts = 0
        last_reason = None

        for _ in range(self.config.request_attempts):
            attempts += 1
            await self._pause()

            if mode in (RouteMode.INITIAL_FLOOD, RouteMode.FLOOD):
                used_flood = True
                if not await self.session.prepare_flood(
                    contact, self.config.request_timeout
                ):
                    last_reason = "reset_failed"
                    self.logger.warning(
                        "%s: %s attempt %s/%s could not reset path to flood",
                        contact_name,
                        request_name,
                        attempts,
                        self.config.request_attempts,
                    )
                    continue

            self.logger.info(
                "%s: %s attempt %s/%s (%s)",
                contact_name,
                request_name,
                attempts,
                self.config.request_attempts,
                mode.value,
            )
            value = await operation()
            if value is not None:
                if mode is RouteMode.INITIAL_FLOOD and self.session.has_route(contact):
                    self.logger.info(
                        "%s: route learned, switching to path", contact_name
                    )
                    mode = RouteMode.PATH
                return RequestResult(value, attempts, used_flood, mode)

            last_reason = "no_response"
            if mode is RouteMode.PATH:
                mode = RouteMode.FLOOD
                self.logger.info(
                    "%s: path %s failed, switching to flood for the rest of the session",
                    contact_name,
                    request_name,
                )

        self.logger.warning(
            "%s: %s failed after %s attempts",
            contact_name,
            request_name,
            attempts,
        )
        return RequestResult(None, attempts, used_flood, mode, last_reason)

    async def poll(
        self, contact: dict, existing_row: dict | None = None
    ) -> PollResult:
        key = contact["public_key"].lower()
        contact_name = contact.get("adv_name", "")
        existing = existing_row or {}
        saved_password = _saved_password(existing)
        total = _as_int(existing.get("login_attempts_total"))
        last = 0
        advert_timestamp = self._advert_timestamp(contact)
        candidates = build_login_candidates(saved_password, self.config)
        mode = RouteMode.INITIAL_FLOOD
        self.logger.info("%s: starting with flood", contact_name)

        outcome: LoginOutcome | None = None
        matched_password: str | None = None
        reasons: list[str] = []

        for password in candidates:
            if total + 1 > self.config.max_login_attempts:
                message = (
                    f"login attempt limit reached ({self.config.max_login_attempts})"
                )
                self.logger.warning("%s: %s", contact_name, message)
                return self._failure_result(
                    key,
                    contact_name,
                    status="final_fail",
                    error=message,
                    last=last,
                    total=total,
                    advert_timestamp=advert_timestamp,
                )

            total += 1
            last += 1
            self.tracker.record_attempt(key, total, last)
            await self._pause()

            if mode in (RouteMode.INITIAL_FLOOD, RouteMode.FLOOD):
                if not await self.session.prepare_flood(
                    contact, self.config.timeout
                ):
                    reasons.append("reset_failed")
                    self.logger.warning(
                        "%s: login attempt %s could not reset path to flood",
                        contact_name,
                        last,
                    )
                    continue

            self.logger.info(
                "%s: login attempt %s (total %s), password=%s (%s)",
                contact_name,
                last,
                total,
                _password_label(password),
                mode.value,
            )
            attempt = await self.session.login(
                contact, password, self.config.timeout
            )
            if attempt.success:
                outcome = attempt
                matched_password = password
                if mode is RouteMode.INITIAL_FLOOD and self.session.has_route(contact):
                    self.logger.info(
                        "%s: route learned, switching to path", contact_name
                    )
                    mode = RouteMode.PATH
                break

            reasons.append(attempt.reason)
            if attempt.reason == "rejected":
                # A rejection is a valid reply: the route is usable.
                if mode is RouteMode.INITIAL_FLOOD and self.session.has_route(contact):
                    self.logger.info(
                        "%s: route learned, switching to path", contact_name
                    )
                    mode = RouteMode.PATH
                continue
            if attempt.reason == "timeout":
                if mode is RouteMode.PATH:
                    mode = RouteMode.FLOOD
                    self.logger.info(
                        "%s: path login failed, switching to flood for the rest of the session",
                        contact_name,
                    )
                continue
            if attempt.reason == "error" and mode is RouteMode.PATH:
                mode = RouteMode.FLOOD

        if outcome is None:
            if reasons and all(
                reason in {"timeout", "reset_failed"} for reason in reasons
            ):
                status = "timeout"
            elif reasons and all(reason == "error" for reason in reasons):
                status = "error"
            else:
                status = "login_failed"
            detail = reasons[-1] if reasons else "no attempts"
            return self._failure_result(
                key,
                contact_name,
                status=status,
                error=f"{len(reasons)} login attempts failed (last: {detail})",
                last=last,
                total=total,
                advert_timestamp=advert_timestamp,
            )

        login_method = "password" if matched_password else "empty"
        if login_method == "empty":
            login_password = saved_password or "(empty)"
        else:
            login_password = matched_password or "(empty)"

        self.logger.info(
            "%s: login ok as %s via %s (password=%s)",
            contact_name,
            "admin" if outcome.is_admin else "guest",
            login_method,
            _password_label(login_password),
        )
        self.tracker.record_login_success(
            key,
            last,
            outcome.is_admin,
            login_password,
            login_method,
        )

        login_timestamp = outcome.server_timestamp
        local_timestamp = int(self._now())
        skew = (
            login_timestamp - local_timestamp
            if login_timestamp is not None
            else None
        )

        owner: OwnerInfo | None = None
        owner_attempts = 0
        owner_used_flood = False
        if outcome.fw_ver_level is None or outcome.fw_ver_level >= 2:
            result = await self._request_with_retries(
                contact,
                contact_name,
                "owner info",
                lambda: self.session.get_owner_info(
                    contact, self.config.request_timeout
                ),
                mode,
            )
            owner = result.value
            owner_attempts = result.attempts
            owner_used_flood = result.used_flood
            mode = result.mode

        version = owner.firmware_version if owner else None
        name = owner.name if owner else None
        owner_info = owner.owner_info if owner else None

        remote_version_attempts = 0
        remote_version_flood = False
        remote_name_attempts = 0
        remote_name_flood = False
        remote_owner_attempts = 0
        remote_owner_flood = False

        if outcome.is_admin:
            if version is None:
                result = await self._request_with_retries(
                    contact,
                    contact_name,
                    "version via CLI",
                    lambda: self.session.get_remote_version(
                        contact, self.config.request_timeout
                    ),
                    mode,
                )
                version = result.value
                remote_version_attempts = result.attempts
                remote_version_flood = result.used_flood
                mode = result.mode
            if name is None:
                result = await self._request_with_retries(
                    contact,
                    contact_name,
                    "name via CLI",
                    lambda: self.session.get_remote_name(
                        contact, self.config.request_timeout
                    ),
                    mode,
                )
                name = result.value
                remote_name_attempts = result.attempts
                remote_name_flood = result.used_flood
                mode = result.mode
            if owner_info is None:
                result = await self._request_with_retries(
                    contact,
                    contact_name,
                    "owner info via CLI",
                    lambda: self.session.get_remote_owner_info(
                        contact, self.config.request_timeout
                    ),
                    mode,
                )
                owner_info = result.value
                remote_owner_attempts = result.attempts
                remote_owner_flood = result.used_flood
                mode = result.mode

        version_is_old = False
        if version is None:
            if outcome.fw_ver_level is not None and outcome.fw_ver_level < 2:
                version = "старая (FW < 1.12)"
                version_is_old = True
            elif outcome.fw_ver_level is None:
                version = "старая (версия недоступна)"
                version_is_old = True

        status_result = await self._request_with_retries(
            contact,
            contact_name,
            "status",
            lambda: self.session.get_status(contact, self.config.request_timeout),
            mode,
        )
        status_info: StatusInfo | None = status_result.value
        mode = status_result.mode

        neighbours_result = await self._request_with_retries(
            contact,
            contact_name,
            "neighbours",
            lambda: self.session.get_neighbours_count(
                contact, self.config.request_timeout
            ),
            mode,
        )
        neighbours = neighbours_result.value
        mode = neighbours_result.mode

        missing: list[str] = []
        if status_info is None:
            missing.append(
                f"status ({status_result.attempts} attempts, {status_result.transport})"
            )
        if neighbours is None:
            missing.append(
                f"neighbours ({neighbours_result.attempts} attempts, {neighbours_result.transport})"
            )
        if version is None:
            missing.append("firmware version")
        if name is None and not version_is_old:
            missing.append("repeater name")
        if (
            owner is None
            and owner_attempts
            and (version is None or name is None)
        ):
            missing.append(
                f"owner info ({owner_attempts} attempts, "
                + ("flood" if owner_used_flood else "path")
                + ")"
            )
        if version is None and remote_version_attempts:
            missing.append(
                f"version via CLI ({remote_version_attempts} attempts, "
                + ("flood" if remote_version_flood else "path")
                + ")"
            )
        if name is None and remote_name_attempts:
            missing.append(
                f"name via CLI ({remote_name_attempts} attempts, "
                + ("flood" if remote_name_flood else "path")
                + ")"
            )
        if owner_info is None and remote_owner_attempts and owner is None:
            missing.append(
                f"owner info via CLI ({remote_owner_attempts} attempts, "
                + ("flood" if remote_owner_flood else "path")
                + ")"
            )

        if version_is_old and status_info is not None and neighbours is not None:
            status = "ok_old"
            notes = ["old firmware: version unavailable"]
            if name is None:
                notes.append("repeater name unavailable")
            error = "; ".join(notes)
        elif not missing:
            status = "ok"
            error = None
        else:
            status = "partial"
            error = "missing: " + ", ".join(missing)

        return PollResult(
            key=key,
            contact_name=contact_name,
            repeater_name=name,
            advert_timestamp=advert_timestamp,
            firmware_version=version,
            owner_info=owner_info,
            uptime_seconds=status_info.uptime_seconds if status_info else None,
            tx_airtime_seconds=status_info.tx_airtime_seconds if status_info else None,
            rx_airtime_seconds=status_info.rx_airtime_seconds if status_info else None,
            login_timestamp=login_timestamp,
            clock_skew_seconds=skew,
            clock_status=clock_status(skew, self.config.clock_skew_threshold),
            neighbours_count=neighbours,
            noise_floor_dbm=status_info.noise_floor_dbm if status_info else None,
            debug_flags=status_info.debug_flags if status_info else None,
            is_admin=outcome.is_admin,
            login_password=login_password,
            login_method=login_method,
            login_attempts_last=last,
            login_attempts_total=0,
            status=status,
            error=error,
        )
