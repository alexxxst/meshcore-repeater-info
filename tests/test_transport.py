import pytest

from meshcore_repeater_info.mesh import MeshCoreSession
from meshcore_repeater_info.ports import BleDeviceInfo, PortError
from meshcore_repeater_info.transport import (
    DEFAULT_TCP_PORT,
    TransportSpec,
    ble_spec_from_devices,
    connect_session,
    parse_transport,
    transport_from_env,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/dev/cu.usbmodem1101", TransportSpec("serial", "/dev/cu.usbmodem1101")),
        ("COM3", TransportSpec("serial", "COM3")),
        ("ble", TransportSpec("ble")),
        ("ble://", TransportSpec("ble")),
        ("BLE://MeshCore-A1B2", TransportSpec("ble", "MeshCore-A1B2")),
        ("ble://24:62:AB:12:34:56", TransportSpec("ble", "24:62:AB:12:34:56")),
        ("tcp://192.168.1.50", TransportSpec("tcp", "192.168.1.50", DEFAULT_TCP_PORT)),
        ("tcp://192.168.1.50:5000", TransportSpec("tcp", "192.168.1.50", 5000)),
        ("TCP://Host", TransportSpec("tcp", "Host", DEFAULT_TCP_PORT)),
    ],
)
def test_parse_transport(value, expected):
    assert parse_transport(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "   ", "tcp://", "tcp://:4000", "tcp://host:abc", "tcp://host:0", "tcp://host:70000"],
)
def test_parse_transport_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_transport(value)


def test_parse_transport_rejects_unknown_scheme_by_name():
    with pytest.raises(ValueError, match="unknown transport"):
        parse_transport("bluetooth://MeshCore-A1B2")


def test_transport_from_env_reads_meshcore_port(monkeypatch):
    monkeypatch.setenv("MESHCORE_PORT", "ble://MeshCore-A1B2")
    assert transport_from_env() == TransportSpec("ble", "MeshCore-A1B2")


def test_transport_from_env_ignores_missing_or_empty(monkeypatch):
    monkeypatch.delenv("MESHCORE_PORT", raising=False)
    assert transport_from_env() is None
    monkeypatch.setenv("MESHCORE_PORT", "")
    assert transport_from_env() is None


def test_describe_shows_transport_and_target():
    assert TransportSpec("serial", "/dev/x").describe() == "serial /dev/x"
    assert TransportSpec("ble").describe() == "ble://<scan>"
    assert TransportSpec("ble", "MeshCore-A1B2").describe() == "ble://MeshCore-A1B2"
    assert TransportSpec("tcp", "10.0.0.2", 4000).describe() == "tcp://10.0.0.2:4000"


def test_single_ble_device_is_selected_by_address():
    spec = ble_spec_from_devices([BleDeviceInfo("AA:BB", "MeshCore-A1B2", -40)])
    assert spec == TransportSpec("ble", "AA:BB")


def test_no_ble_devices_is_an_error():
    with pytest.raises(PortError, match="No MeshCore BLE device"):
        ble_spec_from_devices([])


def test_multiple_ble_devices_require_explicit_choice():
    devices = [
        BleDeviceInfo("AA", "MeshCore-A", -40),
        BleDeviceInfo("BB", "MeshCore-B", -50),
    ]
    with pytest.raises(PortError, match="Multiple MeshCore BLE devices"):
        ble_spec_from_devices(devices)


@pytest.mark.asyncio
async def test_connect_session_dispatches_serial(monkeypatch):
    calls = []

    async def fake(cls, port, baudrate=115_200, debug=False):
        calls.append((port, baudrate, debug))
        return "session"

    monkeypatch.setattr(MeshCoreSession, "connect_serial", classmethod(fake))

    assert await connect_session(TransportSpec("serial", "/dev/x"), 9_600, True) == "session"
    assert calls == [("/dev/x", 9_600, True)]


@pytest.mark.asyncio
async def test_connect_session_dispatches_ble_with_pin(monkeypatch):
    calls = []

    async def fake(cls, address=None, pin=None, debug=False):
        calls.append((address, pin, debug))
        return "session"

    monkeypatch.setattr(MeshCoreSession, "connect_ble", classmethod(fake))
    spec = TransportSpec("ble", "MeshCore-A1B2", pin="123456")

    assert await connect_session(spec) == "session"
    assert calls == [("MeshCore-A1B2", "123456", False)]


@pytest.mark.asyncio
async def test_connect_session_dispatches_tcp(monkeypatch):
    calls = []

    async def fake(cls, host, port, debug=False):
        calls.append((host, port, debug))
        return "session"

    monkeypatch.setattr(MeshCoreSession, "connect_tcp", classmethod(fake))
    spec = TransportSpec("tcp", "10.0.0.2", 4000)

    assert await connect_session(spec) == "session"
    assert calls == [("10.0.0.2", 4000, False)]
