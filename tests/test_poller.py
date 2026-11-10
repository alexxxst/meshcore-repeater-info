import random

import pytest

from meshcore_repeater_info.mesh import LoginOutcome, OwnerInfo, StatusInfo
from meshcore_repeater_info.poller import (
    PollConfig,
    Poller,
    RequestResult,
    RouteMode,
    build_login_candidates,
)


CONTACT = {"public_key": "a" * 64, "adv_name": "Contact Name", "type": 2}


def make_config(**overrides) -> PollConfig:
    values = {
        "timeout": 15.0,
        "request_timeout": 25.0,
        "min_delay": 20.0,
        "max_delay": 30.0,
        "clock_skew_threshold": 600,
        "max_login_attempts": 35,
        "request_attempts": 5,
    }
    values.update(overrides)
    return PollConfig(**values)


class FakeSession:
    def __init__(
        self,
        login_results: dict[str, LoginOutcome] | None = None,
        *,
        login_sequence: list[LoginOutcome] | None = None,
        route_after_flood: bool = False,
        owner: OwnerInfo | None = None,
        owner_results: list[OwnerInfo | None] | None = None,
        remote_version: str | None = None,
        remote_version_results: list[str | None] | None = None,
        remote_name: str | None = None,
        remote_name_results: list[str | None] | None = None,
        remote_owner: str | None = None,
        remote_owner_results: list[str | None] | None = None,
        status: StatusInfo | None = None,
        status_results: list[StatusInfo | None] | None = None,
        neighbours: int | None = 3,
        neighbours_results: list[int | None] | None = None,
    ):
        self.login_results = login_results or {}
        self.login_sequence = login_sequence
        self.route_after_flood = route_after_flood
        self.owner = owner
        self.owner_results = owner_results
        self.remote_version = remote_version
        self.remote_version_results = remote_version_results
        self.remote_name = remote_name
        self.remote_name_results = remote_name_results
        self.remote_owner = remote_owner
        self.remote_owner_results = remote_owner_results
        self.status = status
        self.status_results = status_results
        self.neighbours = neighbours
        self.neighbours_results = neighbours_results

        self.has_route_flag = False
        self.prepare_flood_calls = 0
        self.prepare_flood_timeouts: list[float] = []
        self.login_calls: list[str] = []
        self.login_timeouts: list[float] = []
        self.owner_calls = 0
        self.owner_timeouts: list[float] = []
        self.remote_version_calls = 0
        self.remote_name_calls = 0
        self.remote_owner_calls = 0
        self.status_calls = 0
        self.status_timeouts: list[float] = []
        self.neighbours_calls = 0
        self.neighbours_timeouts: list[float] = []

    async def prepare_flood(self, contact, timeout):
        self.prepare_flood_calls += 1
        self.prepare_flood_timeouts.append(timeout)
        self.has_route_flag = self.route_after_flood
        return True

    def has_route(self, contact):
        return self.has_route_flag

    async def login(self, contact, password, timeout):
        self.login_calls.append(password)
        self.login_timeouts.append(timeout)
        if self.login_sequence is not None:
            return self.login_sequence.pop(0)
        return self.login_results.get(
            password, LoginOutcome(success=False, reason="rejected")
        )

    async def get_owner_info(self, contact, timeout):
        self.owner_calls += 1
        self.owner_timeouts.append(timeout)
        if self.owner_results:
            return self.owner_results.pop(0)
        return self.owner

    async def get_remote_version(self, contact, timeout):
        self.remote_version_calls += 1
        if self.remote_version_results:
            return self.remote_version_results.pop(0)
        return self.remote_version

    async def get_remote_name(self, contact, timeout):
        self.remote_name_calls += 1
        if self.remote_name_results:
            return self.remote_name_results.pop(0)
        return self.remote_name

    async def get_remote_owner_info(self, contact, timeout):
        self.remote_owner_calls += 1
        if self.remote_owner_results:
            return self.remote_owner_results.pop(0)
        return self.remote_owner

    async def get_status(self, contact, timeout):
        self.status_calls += 1
        self.status_timeouts.append(timeout)
        if self.status_results:
            return self.status_results.pop(0)
        return self.status

    async def get_neighbours_count(self, contact, timeout):
        self.neighbours_calls += 1
        self.neighbours_timeouts.append(timeout)
        if self.neighbours_results:
            return self.neighbours_results.pop(0)
        return self.neighbours


