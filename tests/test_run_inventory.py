import random

import pytest

from meshcore_repeater_info.cli import _async_main, _run_loop, run_inventory
from meshcore_repeater_info.formatting import format_utc
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
        "input": None,
        "export_xlsx": None,
        "loop": False,
        "loop_interval": 60.0,
        "force": False,
        "alive": False,
        "dead": False,
        "broken": False,
        "only": None,
        "limit": None,
        "timeout": 15.0,
        "request_timeout": 25.0,
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
async def test_broken_mode_repolls_existing_broken_rows_only(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(PollResult(key="a" * 64, contact_name="Alpha", status="ok"))
    store.apply_result(
        PollResult(key="b" * 64, contact_name="Beta", status="timeout")
    )
    store.apply_result(
        PollResult(
            key="c" * 64,
            contact_name="Gamma",
            status="final_fail",
            login_attempts_total=35,
        )
    )
    session = FakeSession(
        [
            contact("a", "Alpha"),
            contact("b", "Beta"),
            contact("c", "Gamma"),
            contact("d", "Delta"),
        ]
    )

    summary = await run_inventory(
        make_config(path, broken=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 2
    assert summary.skipped == 2
    assert session.login_contacts == ["b" * 64, "c" * 64]
    assert store.get("c" * 64)["status"] == "ok"
    assert store.get("c" * 64)["login_attempts_total"] == "0"
    assert store.get("d" * 64) is None


@pytest.mark.asyncio
async def test_export_mode_does_not_connect_to_radio(tmp_path):
    csv_path = tmp_path / "report.csv"
    xlsx_path = tmp_path / "report.xlsx"
    store = CsvStore(csv_path)
    store.load()
    store.apply_result(
        PollResult(key="a" * 64, contact_name="Alpha", status="ok")
    )

    code = await _async_main(
        make_config(
            csv_path,
            input=csv_path,
            export_xlsx=xlsx_path,
        )
    )

    assert code == 0
    assert xlsx_path.exists()


@pytest.mark.asyncio
async def test_remove_or_delete_names_are_skipped_and_rows_deleted(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(PollResult(key="a" * 64, contact_name="remove-me", status="ok"))
    store.apply_result(PollResult(key="b" * 64, contact_name="Beta", status="timeout"))
    session = FakeSession(
        [contact("a", "REMOVE-me"), contact("b", "Beta")]
    )

    summary = await run_inventory(
        make_config(path),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert store.get("a" * 64) is None
    assert store.get("b" * 64) is not None
    assert session.login_contacts == ["b" * 64]
    assert summary.skipped == 1
    assert summary.polled == 1


@pytest.mark.asyncio
async def test_run_loop_makes_passes_and_sleeps_between_them(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    session = FakeSession([contact("a", "Alpha")])
    connects = 0
    sleeps: list[float] = []

    async def connect():
        nonlocal connects
        connects += 1
        return session

    async def sleep(seconds):
        sleeps.append(seconds)

    await _run_loop(
        make_config(path, loop=True, loop_interval=60),
        store,
        connect,
        sleep=sleep,
        max_passes=2,
    )

    assert connects == 1
    assert sleeps == [60]
    assert session.login_contacts == ["a" * 64]
    assert store.get("a" * 64)["status"] == "ok"


@pytest.mark.asyncio
async def test_loop_repolls_ok_repeater_when_advert_is_over_a_day_newer(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="Alpha",
            status="ok",
            advert_timestamp=1_000,
        )
    )
    newer = 1_000 + 86_401
    session = FakeSession([{**contact("a", "Alpha"), "last_advert": newer}])

    summary = await run_inventory(
        make_config(path, loop=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 1
    assert session.login_contacts == ["a" * 64]
    assert store.get("a" * 64)["last_advert_utc"] == format_utc(newer)


@pytest.mark.asyncio
async def test_loop_does_not_repoll_for_slightly_newer_advert(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="Alpha",
            status="ok",
            advert_timestamp=1_000,
        )
    )
    session = FakeSession([{**contact("a", "Alpha"), "last_advert": 1_000 + 3_600}])

    summary = await run_inventory(
        make_config(path, loop=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 0
    assert summary.skipped == 1
    assert session.login_contacts == []


@pytest.mark.asyncio
async def test_loop_skips_ok_repeater_when_advert_is_older(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="a" * 64,
            contact_name="Alpha",
            status="ok",
            advert_timestamp=2_000,
        )
    )
    session = FakeSession([{**contact("a", "Alpha"), "last_advert": 1_000}])

    summary = await run_inventory(
        make_config(path, loop=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 0
    assert summary.skipped == 1
    assert session.login_contacts == []


@pytest.mark.asyncio
async def test_loop_alive_mode_does_not_promote_broken_rows(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="b" * 64,
            contact_name="Beta",
            status="timeout",
            advert_timestamp=1_000,
        )
    )
    session = FakeSession([{**contact("b", "Beta"), "last_advert": 87_401}])

    summary = await run_inventory(
        make_config(path, loop=True, alive=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 0
    assert summary.skipped == 1


@pytest.mark.asyncio
async def test_loop_broken_mode_repolls_final_fail_with_newer_advert(tmp_path):
    path = tmp_path / "report.csv"
    store = CsvStore(path)
    store.load()
    store.apply_result(
        PollResult(
            key="c" * 64,
            contact_name="Gamma",
            status="final_fail",
            login_attempts_total=35,
            advert_timestamp=1_000,
        )
    )
    session = FakeSession([{**contact("c", "Gamma"), "last_advert": 87_401}])

    summary = await run_inventory(
        make_config(path, loop=True, broken=True),
        session,
        store,
        poller=make_poller(session, store),
    )

    assert summary.polled == 1
    assert session.login_contacts == ["c" * 64]
    assert store.get("c" * 64)["status"] == "ok"
    assert store.get("c" * 64)["login_attempts_total"] == "0"


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
