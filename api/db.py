"""
Database access.

Connection function is get_conn() - same name as the other repos in this
estate, so nobody has to remember a second convention. RealDictCursor, so
rows are r['col'] and never r[0].
"""

import contextlib

import psycopg2
import psycopg2.extras
from psycopg2 import pool as _pgpool

from api.config import load_config

_pool = None


def _init_pool():
    global _pool
    if _pool is None:
        cfg = load_config()
        _pool = _pgpool.ThreadedConnectionPool(
            1, 10,
            host=cfg.DB_HOST,
            port=cfg.DB_PORT,
            dbname=cfg.DB_NAME,
            user=cfg.DB_USER,
            password=cfg.DB_PASSWORD,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return _pool


@contextlib.contextmanager
def get_conn():
    """
    Yields a connection. Commits on clean exit, rolls back on exception.

    The disposition branch and the suppression write both depend on this
    being a real transaction boundary - 'remove_me' must write suppression
    and set the lead to dnc in ONE transaction, not two.
    """
    p = _init_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


def ping() -> bool:
    """Cheap liveness check for /health."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 AS ok')
            return cur.fetchone()['ok'] == 1
