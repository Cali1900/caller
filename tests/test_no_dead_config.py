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
# AUTO-SEND IS BUILT AND DELIBERATELY NOT WIRED
# ==========================================================================

def test_the_worker_does_not_run_the_sender_loop():
    """
    ⚠️ THIS ASSERTS A DECISION, NOT A BUG.

    api/sender.py is complete: due() selects leads whose campaign is on
    email_1_mode='auto' and whose delay has elapsed, send_one() re-checks the
    whole gate in-transaction, run_once() loops them. api/autosend.py is the
    gate, with seven exclusions and their own break definitions (41-47).

    NOTHING CALLS sender.run_once(). The worker ticks drain, scorer, drafts,
    alerts, digest and the archive sweep - not the sender. The only production
    caller of the module is web.py, which calls send_manual(): the "Send now"
    button a person presses after reading the draft.

    That is deliberate while the drip is parked. The brief is explicit that
    reply detection is a HARD GATE - "nothing auto-sends if detection is
    unavailable" - and reply ingest is not built. So email_1_mode='auto'
    changes nothing today, and that is the safe state, not an oversight.

    WITHOUT THIS TEST it reads as a missing line rather than a decision, and
    the fix looks like a one-line addition to worker.main(). Adding that line
    turns on automated outbound email to law firms. If you are here because
    this test failed, you are making that decision - make it deliberately,
    with the reply gate in place, and rewrite this test to say so.
    """
    src = open(os.path.join(ROOT, 'api', 'worker.py')).read()
    tree = ast.parse(src)

    # Static, not behavioural: ask whether the CODE references it at all,
    # rather than whether one run happened not to reach it.
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            called.add(node.attr)
        elif isinstance(node, ast.Name):
            called.add(node.id)

    assert 'sender' not in called, (
        'api/worker.py now references the sender. If you have wired the '
        'auto-send loop, that is a decision to turn on automated outbound '
        'email to law firms - see this test\'s docstring, confirm reply '
        'detection is in place, and update it deliberately.')

    # And the positive half: the manual path IS wired, so this test cannot
    # pass merely because the sender was deleted.
    web = open(os.path.join(ROOT, 'api', 'web.py')).read()
    assert 'send_manual' in web, \
        'the "Send now" button lost its wiring - this test would then be ' \
        'asserting nothing'