class FakeTracker:
    def __init__(self):
        self.attempts: list[tuple[str, int, int]] = []
        self.successes: list[tuple[str, int, bool, str, str]] = []

    def record_attempt(self, key, total, last):
        self.attempts.append((key, total, last))

    def record_login_success(self, key, last, is_admin, login_password, login_method):
        self.successes.append((key, last, is_admin, login_password, login_method))


class FakeSleeper:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


def successful_status() -> StatusInfo:
    return StatusInfo(
        uptime_seconds=90_061,
        tx_airtime_seconds=45_030,
        rx_airtime_seconds=9_006,
        noise_floor_dbm=-115,
        debug_flags=2,
    )


def make_poller(session, tracker, sleeper, now=1_700_000_000, **config):
    return Poller(
        session=session,
        config=make_config(**config),
        tracker=tracker,
        sleep=sleeper,
        now=lambda: now,
        rng=random.Random(1),
    )


def test_request_transport_label_is_path_not_direct():
    assert RequestResult(None, 1, False, RouteMode.PATH).transport == "path"
    assert RequestResult(None, 1, True, RouteMode.FLOOD).transport == "flood"


def test_build_login_candidates_default_order_and_counts():
    candidates = build_login_candidates(None, make_config())

    assert candidates[:4] == ["", "", "", "123456"]
    assert candidates.count("") == 3
    assert candidates.count("123456") == 2
    assert candidates.count("guest") == 2
    assert candidates[7:9] == ["password", "password"]
    assert candidates.count("1234") == 2
    assert "admin" not in candidates
    assert candidates.count("1234567890") == 2
    assert candidates.count("password") == 2
    assert len(candidates) == 13


def test_build_login_candidates_moves_saved_password_after_empty():
    candidates = build_login_candidates("admin", make_config())

    assert candidates[:4] == ["", "", "", "admin"]
    assert candidates.count("admin") == 2


@pytest.mark.asyncio
async def test_startup_is_flood_then_switches_to_path_after_route_learned():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=True,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    assert session.prepare_flood_calls == 1  # only the initial login
    assert session.login_calls == [""]
    assert session.owner_calls == 1
    assert session.status_calls == 1
    assert session.neighbours_calls == 1
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_no_route_keeps_flooding_every_request():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    assert session.prepare_flood_calls == 4  # login, owner, status, neighbours
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_login_uses_15s_and_post_login_requests_use_25s():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    await make_poller(
        session, FakeTracker(), FakeSleeper(), timeout=15.0, request_timeout=25.0
    ).poll(CONTACT)

    assert session.login_timeouts == [15.0]
    assert session.owner_timeouts == [25.0]
    assert session.status_timeouts == [25.0]
    assert session.neighbours_timeouts == [25.0]
    assert session.prepare_flood_timeouts == [15.0, 25.0, 25.0, 25.0]


@pytest.mark.asyncio
async def test_saved_password_is_tried_after_three_empty_attempts():
    session = FakeSession(
        {
            "admin": LoginOutcome(
                success=True, reason="ok", is_admin=True, fw_ver_level=2
            ),
        },
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(
        CONTACT, {"login_password": "admin", "login_attempts_total": "0"}
    )

    assert session.login_calls == ["", "", "", "admin"]
    assert result.is_admin is True
    assert result.login_password == "admin"
    assert result.login_method == "password"


@pytest.mark.asyncio
async def test_empty_login_preserves_previously_known_password():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=True, fw_ver_level=2)},
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(
        CONTACT, {"login_password": "admin", "login_attempts_total": "0"}
    )

    assert session.login_calls == [""]
    assert result.is_admin is True
    assert result.login_password == "admin"
    assert result.login_method == "empty"


