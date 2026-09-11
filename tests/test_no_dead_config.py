"""
⚠️ STANDING CHECK — a test may not configure something production ignores.

Eleven times now a guard has been "covered" by a test that configured a value
nothing consumes. The seeds matched the defaults, so the assertion was green
while testing nothing:

  * `dialing_windows` — the pre-campaign global window table. Windows moved to
    `campaign_windows`; both seed Mon-Fri 09:00-17:00, so every window
    assertion agreed with the dialer by coincidence.
  * `settings['max_concurrent']` — moved onto the campaign. The campaign
    default is also 1, so the evidence for the guard that stops us dialing a
    hundred leads in forty minutes rested on a value the code never reads.

This test makes that shape fail instead of pass. It is deliberately static: it
asks whether ANY production code reads the thing, not whether it happened to be
read during one test.

Adding a key to an allowlist here is a decision to be made in review, not a
convenience.
"""

import ast
import glob
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tests that exercise the settings module ITSELF. They legitimately write keys
# nothing dials on, because the subject under test is set_many's validation.
SETTINGS_MODULE_TESTS = {
    'test_min_greater_than_max_is_refused',
    'test_a_db_value_overrides_the_default',
    'test_an_out_of_range_version_is_refused',
}

# Infrastructure tables the harness owns. Production never touches them and
# never should.
HARNESS_TABLES = {
    'schema_migrations',   # conftest applies migrations the way migrate.sh does
    'pg_database',         # conftest creates the test database
    'information_schema',  # a test asserts on column types
}


def _py(pattern):
    return sorted(glob.glob(os.path.join(ROOT, pattern), recursive=True))


# ---------------------------------------------------------------------------
# settings keys
# ---------------------------------------------------------------------------

def _keys_tests_write():
    """{key: {"file::test", ...}} for every set_many({...}) literal in tests."""
    out = {}
    for path in _py('tests/*.py'):
        tree = ast.parse(open(path).read())
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if fn.name in SETTINGS_MODULE_TESTS:
                continue
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                name = getattr(call.func, 'attr', None) or getattr(call.func, 'id', None)
                if name != 'set_many' or not call.args:
                    continue
                arg = call.args[0]
                if not isinstance(arg, ast.Dict):
                    continue
                for k in arg.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        out.setdefault(k.value, set()).add(
                            f'{os.path.basename(path)}::{fn.name}')
    return out


SETTINGS_READER = re.compile(r'\bsettings\w*\.get\(\s*[\'"]([a-z_]+)[\'"]')
SETTINGS_SUBSCRIPT = re.compile(r'\b(?:st|settings|cfgset)\[[\'"]([a-z_]+)[\'"]\]')


def _keys_production_reads():
    """Keys production reads FROM SETTINGS.

    Deliberately source-aware. A plain string search would call `daily_cap`
    alive because the campaign has a column of that name - which is exactly the
    confusion that let the dead settings key survive.
    """
    keys = set()
    for path in _py('api/**/*.py'):
        src = open(path).read()
        if path.endswith('api/settings.py'):
            continue           # its own defaults table is not a consumer
        keys |= set(SETTINGS_READER.findall(src))
        keys |= set(SETTINGS_SUBSCRIPT.findall(src))
    return keys


def test_no_test_configures_a_settings_key_production_ignores():
    written = _keys_tests_write()
    read = _keys_production_reads()
    dead = {k: v for k, v in written.items() if k not in read}
    assert not dead, (
        'These tests write settings keys NO production code reads, so they '
        'configure nothing and their assertions pass for the wrong reason:\n'
        + '\n'.join(f'  {k!r} <- {", ".join(sorted(v))}' for k, v in sorted(dead.items()))
        + '\n\nEither point the test at where the value actually lives (the '
          'campaign, usually), or delete the line. Do not add it to the '
          'allowlist unless the test is exercising the settings module itself.')


def test_the_settings_module_stays_deleted():
    """
    api/settings.py was deleted on 2026-09-08. Every key it held moved onto the
    campaign; what remained was ten inert rows that still LOOKED authoritative.

    That is not cosmetic. scripts/deploy.sh paused before every restart by
    writing settings['dialing_enabled'], which nothing had read for days - so
    its pause was a no-op, and a deploy during calling hours would have rebuilt
    straight through a live call while reporting that it had paused.

    If a global settings store is ever genuinely needed again, that is a design
    decision that should break this test and be argued for - not something that
    reappears because one value had nowhere obvious to live.
    """
    assert not os.path.exists(os.path.join(ROOT, 'api', 'settings.py')), (
        'api/settings.py is back. Operator config belongs to the campaign that '
        'uses it; a global store is how config and reader drift apart.')

    importers = []
    for path in _py('api/**/*.py') + _py('scripts/*.sh'):
        src = open(path).read()
        if re.search(r'\bfrom api import [^\n]*\bsettings\b|\bapi\.settings\b', src):
            importers.append(os.path.relpath(path, ROOT))
    assert not importers, f'these import a settings module again: {importers}'


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

