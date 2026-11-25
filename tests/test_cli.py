from pathlib import Path

import pytest

import meshcore_repeater_info.cli as cli
from meshcore_repeater_info.cli import build_config, select_contacts
from meshcore_repeater_info.ports import BleDeviceInfo, PortError, PortInfo
from meshcore_repeater_info.transport import TransportError, TransportSpec


def contact(key_char: str, name: str) -> dict:
    return {"public_key": key_char * 64, "adv_name": name, "type": 2}


def test_parser_defaults():
    config = build_config([])

    assert config.transport is None
    assert config.baudrate == 115_200
    assert config.list_ble is False
    assert config.output == Path("repeaters.csv")
    assert config.force is False
    assert config.alive is False
    assert config.dead is False
    assert config.broken is False
    assert config.input is None
    assert config.export_xlsx is None
    assert config.loop is False
    assert config.loop_interval == 60.0
    assert config.timeout == 15.0
    assert config.request_timeout == 25.0
    assert config.min_delay == 2.0
    assert config.max_delay == 5.0
    assert config.clock_skew_threshold == 600
    assert config.max_login_attempts == 35
    assert config.request_attempts == 5
    assert config.passwords == (
        "",
        "123456",
        "guest",
        "password",
        "1234",
        "1234567890",
    )


def test_parser_custom_values():
    config = build_config(
        [
            "--port",
            "/dev/cu.usbmodem1101",
            "--output",
            "out.csv",
            "--input",
            "in.csv",
            "--export-xlsx",
            "out.xlsx",
            "--loop",
            "--loop-interval",
            "30",
            "--force",
            "--only",
            "rep",
            "--limit",
            "1",
            "--timeout",
            "10",
            "--request-timeout",
            "30",
            "--min-delay",
            "1",
            "--max-delay",
            "3",
            "--clock-skew-threshold",
            "300",
            "--max-login-attempts",
            "20",
            "--request-attempts",
            "7",
            "--passwords",
            ",foo,bar",
            "--debug",
        ]
    )

    assert config.transport == TransportSpec("serial", "/dev/cu.usbmodem1101")
    assert config.output == Path("out.csv")
    assert config.input == Path("in.csv")
    assert config.export_xlsx == Path("out.xlsx")
    assert config.loop is True
    assert config.loop_interval == 30.0
    assert config.force is True
    assert config.only == "rep"
    assert config.limit == 1
    assert config.timeout == 10.0
    assert config.request_timeout == 30.0
    assert config.min_delay == 1.0
    assert config.max_delay == 3.0
    assert config.clock_skew_threshold == 300
    assert config.max_login_attempts == 20
    assert config.request_attempts == 7
    assert config.passwords == ("", "foo", "bar")
    assert config.debug is True


def test_parser_alive_and_dead_modes():
    assert build_config(["--alive"]).alive is True
    assert build_config(["--dead"]).dead is True
    assert build_config(["--broken"]).broken is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--alive", "--dead"],
        ["--alive", "--broken"],
        ["--dead", "--broken"],
        ["--force", "--alive"],
        ["--force", "--dead"],
        ["--force", "--broken"],
    ],
)
def test_parser_retry_modes_are_mutually_exclusive(argv):
    with pytest.raises(SystemExit):
        build_config(argv)


def test_parser_rejects_non_positive_request_timeout():
    with pytest.raises(SystemExit):
        build_config(["--request-timeout", "0"])


def test_parser_rejects_non_positive_loop_interval():
    with pytest.raises(SystemExit):
        build_config(["--loop-interval", "0"])


def test_parser_rejects_reversed_delays():
    with pytest.raises(SystemExit):
        build_config(["--min-delay", "5", "--max-delay", "2"])


def test_parser_rejects_request_attempts_below_five():
    with pytest.raises(SystemExit):
        build_config(["--request-attempts", "4"])





