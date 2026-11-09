"""CLI configuration values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Config:
    port: str | None
    baudrate: int
    output: Path
    force: bool
    alive: bool
    dead: bool
    only: str | None
    limit: int | None
    timeout: float
    min_delay: float
    max_delay: float
    clock_skew_threshold: int
    max_login_attempts: int
    request_attempts: int
    passwords: tuple[str, ...]
    debug: bool
    list_ports: bool
