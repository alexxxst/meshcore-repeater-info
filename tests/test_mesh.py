import struct

import pytest

from meshcore_repeater_info.mesh import MeshCoreSession, RepeaterRequest


CONTACT = {"public_key": "a" * 64, "adv_name": "R", "type": 2}


class PayloadSession(MeshCoreSession):
    def __init__(self, payload):
        super().__init__(mc=None)
        self.payload = payload
        self.requests: list[tuple[int, bytes]] = []

    async def _binary_request(self, contact, request_type, timeout, data=b""):
        self.requests.append((int(request_type), data))
        return self.payload


@pytest.mark.asyncio
async def test_owner_info_parses_three_lines_and_keeps_owner_newlines():
    session = PayloadSession(
        b"v1.18.0\nMy Repeater\nAdmin: @x\nPhone: 123\x00"
    )

    owner = await session.get_owner_info(CONTACT, 9.0)

    assert owner.firmware_version == "v1.18.0"
    assert owner.name == "My Repeater"
    assert owner.owner_info == "Admin: @x\nPhone: 123"
    assert session.requests == [(0x07, b"")]


@pytest.mark.asyncio
async def test_owner_info_empty_payload_is_none():
    session = PayloadSession(b"")
    assert await session.get_owner_info(CONTACT, 9.0) is None


@pytest.mark.asyncio
async def test_status_maps_binary_fields():
    data = bytearray(52)
    struct.pack_into("<H", data, 0, 5_000)
    struct.pack_into("<h", data, 4, -115)
    struct.pack_into("<I", data, 16, 45_030)
    struct.pack_into("<I", data, 20, 90_061)
    struct.pack_into("<H", data, 40, 2)
    struct.pack_into("<I", data, 48, 9_006)
    session = PayloadSession(bytes(data))

    status = await session.get_status(CONTACT, 9.0)

    assert status.uptime_seconds == 90_061
    assert status.tx_airtime_seconds == 45_030
    assert status.rx_airtime_seconds == 9_006
    assert status.noise_floor_dbm == -115
    assert status.debug_flags == 2
    assert session.requests[0][0] == 0x01


@pytest.mark.asyncio
async def test_short_status_payload_is_none():
    status_session = PayloadSession(b"\x00\x01")
    assert await status_session.get_status(CONTACT, 9.0) is None


class DummySubscription:
    def unsubscribe(self):
        pass


class FakeCommands:
    def __init__(self, mc=None):
        self.mc = mc
        self.request_type = None
        self.data = None
        self.reset_calls = 0
        self.sent_data = None
        self.login_event = None
        self.contacts_called = 0
        self.contacts_lastmod = None
        self.contacts_payload = None
        self.contacts_silent = False
        self.contacts_error = None

    async def get_contacts_async(self, lastmod=0):
        from meshcore.events import Event, EventType

        self.contacts_called += 1
        self.contacts_lastmod = lastmod
        if self.contacts_silent:
            return
        if self.contacts_error is not None:
            for handler in self.mc.handlers.get(EventType.ERROR, []):
                handler(Event(EventType.ERROR, self.contacts_error))
            return
        payload = {
            contact["public_key"]: contact
            for contact in (self.contacts_payload or [])
        }
        self.mc.contacts = payload
        for contact in payload.values():
            for handler in self.mc.handlers.get(EventType.NEXT_CONTACT, []):
                handler(Event(EventType.NEXT_CONTACT, contact))
        for handler in self.mc.handlers.get(EventType.CONTACTS, []):
            handler(Event(EventType.CONTACTS, payload, {"lastmod": 1}))

    async def send(self, data, expected_events=None, timeout=None):
        from meshcore.events import Event, EventType

        self.sent_data = bytes(data)
        if self.login_event is not None and self.mc is not None:
            for handler in self.mc.handlers.get(EventType.LOGIN_SUCCESS, []):
                handler(self.login_event)
        return Event(EventType.MSG_SENT, {})

    async def send_binary_req(
        self, contact, request_type, data=b"", timeout=None, min_timeout=0
    ):
        from meshcore.events import Event, EventType

        self.request_type = request_type
        self.data = data
        return Event(EventType.MSG_SENT, {"expected_ack": b"\x01\x02\x03\x04"})

    async def reset_path(self, contact):
        from meshcore.events import Event, EventType

        self.reset_calls += 1
        return Event(EventType.OK, {})

    async def send_cmd(self, contact, command):
        from meshcore.events import Event, EventType

        return Event(EventType.MSG_SENT, {})


class FakeMC:
    def __init__(self):
        self.handlers: dict = {}
        self.filters: dict = {}
        self.contacts: dict = {}
        self.commands = FakeCommands(self)

    def subscribe(self, event_type, handler, attribute_filters=None):
        self.handlers.setdefault(event_type, []).append(handler)
        self.filters[event_type] = attribute_filters
        return DummySubscription()

    async def start_auto_message_fetching(self):
        return DummySubscription()

    async def stop_auto_message_fetching(self):
        pass


@pytest.mark.asyncio
async def test_binary_request_passes_an_enum_to_meshcore():
    session = MeshCoreSession(FakeMC())

    await session._binary_request(CONTACT, RepeaterRequest.STATUS, 0.01)

    assert session.mc.commands.request_type.value == 0x01
    assert session.mc.commands.data == b""


