"""
Tests for THE DIAL GUARD.

The standard here: remove the guard, watch a test go red, put it back. If
removing the guard fails nothing, the test is decoration. See the break-pass
in scripts/break_pass.sh, which does exactly that mechanically and reports
whether the edit actually landed before reading the result.
"""

import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.config import parse_allowlist  # noqa: E402
from api.guards import DialRefused, assert_dialable  # noqa: E402

MINE = '+15551234567'
STRANGER = '+12125550100'


@dataclass(frozen=True)
class Cfg:
    """Minimal stand-in. The guard is pure, so it needs nothing else."""
    DIAL_MODE: object
    DIAL_ALLOWLIST: frozenset = frozenset()


# --- the phase-0 condition: an empty allowlist dials NOTHING ---------------

def test_empty_allowlist_refuses_every_number():
    cfg = Cfg(DIAL_MODE='allowlist', DIAL_ALLOWLIST=frozenset())
    for number in (MINE, STRANGER, '+447700900000', '+15550000000'):
        with pytest.raises(DialRefused):
            assert_dialable(number, cfg)


def test_allowlisted_number_is_permitted():
    cfg = Cfg(DIAL_MODE='allowlist', DIAL_ALLOWLIST=frozenset({MINE}))
    assert_dialable(MINE, cfg)          # must not raise


def test_number_off_the_allowlist_is_refused():
    cfg = Cfg(DIAL_MODE='allowlist', DIAL_ALLOWLIST=frozenset({MINE}))
    with pytest.raises(DialRefused):
        assert_dialable(STRANGER, cfg)


# --- fail closed on anything that is not exactly a known mode -------------

@pytest.mark.parametrize('mode', [
    'garbage', '', ' ', 'ALLOWLIST', 'Unrestricted', 'unrestricted ',
    None, 0, 'allow_list', 'prod',
])
def test_unknown_mode_refuses(mode):
    """Anything that is not exactly 'allowlist'/'unrestricted' must refuse."""
    cfg = Cfg(DIAL_MODE=mode, DIAL_ALLOWLIST=frozenset({MINE}))
    with pytest.raises(DialRefused):
        assert_dialable(MINE, cfg)


def test_unrestricted_permits():
    """Prod, and only prod."""
    cfg = Cfg(DIAL_MODE='unrestricted', DIAL_ALLOWLIST=frozenset())
    assert_dialable(STRANGER, cfg)      # must not raise


# --- config: absent vs empty is a deliberate asymmetry --------------------

def test_absent_dial_mode_defaults_to_allowlist(base_env):
    """Absent -> allowlist. A misconfigured box is inert, not loose."""
    from api.config import load_config
    cfg = load_config()
    assert cfg.DIAL_MODE == 'allowlist'
    assert cfg.DIAL_ALLOWLIST == frozenset()
    # and therefore dials nothing
    with pytest.raises(DialRefused):
        assert_dialable(MINE, cfg)


def test_empty_dial_mode_is_garbage_not_a_default(base_env):
    """EMPTY is not ABSENT. Empty stays empty and the guard refuses it."""
    base_env.setenv('DIAL_MODE', '')
    from api.config import load_config
    cfg = load_config()
    assert cfg.DIAL_MODE == ''
    with pytest.raises(DialRefused):
        assert_dialable(MINE, cfg)


def test_parse_allowlist():
    assert parse_allowlist(None) == frozenset()
    assert parse_allowlist('') == frozenset()
    assert parse_allowlist('   ') == frozenset()
    assert parse_allowlist(',,') == frozenset()
    assert parse_allowlist(f' {MINE} , {STRANGER} ') == frozenset({MINE, STRANGER})


# --- required config raises rather than defaulting ------------------------

def test_missing_db_setting_raises(base_env):
    from api.config import ConfigError, load_config
    for k in ('CALLER_DB_HOST', 'CALLER_DB_PORT', 'CALLER_DB_NAME',
              'CALLER_DB_USER', 'CALLER_DB_PASSWORD'):
        base_env.delenv(k, raising=False)
    with pytest.raises(ConfigError):
        load_config()


def test_missing_retell_setting_raises(base_env):
    """Retell credentials are required too - no quiet default."""
    from api.config import ConfigError, load_config
    base_env.delenv('RETELL_API_KEY', raising=False)
    with pytest.raises(ConfigError):
        load_config()
