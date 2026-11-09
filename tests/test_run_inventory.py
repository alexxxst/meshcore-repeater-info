import random

import pytest

from meshcore_repeater_info.cli import run_inventory
from meshcore_repeater_info.config import Config
from meshcore_repeater_info.mesh import LoginOutcome, OwnerInfo, StatusInfo
from meshcore_repeater_info.model import PollResult
from meshcore_repeater_info.poller import DEFAULT_PASSWORDS, PollConfig, Poller
from meshcore_repeater_info.storage import CsvStore


def contact(key_char: str, name: str) -> dict:
    return {"public_key": key_char * 64, "adv_name": name, "type": 2}


def make_config(output, **overrides) -> Config:
    values = {
        "port": None,
        "baudrate": 115_200,
        "output": output,
        "force": False,
        "alive": False,
        "dead": False,
        "only": None,
        "limit": None,
        "timeout": 15.0,
        "min_delay": 0.0,
        "max_delay": 0.0,
        "clock_skew_threshold": 600,
        "max_login_attempts": 35,
        "request_attempts": 5,
        "passwords": DEFAULT_PASSWORDS,
        "debug": False,
        "list_ports": False,
    }
    values.update(overrides)
    return Config(**values)


class FakeSession:
    def __init__(self, contacts, fail_keys=()):
        self.contacts = contacts
        self.fail_keys = set(fail_keys)
        self.login_contacts: list[str] = []

    async def get_repeater_contacts(self):
        return self.contacts

    async def prepare_flood(self, contact, timeout):
        return True

    def has_route(self, contact):
        return False

    async def login(self, contact, password, timeout):
        key = contact["public_key"]
        if key in self.fail_keys:
            raise RuntimeError("radio exploded")
        self.login_contacts.append(key)
        return LoginOutcome(
            success=True, reason="ok", is_admin=False, fw_ver_level=2
        )

    async def get_owner_info(self, contact, timeout):
        return OwnerInfo("v1.18.0", contact["adv_name"], "Admin")

    async def get_remote_version(self, contact, timeout):
        return None

    async def get_remote_name(self, contact, timeout):
        return None

    async def get_remote_owner_info(self, contact, timeout):
        return None

    async def get_status(self, contact, timeout):
        return StatusInfo(100, 10, 5, -110, 1)

    async def get_neighbours_count(self, contact, timeout):
        return 2

    async def close(self):
        pass


class FakeSleeper:
    async def __call__(self, seconds):
        pass


def make_poller(session, store):
    return Poller(
        session=session,
        config=PollConfig(min_delay=0.0, max_delay=0.0),
        tracker=store,
        sleep=FakeSleeper(),
        now=lambda: 1_700_000_000,
        rng=random.Random(1),
    )


@pytest.mark.asyncio
async def test_run_skips_completed_repeater_and_polls_new_one(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="Alpha",
            status="ok",
            login_attempts_total=0,
        )
    )
    session = FakeSession([contact("a", "Alpha"), contact("b", "Beta")])

    summary = await run_inventory(
        make_config(path), session, store, poller=make_poller(session, store)
    )

    assert summary.skipped == 1
    assert summary.polled == 1
    assert session.login_contacts == ["b" * 64]
    assert store.get("a" * 64)["status"] == "ok"
    assert store.get("b" * 64)["status"] == "ok"


@pytest.mark.asyncio
async def test_limit_counts_actual_polls(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(key="a" * 64, contact_name="Alpha", status="ok")
    )
    session = FakeSession(
        [contact("a", "Alpha"), contact("b", "Beta"), contact("c", "Gamma")]
    )

    summary = await run_inventory(
        make_config(path, limit=1),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 1
    assert session.login_contacts == ["b" * 64]


@pytest.mark.asyncio
async def test_alive_mode_repolls_only_completed_repeaters(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(PollResult(key="a" * 64, contact_name="Alpha", status="ok"))
    store.apply_result(
        PollResult(key="b" * 64, contact_name="Beta", status="timeout")
    )
    session = FakeSession(
        [contact("a", "Alpha"), contact("b", "Beta"), contact("c", "Gamma")]
    )

    summary = await run_inventory(
        make_config(path, alive=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 1
    assert summary.skipped == 2
    assert session.login_contacts == ["a" * 64]


@pytest.mark.asyncio
async def test_dead_mode_repolls_failed_and_new_and_resets_final_fail(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(PollResult(key="a" * 64, contact_name="Alpha", status="ok"))
    store.apply_result(
        PollResult(
            key="b" * 64,
            contact_name="Beta",
            status="final_fail",
            login_attempts_total=35,
        )
    )
    session = FakeSession(
        [contact("a", "Alpha"), contact("b", "Beta"), contact("c", "Gamma")]
    )

    summary = await run_inventory(
        make_config(path, dead=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 2
    assert summary.skipped == 1
    assert session.login_contacts == ["b" * 64, "c" * 64]
    assert store.get("b" * 64)["status"] == "ok"
    assert store.get("b" * 64)["login_attempts_total"] == "0"


@pytest.mark.asyncio
async def test_force_repolls_all_repeaters(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(PollResult(key="a" * 64, contact_name="Alpha", status="ok"))
    store.apply_result(
        PollResult(key="b" * 64, contact_name="Beta", status="timeout")
    )
    session = FakeSession(
        [contact("a", "Alpha"), contact("b", "Beta"), contact("c", "Gamma")]
    )

    summary = await run_inventory(
        make_config(path, force=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 3
    assert session.login_contacts == ["a" * 64, "b" * 64, "c" * 64]


@pytest.mark.asyncio
async def test_force_repolls_completed_repeater(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(key="a" * 64, contact_name="Alpha", status="ok")
    )
    session = FakeSession([contact("a", "Alpha")])

    summary = await run_inventory(
        make_config(path, force=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 1
    assert session.login_contacts == ["a" * 64]


@pytest.mark.asyncio
async def test_final_fail_is_skipped_without_force(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="Alpha",
            status="final_fail",
            login_attempts_total=35,
        )
    )
    session = FakeSession([contact("a", "Alpha")])

    summary = await run_inventory(
        make_config(path), session, store, poller=make_poller(session, store)
    )

    assert summary.skipped == 1
    assert summary.polled == 0
    assert session.login_contacts == []
    assert store.get("a" * 64)["status"] == "final_fail"


@pytest.mark.asyncio
async def test_one_repeater_exception_does_not_stop_the_run(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    session = FakeSession(
        [contact("a", "Alpha"), contact("b", "Beta")], fail_keys={"a" * 64}
    )

    summary = await run_inventory(
        make_config(path), session, store, poller=make_poller(session, store)
    )

    assert summary.polled == 2
    assert store.get("a" * 64)["status"] == "error"
    assert "radio exploded" in store.get("a" * 64)["error"]
    assert store.get("b" * 64)["status"] == "ok"
