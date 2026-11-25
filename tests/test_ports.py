import pytest

from meshcore_repeater_info.ports import (
    BleDeviceInfo,
    PortError,
    PortInfo,
    discover_ble_devices,
    format_ble_devices,
    likely_port_candidates,
    looks_like_meshcore,
    select_port,
)


def test_single_likely_port_is_selected():
    ports = [
        PortInfo("/dev/cu.usbmodem1101", "USB JTAG/serial"),
        PortInfo("/dev/cu.Bluetooth-Incoming-Port", "n/a"),
    ]
    assert select_port(ports) == "/dev/cu.usbmodem1101"


def test_no_likely_port_is_an_error():
    ports = [PortInfo("/dev/cu.Bluetooth-Incoming-Port", "n/a")]
    with pytest.raises(PortError, match="No MeshCore"):
        select_port(ports)


def test_multiple_likely_ports_require_explicit_choice():
    ports = [
        PortInfo("/dev/cu.usbmodem1101", "USB JTAG/serial"),
        PortInfo("/dev/cu.usbmodem1201", "USB JTAG/serial"),
    ]
    with pytest.raises(PortError, match="Multiple"):
        select_port(ports)


def test_likely_port_candidates_filters_unrelated_ports():
    ports = [
        PortInfo("/dev/cu.usbmodem1101"),
        PortInfo("/dev/cu.Bluetooth-Incoming-Port", "n/a"),
    ]
    assert likely_port_candidates(ports) == ["/dev/cu.usbmodem1101"]


@pytest.mark.parametrize(
    ("name", "uuids", "expected"),
    [
        ("MeshCore-A1B2", [], True),
        ("meshcore-a1b2", [], True),
        ("Phone", ["6E400001-B5A3-F393-E0A9-E50E24DCCA9E"], True),
        ("Phone", ["0000180f-0000-1000-8000-00805f9b34fb"], False),
        (None, None, False),
    ],
)
def test_looks_like_meshcore(name, uuids, expected):
    assert looks_like_meshcore(name, uuids) is expected


def test_format_ble_devices_lists_name_address_and_rssi():
    text = format_ble_devices([BleDeviceInfo("AA:BB", "MeshCore-A1B2", -42)])
    assert "MeshCore-A1B2" in text
    assert "AA:BB" in text
    assert "-42" in text


def test_format_ble_devices_empty():
    assert format_ble_devices([]) == "No MeshCore BLE devices found."


@pytest.mark.asyncio
async def test_discover_ble_devices_filters_and_sorts(monkeypatch):
    import bleak

    class Device:
        def __init__(self, address, name):
            self.address = address
            self.name = name

    class Adv:
        def __init__(self, local_name, rssi, service_uuids):
            self.local_name = local_name
            self.rssi = rssi
            self.service_uuids = service_uuids

    found = {
        "1": (Device("AA", "MeshCore-B"), Adv("MeshCore-B", -60, [])),
        "2": (Device("BB", "Phone"), Adv("Phone", -40, [])),
        "3": (Device("CC", "MeshCore-A"), Adv("MeshCore-A", -50, [])),
    }

    async def fake_discover(timeout=5.0, return_adv=False):
        return found

    monkeypatch.setattr(bleak.BleakScanner, "discover", staticmethod(fake_discover))
    devices = await discover_ble_devices()

    assert [device.name for device in devices] == ["MeshCore-A", "MeshCore-B"]
    assert [device.rssi for device in devices] == [-50, -60]


@pytest.mark.asyncio
async def test_discover_ble_devices_wraps_scanner_failure(monkeypatch):
    import bleak

    async def failing_discover(timeout=5.0, return_adv=False):
        raise RuntimeError("adapter off")

    monkeypatch.setattr(bleak.BleakScanner, "discover", staticmethod(failing_discover))

    with pytest.raises(PortError, match="BLE scan failed"):
        await discover_ble_devices()
