import pytest

from meshcore_repeater_info.transport import (
    DEFAULT_TCP_PORT,
    TransportSpec,
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
