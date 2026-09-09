"""
Shared test fixtures.

TEST DATABASE: tests run against `caller_test_db`, NOT the database the
running containers use. `B-tests-share-the-dev-database` is open in the
CounselorAI repo and has bitten three times; there is no reason to build a
fourth writer into a brand-new service. The test database is created and
migrated on demand from the same forward-only migration files.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEST_DB = 'caller_test_db'

# Every setting load_config() requires. Individual tests override what they
# are actually exercising.
REQUIRED_ENV = {
    'CALLER_DB_HOST': 'h',
    'CALLER_DB_PORT': '5432',
    'CALLER_DB_NAME': 'n',
    'CALLER_DB_USER': 'u',
    'CALLER_DB_PASSWORD': 'p',
    'RETELL_API_KEY': 'key_test',
    'RETELL_FROM_NUMBER': '+15550000000',
    'AGENT_L1': 'agent_test',
    'OPERATOR_TIMEZONE': 'America/Los_Angeles',
}


@pytest.fixture
def base_env(monkeypatch):
    """A complete, valid environment. Override single keys per test."""
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv('DIAL_MODE', raising=False)
    monkeypatch.delenv('DIAL_ALLOWLIST', raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# database-backed fixtures
#
# Connection details come from the environment, so the same conftest works
# inside the container (caller-postgres:5432) and from the host
# (127.0.0.1:4102). No docker CLI is used, so this runs anywhere.
# ---------------------------------------------------------------------------

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from psycopg2 import sql as _sql  # noqa: E402


def _conn_kwargs(dbname):
    return dict(
        host=os.environ['CALLER_DB_HOST'],
        port=int(os.environ['CALLER_DB_PORT']),
        dbname=dbname,
        user=os.environ['CALLER_DB_USER'],
        password=os.environ['CALLER_DB_PASSWORD'],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


@pytest.fixture(scope='session')
def test_db():
    """Create + migrate caller_test_db once per session."""
    admin = psycopg2.connect(**_conn_kwargs(os.environ['CALLER_DB_NAME']))
    admin.autocommit = True   # CREATE DATABASE cannot run in a transaction
    with admin.cursor() as cur:
        cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (TEST_DB,))
        if cur.fetchone() is None:
            cur.execute(_sql.SQL('CREATE DATABASE {}').format(
                _sql.Identifier(TEST_DB)))
    admin.close()

    # Apply EVERY migration, tracked the same way scripts/migrate.sh does.
    # Checking for one table and bailing meant the test schema silently
    # stopped matching production the moment a second migration landed.
    conn = psycopg2.connect(**_conn_kwargs(TEST_DB))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                           filename text PRIMARY KEY,
                           applied_at timestamptz NOT NULL DEFAULT now())""")
        mig_dir = os.path.join(ROOT, 'migrations')
        for name in sorted(os.listdir(mig_dir)):
            if not name.endswith('.sql'):
                continue
            cur.execute('SELECT 1 FROM schema_migrations WHERE filename=%s', (name,))
            if cur.fetchone():
                continue
            with open(os.path.join(mig_dir, name)) as f:
                cur.execute(f.read())
            cur.execute('INSERT INTO schema_migrations (filename) VALUES (%s)', (name,))
    conn.close()
    return TEST_DB


