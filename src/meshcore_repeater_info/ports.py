"""Transport discovery for the Companion (serial and BLE)."""

from __future__ import annotations

from dataclasses import dataclass

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class BleDeviceInfo:
    address: str
    name: str = ""
    rssi: int | None = None


class PortError(RuntimeError):
    """Raised when a Companion transport cannot be chosen unambiguously."""


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


def likely_port_candidates(ports: list[PortInfo]) -> list[str]:
    return [port.device for port in ports if _looks_like_meshcore(port)]


def discover_ports() -> list[PortInfo]:
    import serial.tools.list_ports

    return [
        PortInfo(device=port.device, description=port.description or "")
        for port in serial.tools.list_ports.comports()
    ]


def select_port(ports: list[PortInfo]) -> str:
    likely = likely_port_candidates(ports)
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


def looks_like_meshcore(name: str | None, service_uuids) -> bool:
    """True when an advertisement belongs to a MeshCore Companion."""
    if name and name.lower().startswith("meshcore"):
        return True
    return any(
        str(uuid).lower() == NUS_SERVICE_UUID for uuid in (service_uuids or [])
    )


async def discover_ble_devices(timeout: float = 5.0) -> list[BleDeviceInfo]:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    by_address: dict[str, BleDeviceInfo] = {}
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        if not looks_like_meshcore(name, adv.service_uuids):
            continue
        by_address.setdefault(
            device.address, BleDeviceInfo(device.address, name, adv.rssi)
        )
    return sorted(by_address.values(), key=lambda item: (item.name.lower(), item.address))


def format_ports(ports: list[PortInfo]) -> str:
    if not ports:
        return "No serial ports found."
    return "\n".join(f"{port.device:<24} {port.description}" for port in ports)


def format_ble_devices(devices: list[BleDeviceInfo]) -> str:
    if not devices:
        return "No MeshCore BLE devices found."
    lines = []
    for device in devices:
        rssi = f"{device.rssi} dBm" if device.rssi is not None else "-"
        lines.append(f"{device.name or '-':<24} {device.address:<40} {rssi:>8}")
    return "\n".join(lines)
