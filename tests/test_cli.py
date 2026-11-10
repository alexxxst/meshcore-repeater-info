from pathlib import Path

import pytest

from meshcore_repeater_info.cli import build_config, select_contacts


def contact(key_char: str, name: str) -> dict:
    return {"public_key": key_char * 64, "adv_name": name, "type": 2}


def test_parser_defaults():
    config = build_config([])

    assert config.port is None
    assert config.baudrate == 115_200
    assert config.output == Path("repeaters.csv")
    assert config.force is False
    assert config.alive is False
    assert config.dead is False
    assert config.broken is False
    assert config.input is None
    assert config.export_xlsx is None
    assert config.loop is False
    assert config.loop_interval == 60.0
    assert config.timeout == 25.0
    assert config.min_delay == 20.0
    assert config.max_delay == 30.0
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

    assert config.port == "/dev/cu.usbmodem1101"
    assert config.output == Path("out.csv")
    assert config.input == Path("in.csv")
    assert config.export_xlsx == Path("out.xlsx")
    assert config.loop is True
    assert config.loop_interval == 30.0
    assert config.force is True
    assert config.only == "rep"
    assert config.limit == 1
    assert config.timeout == 10.0
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