@pytest.fixture
def db(test_db, monkeypatch):
    """
    A connection to the TEST database, truncated first so every test starts
    from a known state. api.db is reset so it picks up the test database.
    """
    monkeypatch.setenv('CALLER_DB_NAME', test_db)
    monkeypatch.setenv('DIAL_MODE', 'allowlist')
    monkeypatch.setenv('DIAL_ALLOWLIST', '')

    import api.db as _apidb
    _apidb._pool = None            # force a new pool against the test db

    conn = psycopg2.connect(**_conn_kwargs(test_db))
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE activity, dial_audit, webhook_events, call_scores,
                     score_attempts, alerts, digests, prompt_versions,
                     calls, leads, suppression, campaign_configs,
                     -- ⚠️ THESE HAVE NO FOREIGN KEY TO leads, SO CASCADE NEVER
                     -- REACHES THEM. email_do_not_send is keyed on the ADDRESS
                     -- precisely so it outlives the lead - and it was outliving
                     -- test isolation too, leaking suppressed addresses from
                     -- one test into every test that ran after it. A drip test
                     -- was excluded by an address a completely different test
                     -- had blocked, which is the masked-guard shape pointed at
                     -- the harness instead of the code.
                     email_do_not_send, email_audit,
                     -- Named explicitly rather than left to CASCADE: relying on
                     -- a cascade path means a future table with no FK silently
                     -- starts leaking, exactly as above.
                     email_sends, email_clicks, drip_steps, archived_contacts
            RESTART IDENTITY CASCADE
        """)
        # Windows are per-campaign and campaign_windows cascades off
        # campaign_configs above, so a test's window edits die with its
        # campaign. There is no global settings table any more - every piece
        # of operator config belongs to a campaign.
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()
    _apidb._pool = None


def running_campaign_id():
    """
    The campaign a test lead belongs to.

    In the named-campaign model a lead with no campaign is BY DEFINITION not
    dialable - the selection joins on campaign_id. So a fixture lead described
    as "ready to be dialed" has to belong to a running campaign, and every test
    helper that inserts one attaches it here rather than each file inventing
    its own.
    """
    from api import campaigns as c
    run = c.running()
    if run:
        return run['campaign_id']
    existing = [r for r in c.list_all() if r['name'] == 'TEST']
    row = existing[0] if existing else c.create('TEST', notes='fixture')
    c.update(row['campaign_id'], daily_cap=1000, max_concurrent=1)
    return c.start(row['campaign_id'])['campaign_id']


@pytest.fixture
def lead(db):
    """One active lead, ready to be dialed."""
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO leads (company, phone_e164, timezone, pool_status, status,
                               campaign_id)
            VALUES ('Test Firm LLP', '+15551234567', 'America/Los_Angeles',
                    'active', 'new', %s)
            RETURNING lead_id, phone_e164
        """, (running_campaign_id(),))
        row = cur.fetchone()
    db.commit()
    return row


@pytest.fixture
def cfg_env(db):
    """A Config pointed at the TEST database. Depends on `db` so the env is
    already redirected before load_config() reads it."""
    from api.config import load_config
    return load_config()


@pytest.fixture
def cfg_dialable(db, monkeypatch):
    """
    Config with the ALLOWLIST out of the way, for tests about the cap, pause
    and campaign gating.

    Same reasoning as neutralising the window in those tests: the allowlist has
    its own dedicated tests and its own break pass, and leaving it in here
    would make a cap test fail for allowlist reasons. Retell is always mocked
    in tests, so nothing can dial regardless.
    """
    monkeypatch.setenv('DIAL_MODE', 'unrestricted')
    from api.config import load_config
    return load_config()


@pytest.fixture
def campaign(db):
    """
    A saved campaign, RUNNING, with a wide-open cap.

    Campaigns are named configurations and exactly one runs at a time, so
    every test that expects a dial needs one started. Created stopped and
    started explicitly here, the same way the UI does it.
    """
    from api import campaigns as c
    for row in c.list_all():
        c.stop(row['campaign_id'])
    return c.get(running_campaign_id())


@pytest.fixture
def queued(db, campaign):
    """Assign leads to the running campaign and queue them."""
    from api import campaigns as c

    def _queue(ids):
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        ids = [str(i) for i in ids]
        if ids:
            c.assign(ids, campaign['campaign_id'])
        with db.cursor() as cur:
            cur.execute("UPDATE leads SET pool_status='active' "
                        "WHERE lead_id = ANY(%s::uuid[])", (ids,))
        db.commit()
    _queue.campaign_id = campaign['campaign_id']
    return _queue
