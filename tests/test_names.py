import pytest

from meshcore_repeater_info.names import (
    is_removed_repeater_name,
    is_valid_repeater_name,
)


@pytest.mark.parametrize(
    "name",
    [
        "PDO-Strelna-NE",
        "PDO-Strelna-NE-Extra",
        "PDO-Strelna",
        "VYB-Parnas",
        "KRG-SevRuchi",
        "MOS-UGag-East",
        "ABC-Street_N",
        "ABC_Street-N",
        "LO-LOM-BULP",
        "LO-VIR-PETR-360",
        "LO-GAT-Kobralovo-NW",
        "LO-GAT-Kobralovo-NW-Extra",
        "LO-ABC-Street",
        "LO_ABC_Street_N",
        "NEV-Solidar-360☀️",
        "NEV-Solidar-360☀",
    ],
)
def test_valid_repeater_names(name):
    assert is_valid_repeater_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Parnas",
        "AB-Street-N",
        "ABC",
        "ABC-",
        "ABC--Street",
        "NEV-Solidar-360🔥",
        "ПДО-Strelna-NE",
        "ABC-Street N",
        "ABC.Street-N",
        "ABC-Street-N!",
        "PDO-Strelna-NE-Extra-More",
        "LO-GAT-Kobralovo-NW-Extra-More",
        "LO-LOM",
        "LO-AB-Street",
        "LO-ABC",
        "lo-abc-street",
    ],
)
def test_invalid_repeater_names(name):
    assert is_valid_repeater_name(name) is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("remove-me", True),
        ("Me-Remove", True),
        ("DELETE-repeater", True),
        ("my-deleted-node", True),
        ("PDO-Strelna-NE", False),
        ("", False),
    ],
)
def test_removed_repeater_names(name, expected):
    assert is_removed_repeater_name(name) is expected
