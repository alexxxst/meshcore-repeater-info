"""Validation of repeater names according to the MeshCore SPb naming rules."""

from __future__ import annotations

import re

_ALLOWED = re.compile(r"^[A-Za-z0-9_\-☀\uFE0F]+$")
_CODE = re.compile(r"^[A-Z0-9]{3}$")


def is_valid_repeater_name(name: str) -> bool:
    """Return True when a name matches the city or region naming template.

    City:   XXX-Name[-Direction]       (minimum 2 blocks)
    Region: LO-XXX-Name[-Direction]    (minimum 3 blocks)
    """
    if not name or not _ALLOWED.fullmatch(name):
        return False

    parts = [part for part in re.split(r"[-_]", name) if part]
    if len(parts) != len(re.split(r"[-_]", name)):
        return False  # empty block, e.g. "ABC--Street"

    if parts[0] == "LO":
        return 3 <= len(parts) <= 5 and bool(_CODE.fullmatch(parts[1]))
    return 2 <= len(parts) <= 4 and bool(_CODE.fullmatch(parts[0]))