@pytest.mark.asyncio
async def test_login_sends_raw_command_and_parses_success():
    from meshcore.events import Event, EventType

    mc = FakeMC()
    mc.commands.login_event = Event(
        EventType.LOGIN_SUCCESS,
        {"is_admin": True, "server_timestamp": 123, "fw_ver_level": 2},
    )
    session = MeshCoreSession(mc)

    outcome = await session.login(CONTACT, "secret", 1.0)

    assert mc.commands.sent_data == b"\x1a" + bytes.fromhex("a" * 64) + b"secret"
    assert outcome.success
    assert outcome.is_admin is True
    assert outcome.server_timestamp == 123
    assert outcome.fw_ver_level == 2


@pytest.mark.asyncio
async def test_remote_cli_accepts_only_cli_data_messages():
    from meshcore.events import EventType

    mc = FakeMC()
    session = MeshCoreSession(mc)

    assert await session._remote_cli(CONTACT, "ver", 0.01) is None
    assert mc.filters[EventType.CONTACT_MSG_RECV] == {
        "pubkey_prefix": "a" * 12,
        "txt_type": 1,
    }


@pytest.mark.asyncio
async def test_path_update_marks_route_and_prepare_flood_clears_it():
    from meshcore.events import Event, EventType

    mc = FakeMC()
    session = MeshCoreSession(mc)
    handler = mc.handlers[EventType.PATH_UPDATE][0]
    handler(Event(EventType.PATH_UPDATE, {"public_key": "a" * 64}))

    assert session.has_route(CONTACT)

    assert await session.prepare_flood(CONTACT, 1.0)
    assert not session.has_route(CONTACT)
    assert mc.commands.reset_calls == 1
    assert CONTACT["out_path_len"] == -1


@pytest.mark.asyncio
async def test_connect_ble_passes_address_and_pin(monkeypatch):
    import meshcore

    calls = {}

    class FakeMc:
        is_connected = True

        async def disconnect(self):
            pass

    async def fake_create_ble(address=None, client=None, device=None, pin=None, debug=False):
        calls.update(address=address, pin=pin, debug=debug)
        return FakeMc()

    monkeypatch.setattr(
        meshcore.MeshCore, "create_ble", staticmethod(fake_create_ble)
    )
    session = await MeshCoreSession.connect_ble("MeshCore-A1B2", "123456", True)

    assert calls == {"address": "MeshCore-A1B2", "pin": "123456", "debug": True}
    assert session.is_connected() is True


@pytest.mark.asyncio
async def test_connect_ble_failure_raises(monkeypatch):
    import meshcore

    async def fake_create_ble(address=None, client=None, device=None, pin=None, debug=False):
        return None

    monkeypatch.setattr(
        meshcore.MeshCore, "create_ble", staticmethod(fake_create_ble)
    )
    with pytest.raises(RuntimeError, match="BLE"):
        await MeshCoreSession.connect_ble(None)


@pytest.mark.asyncio
async def test_connect_tcp_passes_host_and_port(monkeypatch):
    import meshcore

    calls = {}

    class FakeMc:
        is_connected = True

        async def disconnect(self):
            pass

    async def fake_create_tcp(host, port, debug=False):
        calls.update(host=host, port=port, debug=debug)
        return FakeMc()

    monkeypatch.setattr(
        meshcore.MeshCore, "create_tcp", staticmethod(fake_create_tcp)
    )
    session = await MeshCoreSession.connect_tcp("10.0.0.2", 4000, True)

    assert calls == {"host": "10.0.0.2", "port": 4000, "debug": True}
    assert session.is_connected() is True


def test_is_connected_is_false_without_transport():
    assert MeshCoreSession(mc=None).is_connected() is False


@pytest.mark.asyncio
async def test_get_repeater_contacts_fetches_full_list_when_cache_empty():
    mc = FakeMC()
    mc.commands.contacts_payload = [
        {"public_key": "a" * 64, "adv_name": "R1", "type": 2},
        {"public_key": "b" * 64, "adv_name": "R2", "type": 2},
        {"public_key": "c" * 64, "adv_name": "Chat", "type": 1},
    ]
    session = MeshCoreSession(mc)

    contacts = await session.get_repeater_contacts()

    assert [contact["adv_name"] for contact in contacts] == ["R1", "R2"]
    assert mc.commands.contacts_called == 1
    assert mc.commands.contacts_lastmod == 0


@pytest.mark.asyncio
async def test_get_repeater_contacts_uses_cache_without_refetch():
    mc = FakeMC()
    mc.contacts = {
        "a" * 64: {"public_key": "a" * 64, "adv_name": "R1", "type": 2}
    }
    session = MeshCoreSession(mc)

    contacts = await session.get_repeater_contacts()

    assert [contact["adv_name"] for contact in contacts] == ["R1"]
    assert mc.commands.contacts_called == 0


@pytest.mark.asyncio
async def test_get_repeater_contacts_stalls_with_clear_error(monkeypatch):
    import meshcore_repeater_info.mesh as mesh_module

    monkeypatch.setattr(mesh_module, "CONTACTS_STALL_TIMEOUT", 0.01)
    mc = FakeMC()
    mc.commands.contacts_silent = True
    session = MeshCoreSession(mc)

    with pytest.raises(mesh_module.ContactsFetchError, match="contact list"):
        await session.get_repeater_contacts()


@pytest.mark.asyncio
async def test_get_repeater_contacts_propagates_companion_error():
    from meshcore_repeater_info.mesh import ContactsFetchError

    mc = FakeMC()
    mc.commands.contacts_error = {"reason": "busy"}
    session = MeshCoreSession(mc)

    with pytest.raises(ContactsFetchError, match="busy"):
        await session.get_repeater_contacts()
