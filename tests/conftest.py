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

    conn = psycopg2.connect(**_conn_kwargs(TEST_DB))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.leads') AS t")
        if cur.fetchone()['t'] is None:
            with open(os.path.join(ROOT, 'migrations',
                                   '20260906_001_initial.sql')) as f:
                cur.execute(f.read())
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
                     calls, campaign_leads, campaigns, leads, suppression
            RESTART IDENTITY CASCADE
        """)
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()
    _apidb._pool = None


@pytest.fixture
def lead(db):
    """One active lead, ready to be dialed."""
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO leads (company, phone_e164, timezone, pool_status, status)
            VALUES ('Test Firm LLP', '+15551234567', 'America/Los_Angeles',
                    'active', 'new')
            RETURNING lead_id, phone_e164
        """)
        row = cur.fetchone()
    db.commit()
    return row
