"""Serial port discovery for the USB Companion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""


class PortError(RuntimeError):
    """Raised when the Companion serial port cannot be chosen unambiguously."""


def _looks_like_meshcore(port: PortInfo) -> bool:
    device = port.device.lower()
    description = port.description.lower()
    if "meshcore" in description:
        return True
    if "usb jtag/serial" in description:
        return True
    return device.startswith(
        (
            "/dev/cu.usbmodem",
            "/dev/tty.usbmodem",
            "/dev/ttyusb",
            "/dev/ttyacm",
            "com",
        )
    )


def discover_ports() -> list[PortInfo]:
    import serial.tools.list_ports

    return [
        PortInfo(device=port.device, description=port.description or "")
        for port in serial.tools.list_ports.comports()
    ]


def select_port(
    ports: list[PortInfo], env: Mapping[str, str] | None = None
) -> str:
    env = env if env is not None else os.environ
    explicit = env.get("MESHCORE_PORT")
    if explicit:
        return explicit

    likely = [port.device for port in ports if _looks_like_meshcore(port)]
    if len(likely) == 1:
        return likely[0]
    if not likely:
        raise PortError(
            "No MeshCore serial port found. Connect the Companion or pass --port."
        )
    raise PortError(
        "Multiple possible MeshCore serial ports: "
        + ", ".join(likely)
        + ". Pass --port to choose one."
    )


def format_ports(ports: list[PortInfo]) -> str:
    if not ports:
        return "No serial ports found."
    return "\n".join(f"{port.device:<24} {port.description}" for port in ports)