def test_select_contacts_by_key_prefix_and_name():
    contacts = [contact("a", "Alpha"), contact("b", "Beta")]

    assert select_contacts(contacts, "aaaa") == [contacts[0]]
    assert select_contacts(contacts, "beta") == [contacts[1]]
    assert select_contacts(contacts, None) == contacts


def test_select_contacts_rejects_ambiguous_or_unknown():
    contacts = [contact("a", "Alpha"), contact("a", "Another")]

    with pytest.raises(ValueError, match="Ambiguous"):
        select_contacts(contacts, "a")
    with pytest.raises(ValueError, match="No repeater"):
        select_contacts(contacts, "z")


def test_parser_ble_transport_with_pin():
    config = build_config(["-p", "ble://MeshCore-A1B2", "--ble-pin", "123456"])
    assert config.transport == TransportSpec("ble", "MeshCore-A1B2")
    assert config.ble_pin == "123456"


def test_parser_tcp_transport():
    config = build_config(["-p", "tcp://10.0.0.2:4000"])
    assert config.transport == TransportSpec("tcp", "10.0.0.2", 4000)


def test_parser_rejects_pin_with_explicit_non_ble():
    with pytest.raises(SystemExit):
        build_config(["-p", "tcp://10.0.0.2", "--ble-pin", "123456"])


def test_parser_defers_pin_when_transport_comes_from_env():
    config = build_config(["--ble-pin", "123456"])
    assert config.transport is None
    assert config.ble_pin == "123456"


def test_parser_rejects_unknown_transport_scheme():
    with pytest.raises(SystemExit):
        build_config(["-p", "bluetooth://MeshCore-A1B2"])


@pytest.mark.asyncio
async def test_resolve_transport_returns_explicit_without_discovery(monkeypatch):
    monkeypatch.setattr(cli, "discover_ports", lambda: (_ for _ in ()).throw(AssertionError))
    spec = TransportSpec("tcp", "10.0.0.2", 4000)
    assert await cli._resolve_transport(spec) is spec


@pytest.mark.asyncio
async def test_resolve_transport_honours_meshcore_port_env(monkeypatch):
    monkeypatch.setenv("MESHCORE_PORT", "ble://MeshCore-A1B2")
    monkeypatch.setattr(cli, "discover_ports", lambda: (_ for _ in ()).throw(AssertionError))
    assert await cli._resolve_transport(None) == TransportSpec("ble", "MeshCore-A1B2")


@pytest.mark.asyncio
async def test_resolve_transport_uses_single_serial_candidate(monkeypatch):
    monkeypatch.delenv("MESHCORE_PORT", raising=False)
    monkeypatch.setattr(cli, "discover_ports", lambda: [PortInfo("/dev/cu.usbmodem1101")])

    async def no_ble():
        raise AssertionError("BLE must not be scanned when serial exists")

    monkeypatch.setattr(cli, "discover_ble_devices", no_ble)
    assert await cli._resolve_transport(None) == TransportSpec(
        "serial", "/dev/cu.usbmodem1101"
    )


@pytest.mark.asyncio
async def test_resolve_transport_falls_back_to_single_ble(monkeypatch):
    monkeypatch.delenv("MESHCORE_PORT", raising=False)
    monkeypatch.setattr(cli, "discover_ports", lambda: [])

    async def fake_ble():
        return [BleDeviceInfo("AA", "MeshCore-A1B2", -40)]

    monkeypatch.setattr(cli, "discover_ble_devices", fake_ble)
    assert await cli._resolve_transport(None) == TransportSpec("ble", "AA")


@pytest.mark.asyncio
async def test_resolve_transport_rejects_multiple_serial_ports(monkeypatch):
    monkeypatch.delenv("MESHCORE_PORT", raising=False)
    monkeypatch.setattr(
        cli,
        "discover_ports",
        lambda: [PortInfo("/dev/cu.usbmodem1101"), PortInfo("/dev/cu.usbmodem1201")],
    )
    with pytest.raises(PortError, match="Multiple"):
        await cli._resolve_transport(None)


