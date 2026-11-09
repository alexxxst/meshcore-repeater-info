"""Protocol-facing data types and the real MeshCore Companion session."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoginOutcome:
    success: bool
    reason: str
    is_admin: bool = False
    server_timestamp: int | None = None
    fw_ver_level: int | None = None


@dataclass(slots=True)
class OwnerInfo:
    firmware_version: str | None
    name: str | None
    owner_info: str | None


@dataclass(slots=True)
class StatusInfo:
    uptime_seconds: int
    tx_airtime_seconds: int
    rx_airtime_seconds: int
    noise_floor_dbm: int
    debug_flags: int


class MeshSession(Protocol):
    """The small radio-facing surface the poller needs."""

    async def get_repeater_contacts(self) -> list[dict[str, Any]]: ...

    async def prepare_flood(self, contact: dict, timeout: float) -> bool: ...

    def has_route(self, contact: dict) -> bool: ...

    async def login(self, contact: dict, password: str, timeout: float) -> LoginOutcome: ...

    async def get_owner_info(self, contact: dict, timeout: float) -> OwnerInfo | None: ...

    async def get_remote_version(self, contact: dict, timeout: float) -> str | None: ...

    async def get_remote_name(self, contact: dict, timeout: float) -> str | None: ...

    async def get_remote_owner_info(self, contact: dict, timeout: float) -> str | None: ...

    async def get_status(self, contact: dict, timeout: float) -> StatusInfo | None: ...

    async def get_neighbours_count(self, contact: dict, timeout: float) -> int | None: ...

    async def close(self) -> None: ...


class RepeaterRequest(IntEnum):
    STATUS = 0x01
    NEIGHBOURS = 0x06
    OWNER_INFO = 0x07


class MeshCoreSession:
    """Serial Companion implementation backed by the meshcore Python library."""

    def __init__(self, mc):
        self.mc = mc
        self._route_learned: set[str] = set()
        self._path_subscription = None
        if mc is not None:
            from meshcore.events import EventType

            try:
                self._path_subscription = mc.subscribe(
                    EventType.PATH_UPDATE, self._on_path_update
                )
            except Exception:
                logger.debug("Could not subscribe to PATH_UPDATE", exc_info=True)

    @classmethod
    async def connect(
        cls,
        port: str,
        baudrate: int = 115200,
        debug: bool = False,
    ) -> "MeshCoreSession":
        from meshcore import MeshCore

        mc = await MeshCore.create_serial(port, baudrate, debug=debug)
        if mc is None:
            raise RuntimeError(f"Companion did not respond on {port}")
        return cls(mc)

    def _on_path_update(self, event) -> None:
        payload = event.payload or {}
        key = payload.get("public_key") or (event.attributes or {}).get("public_key")
        if key:
            self._route_learned.add(str(key).lower())

    def has_route(self, contact: dict) -> bool:
        return contact["public_key"].lower() in self._route_learned

    async def prepare_flood(self, contact: dict, timeout: float) -> bool:
        """Reset the companion's stored route so the next send is flooded."""
        from meshcore.events import EventType

        key = contact["public_key"].lower()
        try:
            result = await asyncio.wait_for(
                self.mc.commands.reset_path(contact), timeout
            )
        except asyncio.TimeoutError:
            logger.warning("reset_path timed out for %s", key[:12])
            return False
        except Exception as exc:
            logger.warning("reset_path failed for %s: %s", key[:12], exc)
            return False
        if result is None or result.type == EventType.ERROR:
            return False
        # Discard only after the companion confirmed the reset, so a late
        # PATH_UPDATE from before the reset cannot masquerade as a fresh route.
        self._route_learned.discard(key)
        contact["out_path_len"] = -1
        contact["out_path"] = ""
        return True

    async def get_repeater_contacts(self) -> list[dict[str, Any]]:
        await self.mc.ensure_contacts()
        contacts = getattr(self.mc, "contacts", {}) or {}
        return [
            contact
            for contact in contacts.values()
            if int(contact.get("type", 0)) == 2  # ADV_TYPE_REPEATER
        ]

    async def login(
        self, contact: dict, password: str, timeout: float
    ) -> LoginOutcome:
        from meshcore.events import EventType

        prefix = contact["public_key"][:12].lower()
        queue: asyncio.Queue = asyncio.Queue()

        def handler(event):
            queue.put_nowait(event)

        subscriptions = [
            self.mc.subscribe(EventType.LOGIN_SUCCESS, handler, {"pubkey_prefix": prefix}),
            self.mc.subscribe(EventType.LOGIN_FAILED, handler, {"pubkey_prefix": prefix}),
        ]
        try:
            key_hex = contact["public_key"]
            try:
                key = bytes.fromhex(key_hex)
            except ValueError:
                return LoginOutcome(success=False, reason="error")
            if len(key) != 32:
                return LoginOutcome(success=False, reason="error")

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            # Send the same raw SEND_LOGIN command as meshcore's send_login(),
            # but without its "use send_login_sync" warning: we need to handle
            # both LOGIN_SUCCESS and LOGIN_FAILED ourselves.
            data = b"\x1a" + key + password.encode("utf-8")
            sent = await self.mc.commands.send(
                data,
                [EventType.MSG_SENT, EventType.ERROR],
                timeout=max(0.1, deadline - loop.time()),
            )
            if sent is None:
                return LoginOutcome(success=False, reason="error")
            if sent.type == EventType.ERROR:
                reason = "timeout" if (sent.payload or {}).get("reason") == "timeout" else "error"
                return LoginOutcome(success=False, reason=reason)

            remaining = deadline - loop.time()
            if remaining <= 0:
                return LoginOutcome(success=False, reason="timeout")
            try:
                event = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                return LoginOutcome(success=False, reason="timeout")

            if event.type == EventType.LOGIN_FAILED:
                return LoginOutcome(success=False, reason="rejected")

            payload = event.payload or {}
            return LoginOutcome(
                success=True,
                reason="ok",
                is_admin=bool(payload.get("is_admin", False)),
                server_timestamp=payload.get("server_timestamp"),
                fw_ver_level=payload.get("fw_ver_level"),
            )
        finally:
            for subscription in subscriptions:
                subscription.unsubscribe()

    async def _binary_request(
        self,
        contact: dict,
        request_type: int,
        timeout: float,
        data: bytes = b"",
    ):
        from meshcore.events import EventType

        queue: asyncio.Queue = asyncio.Queue()

        def handler(event):
            queue.put_nowait(event)

        subscription = self.mc.subscribe(EventType.BINARY_RESPONSE, handler)
        try:
            sent = await self.mc.commands.send_binary_req(
                contact,
                request_type,
                data=data,
                timeout=timeout,
                min_timeout=timeout,
            )
            if sent is None or sent.type == EventType.ERROR:
                return None
            tag = sent.payload.get("expected_ack")
            if tag is None:
                return None
            if isinstance(tag, bytes):
                tag = tag.hex()
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                try:
                    event = await asyncio.wait_for(queue.get(), remaining)
                except asyncio.TimeoutError:
                    return None
                if event.payload.get("tag") == tag:
                    return bytes.fromhex(event.payload["data"])
        finally:
            subscription.unsubscribe()

    async def get_owner_info(
        self, contact: dict, timeout: float
    ) -> OwnerInfo | None:
        payload = await self._binary_request(
            contact, RepeaterRequest.OWNER_INFO, timeout
        )
        if payload is None:
            return None
        text = payload.decode("utf-8", "ignore").rstrip("\x00")
        if not text:
            return None
        lines = text.split("\n", 2)
        version = lines[0].strip() if len(lines) > 0 else None
        name = lines[1].strip() if len(lines) > 1 else None
        owner = lines[2].strip() if len(lines) > 2 else None
        return OwnerInfo(
            firmware_version=version or None,
            name=name or None,
            owner_info=owner or None,
        )

    async def _remote_cli(
        self, contact: dict, command: str, timeout: float
    ) -> str | None:
        from meshcore.events import EventType

        prefix = contact["public_key"][:12].lower()
        queue: asyncio.Queue = asyncio.Queue()
        subscription = self.mc.subscribe(
            EventType.CONTACT_MSG_RECV,
            lambda event: queue.put_nowait(event),
            {"pubkey_prefix": prefix, "txt_type": 1},
        )
        auto_fetch = None
        try:
            auto_fetch = await self.mc.start_auto_message_fetching()
            sent = await self.mc.commands.send_cmd(contact, command)
            if sent is None or sent.type == EventType.ERROR:
                return None
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                try:
                    event = await asyncio.wait_for(queue.get(), remaining)
                except asyncio.TimeoutError:
                    return None
                payload = event.payload or {}
                if payload.get("pubkey_prefix") != prefix:
                    continue
                text = (payload.get("text") or "").strip()
                if not text:
                    continue
                lowered = text.lower()
                if lowered.startswith(("err", "unknown command")):
                    return None
                return text
        finally:
            subscription.unsubscribe()
            if auto_fetch is not None:
                await self.mc.stop_auto_message_fetching()

    async def get_remote_version(
        self, contact: dict, timeout: float
    ) -> str | None:
        text = await self._remote_cli(contact, "ver", timeout)
        if not text:
            return None
        first = text.split()[0]
        return first if first.lower().startswith("v") else text

    async def get_remote_name(
        self, contact: dict, timeout: float
    ) -> str | None:
        text = await self._remote_cli(contact, "get name", timeout)
        if not text:
            return None
        return text.removeprefix(">").strip() or None

    async def get_remote_owner_info(
        self, contact: dict, timeout: float
    ) -> str | None:
        text = await self._remote_cli(contact, "get owner.info", timeout)
        if not text:
            return None
        return text.removeprefix(">").strip() or None

    async def get_status(self, contact: dict, timeout: float) -> StatusInfo | None:
        from meshcore.parsing import parse_status

        payload = await self._binary_request(contact, RepeaterRequest.STATUS, timeout)
        if payload is None or len(payload) < 52:
            return None
        parsed = parse_status(payload, pubkey_prefix=contact["public_key"][:12])
        return StatusInfo(
            uptime_seconds=int(parsed["uptime"]),
            tx_airtime_seconds=int(parsed["airtime"]),
            rx_airtime_seconds=int(parsed["rx_airtime"]),
            noise_floor_dbm=int(parsed["noise_floor"]),
            debug_flags=int(parsed["full_evts"]),
        )

    async def get_neighbours_count(
        self, contact: dict, timeout: float
    ) -> int | None:
        # One entry is enough; the response always starts with the total count.
        request = (
            b"\x00"
            + (1).to_bytes(1, "little")
            + (0).to_bytes(2, "little")
            + (0).to_bytes(1, "little")
            + (4).to_bytes(1, "little")
            + random.randint(1, 0xFFFFFFFF).to_bytes(4, "little")
        )
        payload = await self._binary_request(
            contact, RepeaterRequest.NEIGHBOURS, timeout, data=request
        )
        if payload is None or len(payload) < 4:
            return None
        return int.from_bytes(payload[0:2], "little", signed=True)

    async def close(self) -> None:
        if self._path_subscription is not None:
            self._path_subscription.unsubscribe()
            self._path_subscription = None
        await self.mc.disconnect()