# Require the SHAPE of the statement, not just the keyword. Matching a bare
# `UPDATE <word>` found "a stray UPDATE cannot get round it" in a docstring and
# reported a table called `cannot` - a checker that cries wolf gets muted, and
# a muted checker is the thing it was written to prevent.
WRITE_TABLE = re.compile(
    r'\b(?:INSERT\s+INTO\s+([a-z_]{3,40})\s*[(]'
    r'|UPDATE\s+([a-z_]{3,40})\s+SET\b'
    r'|TRUNCATE\s+(?:TABLE\s+)?([a-z_]{3,40})\b'
    r'|DELETE\s+FROM\s+([a-z_]{3,40})\b)', re.I)
ANY_TABLE = re.compile(
    r'\b(?:FROM\s+([a-z_]{3,40})\b'
    r'|JOIN\s+([a-z_]{3,40})\b'
    r'|INSERT\s+INTO\s+([a-z_]{3,40})\b'
    r'|UPDATE\s+([a-z_]{3,40})\s+SET\b'
    r'|TRUNCATE\s+(?:TABLE\s+)?([a-z_]{3,40})\b'
    r'|DELETE\s+FROM\s+([a-z_]{3,40})\b)', re.I)

SQL_NOISE = {'select', 'values', 'set', 'only', 'where', 'the', 'and', 'not'}


def _tables(paths, pattern):
    out = {}
    for path in paths:
        for m in pattern.finditer(open(path).read()):
            t = next((g for g in m.groups() if g), '').lower()
            if not t or t in SQL_NOISE:
                continue
            out.setdefault(t, set()).add(os.path.basename(path))
    return out


def test_no_test_writes_a_table_production_never_touches():
    """The `dialing_windows` shape: a test maintaining a table with no effect."""
    written = _tables(_py('tests/*.py'), WRITE_TABLE)
    used = _tables(_py('api/**/*.py'), ANY_TABLE)
    dead = {t: f for t, f in written.items()
            if t not in used and t not in HARNESS_TABLES}
    assert not dead, (
        'These tests write tables NO production code reads:\n'
        + '\n'.join(f'  {t!r} <- {", ".join(sorted(f))}' for t, f in sorted(dead.items()))
        + '\n\nA dead table that still looks authoritative is worse than no '
          'table - the assertions built on it check nothing.')


# ==========================================================================
# THE WORKER RUNS BOTH SEND LOOPS
# ==========================================================================

def test_the_worker_runs_both_send_loops():
    """
    THIS ASSERTION WAS INVERTED ON 2026-09-09, and the history is the point.

    It used to assert the worker did NOT reference the sender. api/sender.py and
    api/autosend.py were complete and tested and nothing called
    sender.run_once() - a gap rather than a decision - so the test made closing
    it deliberate rather than accidental.

    The drip is built now and the switches exist, so it HAS been closed on
    purpose. Both loops are wired, and both are off by default:

      email 1     the call campaign's email_1_mode, 'manual' by default
      drip steps  the drip campaign's is_running

    What is worth guarding is now the reverse. A refactor that quietly drops
    either call leaves two complete senders that never run and a drip that
    silently never advances - no error, no failed send, just firms that never
    hear from us again. Invisible failure is worse than a loud one.
    """
    src = open(os.path.join(ROOT, 'api', 'worker.py')).read()
    tree = ast.parse(src)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            called.add(node.attr)
        elif isinstance(node, ast.Name):
            called.add(node.id)

    assert 'sender' in called, \
        'api/worker.py no longer references the sender - email 1 will never go'
    assert 'drip' in called, \
        'api/worker.py no longer references the drip - no sequence will advance'
    assert 'run_once' in src

    # And the switches that keep them off by default still exist, or "wired"
    # would mean "sending to law firms on a timer with nothing to stop it".
    from api import autosend, campaigns as camp
    assert 'email_1_mode' in camp.CONFIG_FIELDS
    assert hasattr(autosend.HoldReason, 'NOT_AUTO')
    assert hasattr(autosend.HoldReason, 'DRIP_STOPPED')


def test_no_form_field_goes_unread_by_its_handler():
    """
    ⚠️ A DEAD CONTROL TELLS THE OPERATOR THE THING WORKS.

    /upload-form offered a `kind` select ("a CALL list" / "an EMAIL list") and a
    drip picker for months and read NEITHER: every upload went through the call
    parser, so an email-only CSV had every row rejected for a missing phone
    immediately after the screen offered to import it. The picker was invisible on
    top of that, because the page never passed `drips` to the template.

    That is worse than a missing feature. A missing control is obviously missing;
    this one made a promise the code did not keep, and from the operator's side
    there was no way to tell the difference until a real list was uploaded.

    In the suite as well as the pre-commit hook, because a check that only runs on
    commit is a check that a --no-verify skips silently.
    """
    import subprocess
    r = subprocess.run(['python3', 'scripts/check_dead_controls.py'],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