@pytest.mark.asyncio
async def test_resolve_transport_rejects_multiple_ble_devices(monkeypatch):
    monkeypatch.delenv("MESHCORE_PORT", raising=False)
    monkeypatch.setattr(cli, "discover_ports", lambda: [])

    async def fake_ble():
        return [BleDeviceInfo("AA", "MeshCore-A", -40), BleDeviceInfo("BB", "MeshCore-B", -50)]

    monkeypatch.setattr(cli, "discover_ble_devices", fake_ble)
    with pytest.raises(PortError, match="Multiple MeshCore BLE devices"):
        await cli._resolve_transport(None)


@pytest.mark.asyncio
async def test_resolve_transport_reports_both_serial_and_ble_absence(monkeypatch):
    monkeypatch.delenv("MESHCORE_PORT", raising=False)
    monkeypatch.setattr(
        cli, "discover_ports", lambda: [PortInfo("/dev/cu.usbserial-1420", "USB serial")]
    )

    async def no_ble():
        return []

    monkeypatch.setattr(cli, "discover_ble_devices", no_ble)
    with pytest.raises(PortError, match="No MeshCore Companion found"):
        await cli._resolve_transport(None)


@pytest.mark.asyncio
async def test_resolve_transport_applies_pin_to_env_ble(monkeypatch):
    monkeypatch.setenv("MESHCORE_PORT", "ble://MeshCore-A1B2")
    spec = await cli._resolve_transport(None, "123456")
    assert spec == TransportSpec("ble", "MeshCore-A1B2", pin="123456")


@pytest.mark.asyncio
async def test_resolve_transport_rejects_pin_for_non_ble(monkeypatch):
    monkeypatch.setenv("MESHCORE_PORT", "tcp://10.0.0.2")
    with pytest.raises(ValueError, match="--ble-pin"):
        await cli._resolve_transport(None, "123456")


@pytest.mark.asyncio
async def test_resolve_transport_resolves_bare_ble_to_single_device(monkeypatch):
    async def fake_ble():
        return [BleDeviceInfo("AA", "MeshCore-A", -40)]

    monkeypatch.setattr(cli, "discover_ble_devices", fake_ble)
    assert await cli._resolve_transport(TransportSpec("ble")) == TransportSpec("ble", "AA")


@pytest.mark.asyncio
async def test_resolve_transport_rejects_ambiguous_bare_ble(monkeypatch):
    async def fake_ble():
        return [BleDeviceInfo("AA", "MeshCore-A", -40), BleDeviceInfo("BB", "MeshCore-B", -50)]

    monkeypatch.setattr(cli, "discover_ble_devices", fake_ble)
    with pytest.raises(PortError, match="Multiple MeshCore BLE devices"):
        await cli._resolve_transport(TransportSpec("ble"))


@pytest.mark.asyncio
async def test_list_ble_prints_devices_without_connecting(monkeypatch, capsys):
    async def fake_ble():
        return [BleDeviceInfo("AA", "MeshCore-A1B2", -40)]

    monkeypatch.setattr(cli, "discover_ble_devices", fake_ble)
    code = await cli._async_main(cli.build_config(["--list-ble"]))

    assert code == 0
    assert "MeshCore-A1B2" in capsys.readouterr().out


def test_main_reports_transport_error(monkeypatch, caplog):
    async def boom(config):
        raise TransportError("link lost")

    monkeypatch.setattr(cli, "_async_main", boom)
    assert cli.main([]) == 1
    assert "link lost" in caplog.text
    assert "Fatal error" not in caplog.text


def test_main_reports_contacts_fetch_error(monkeypatch, caplog):
    from meshcore_repeater_info.mesh import ContactsFetchError

    async def boom(config):
        raise ContactsFetchError("contact list timeout")

    monkeypatch.setattr(cli, "_async_main", boom)
    assert cli.main([]) == 1
    assert "contact list timeout" in caplog.text
    assert "Fatal error" not in caplog.text
