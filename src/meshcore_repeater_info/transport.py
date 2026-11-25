"""Companion transport selection: serial, BLE and TCP."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping

DEFAULT_TCP_PORT = 4000


class TransportError(RuntimeError):
    """Raised when the Companion transport is lost or unusable."""


@dataclass(frozen=True, slots=True)
class TransportSpec:
    kind: Literal["serial", "ble", "tcp"]
    target: str | None = None
    port: int | None = None
    pin: str | None = None

    def describe(self) -> str:
        if self.kind == "serial":
            return f"serial {self.target}"
        if self.kind == "ble":
            return f"ble://{self.target or '<scan>'}"
        return f"tcp://{self.target}:{self.port}"


def parse_transport(value: str) -> TransportSpec:
    """Turn a ``-p`` value into a transport spec.

    A plain path/device is serial (backward compatible). ``ble`` and
    ``ble://NAME`` select BLE, ``tcp://HOST[:PORT]`` selects TCP.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("empty transport")

    lowered = text.lower()
    if lowered.startswith("tcp://"):
        rest = text[len("tcp://") :]
        if not rest or rest.startswith(":"):
            raise ValueError("tcp:// needs a host, e.g. tcp://192.168.1.50:4000")
        if ":" in rest:
            host, port_text = rest.rsplit(":", 1)
            if not host:
                raise ValueError("tcp:// needs a host, e.g. tcp://192.168.1.50:4000")
            if not port_text.isdigit():
                raise ValueError(f"invalid TCP port {port_text!r}")
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError(f"TCP port out of range: {port}")
        else:
            host = rest
            port = DEFAULT_TCP_PORT
        return TransportSpec("tcp", host, port)

    if lowered == "ble" or lowered.startswith("ble://"):
        target = text[len("ble://") :].strip() if lowered.startswith("ble://") else ""
        return TransportSpec("ble", target or None)

    if "://" in text:
        scheme = text.split("://", 1)[0]
        raise ValueError(
            f"unknown transport {scheme!r}; use a serial port, ble://NAME, "
            "or tcp://HOST[:PORT]"
        )

    return TransportSpec("serial", text)


def transport_from_env(
    env: Mapping[str, str] | None = None,
) -> TransportSpec | None:
    """Parse ``MESHCORE_PORT``, which accepts the same values as ``-p``."""
    values = os.environ if env is None else env
    value = (values.get("MESHCORE_PORT") or "").strip()
    if not value:
        return None
    return parse_transport(value)