@pytest.mark.asyncio
async def test_login_rejected_after_flood_switches_next_password_to_path():
    sequence = [
        LoginOutcome(success=False, reason="rejected"),
        LoginOutcome(success=False, reason="rejected"),
        LoginOutcome(success=False, reason="rejected"),
        LoginOutcome(success=True, reason="ok", is_admin=True, fw_ver_level=2),
    ]
    session = FakeSession(
        login_sequence=sequence,
        route_after_flood=True,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    # first login is flood; the next three password attempts use the path
    assert session.prepare_flood_calls == 1
    assert session.login_calls == ["", "", "", "123456"]
    assert result.is_admin is True


@pytest.mark.asyncio
async def test_path_login_timeout_falls_back_to_flood_for_rest_of_session():
    sequence = [
        LoginOutcome(success=False, reason="rejected"),  # flood, route learned
        LoginOutcome(success=False, reason="timeout"),  # path, failed
        LoginOutcome(success=False, reason="rejected"),  # flood
        LoginOutcome(success=True, reason="ok", is_admin=True, fw_ver_level=2),
    ]
    session = FakeSession(
        login_sequence=sequence,
        route_after_flood=True,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    # first flood, path timeout, then flood for the remaining attempts and
    # for owner info, status and neighbours
    assert session.prepare_flood_calls == 6
    assert result.is_admin is True


@pytest.mark.asyncio
async def test_final_fail_after_total_attempt_limit():
    session = FakeSession({}, route_after_flood=False)
    tracker = FakeTracker()

    result = await make_poller(session, tracker, FakeSleeper()).poll(
        CONTACT, {"login_attempts_total": "30", "login_password": ""}
    )

    assert len(session.login_calls) == 5
    assert tracker.attempts[-1] == ("a" * 64, 35, 5)
    assert result.status == "final_fail"
    assert result.login_attempts_total == 35
    assert "35" in result.error


@pytest.mark.asyncio
async def test_old_firmware_marks_version_and_skips_owner_info():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=1)},
        route_after_flood=False,
        owner=OwnerInfo("should-not-be-used", "Wrong", "Wrong"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    assert session.owner_calls == 0
    assert result.firmware_version == "старая (FW < 1.12)"
    assert result.repeater_name is None
    assert result.status == "ok_old"
    assert result.is_admin is False


@pytest.mark.asyncio
async def test_status_retries_five_times_and_becomes_partial():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=None,
        status_results=[None, None, None, None, None],
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    assert session.status_calls == 5
    assert result.status == "partial"
    assert "status (5 attempts, flood)" in result.error


@pytest.mark.asyncio
async def test_path_request_failure_switches_to_flood_for_rest_of_session():
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=True,
        owner=None,
        owner_results=[None, None, None, None, None],
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    # login flood -> path; owner path fails, 4 flood retries; status+neighbours flood
    assert session.owner_calls == 5
    assert session.prepare_flood_calls == 7
    assert result.status == "partial"
    assert "owner info (5 attempts, flood)" in result.error


@pytest.mark.asyncio
async def test_request_succeeds_on_fifth_attempt_without_touching_login_counter():
    owner = OwnerInfo("v1.18.0", "Repeater", "Admin")
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=False,
        owner_results=[None, None, None, None, owner],
        status=successful_status(),
    )
    tracker = FakeTracker()

    result = await make_poller(session, tracker, FakeSleeper()).poll(CONTACT)

    assert session.owner_calls == 5
    assert result.status == "ok"
    assert result.login_attempts_last == 1
    assert result.login_attempts_total == 0
    assert tracker.attempts == [("a" * 64, 1, 1)]


@pytest.mark.asyncio
async def test_admin_session_uses_remote_version_name_and_owner_fallbacks():
    session = FakeSession(
        {
            "password": LoginOutcome(
                success=True, reason="ok", is_admin=True, fw_ver_level=2
            )
        },
        route_after_flood=False,
        owner_results=[None, None, None, None, None],
        remote_version="v1.17.0",
        remote_name="Repeater",
        remote_owner="Admin: @x",
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(CONTACT)

    assert session.owner_calls == 5
    assert session.remote_version_calls == 1
    assert session.remote_name_calls == 1
    assert session.remote_owner_calls == 1
    assert result.firmware_version == "v1.17.0"
    assert result.repeater_name == "Repeater"
    assert result.owner_info == "Admin: @x"
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_last_advert_timestamp_is_passed_through():
    contact = {**CONTACT, "last_advert": 1_700_000_000}
    session = FakeSession(
        {"": LoginOutcome(success=True, reason="ok", is_admin=False, fw_ver_level=2)},
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(session, FakeTracker(), FakeSleeper()).poll(contact)

    assert result.advert_timestamp == 1_700_000_000


@pytest.mark.asyncio
async def test_clock_skew_is_flagged_when_over_ten_minutes():
    session = FakeSession(
        {
            "": LoginOutcome(
                success=True,
                reason="ok",
                is_admin=False,
                fw_ver_level=2,
                server_timestamp=1_700_000_000,
            )
        },
        route_after_flood=False,
        owner=OwnerInfo("v1.18.0", "Repeater", "Admin"),
        status=successful_status(),
    )

    result = await make_poller(
        session, FakeTracker(), FakeSleeper(), now=1_700_000_601
    ).poll(CONTACT)

    assert result.login_timestamp == 1_700_000_000
    assert result.clock_skew_seconds == -601
    assert result.clock_status == "suspect"
