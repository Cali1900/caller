"""
Configuration, read once from the environment.

Everything required is REQUIRED - a missing value raises. There are no
defaults that quietly stand in for a real value, for the same reason the
compose file has none.

The ONE deliberate default is DIAL_MODE when the variable is entirely ABSENT:
it becomes 'allowlist'. That is not a silent fallback to a stale value, it is
failing in the safe direction - a misconfigured box must be inert rather than
loose. Note the asymmetry: absent -> 'allowlist' (safe), but EMPTY -> stays
empty and the guard refuses it as an unknown mode. Empty is garbage, not a
signal to relax.
"""

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """A required setting is missing. Never defaulted around."""


def _required(name: str) -> str:
    v = os.getenv(name)
    if v is None or v == '':
        raise ConfigError(
            f'{name} is not set. There is deliberately no default - '
            f'a stale fallback looks exactly like success.'
        )
    return v


@dataclass(frozen=True)
class Config:
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str

    DIAL_MODE: str
    DIAL_ALLOWLIST: frozenset

    RETELL_API_KEY: str
    RETELL_FROM_NUMBER: str
    AGENT_L1: str
    AGENT_L1_VERSION: int


def parse_allowlist(raw: str | None) -> frozenset:
    """Comma-separated E.164. Empty means empty, which dials nothing."""
    if not raw:
        return frozenset()
    return frozenset(p.strip() for p in raw.split(',') if p.strip())


def load_config() -> Config:
    mode = os.getenv('DIAL_MODE')
    # Absent -> allowlist (fail closed). Empty string is NOT absent.
    mode = 'allowlist' if mode is None else mode

    return Config(
        DB_HOST=_required('CALLER_DB_HOST'),
        DB_PORT=int(_required('CALLER_DB_PORT')),
        DB_NAME=_required('CALLER_DB_NAME'),
        DB_USER=_required('CALLER_DB_USER'),
        DB_PASSWORD=_required('CALLER_DB_PASSWORD'),
        DIAL_MODE=mode,
        DIAL_ALLOWLIST=parse_allowlist(os.getenv('DIAL_ALLOWLIST')),
        RETELL_API_KEY=_required('RETELL_API_KEY'),
        RETELL_FROM_NUMBER=_required('RETELL_FROM_NUMBER'),
        AGENT_L1=_required('AGENT_L1'),
        AGENT_L1_VERSION=int(_required('AGENT_L1_VERSION')),
    )
