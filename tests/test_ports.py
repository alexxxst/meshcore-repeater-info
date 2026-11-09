import pytest

from meshcore_repeater_info.ports import PortError, PortInfo, select_port


def test_explicit_environment_port_wins():
    ports = [PortInfo("/dev/cu.usbmodem1101", "USB JTAG/serial")]
    assert select_port(ports, {"MESHCORE_PORT": "/dev/cu.custom"}) == "/dev/cu.custom"


def test_single_likely_port_is_selected():
    ports = [
        PortInfo("/dev/cu.usbmodem1101", "USB JTAG/serial"),
        PortInfo("/dev/cu.Bluetooth-Incoming-Port", "n/a"),
    ]
    assert select_port(ports, {}) == "/dev/cu.usbmodem1101"


def test_no_likely_port_is_an_error():
    ports = [PortInfo("/dev/cu.Bluetooth-Incoming-Port", "n/a")]
    with pytest.raises(PortError, match="No MeshCore"):
        select_port(ports, {})


def test_multiple_likely_ports_require_explicit_choice():
    ports = [
        PortInfo("/dev/cu.usbmodem1101", "USB JTAG/serial"),
        PortInfo("/dev/cu.usbmodem1201", "USB JTAG/serial"),
    ]
    with pytest.raises(PortError, match="Multiple"):
        select_port(ports, {})
