"""
prompt_versions: record WHAT RAN.

Measured 2026-09-07 across four real calls: swapping the model alone
(claude-5-sonnet -> gpt-4.1) on a byte-identical 8,047-char prompt moved
llm p50 from 2836ms to 508ms - an 82% drop, roughly fourteen times what
trimming 1,970 characters bought. A score movement after a change like that is
unattributable if the row stores only prompt text.

Prompt length matters too, and superlinearly: ~0.082 ms/char between 3.4k and
5.3k chars, ~0.170 ms/char at 8k. prompt_chars is stored so that curve can be
re-derived rather than re-measured.
"""

import json

from api import db


def snapshot(cfg, version: int, stage: str = 'L1', changed_by: str = 'system',
             note: str = 'auto-snapshot'):
    """
    Read one Retell agent version and record it. Idempotent on
    (agent_id, agent_version).

    VERSION IS REQUIRED, and the caller must say where it got it. This used
    to read cfg.AGENT_L1_VERSION, which made it a second definition of "the
    live version" alongside the campaign row - and the stale one, since
    nothing updated env when the picker changed a campaign. A snapshot of the
    wrong version is worse than no snapshot: it attributes scores to a prompt
    that never ran.
    """
    version = int(version)
    from retell import Retell
    c = Retell(api_key=cfg.RETELL_API_KEY)
    agent = c.agent.retrieve(cfg.AGENT_L1, version=version)
    engine = agent.response_engine
    d = engine if isinstance(engine, dict) else json.loads(engine.model_dump_json())
    llm = c.llm.retrieve(d['llm_id'], version=int(d['version']))
    prompt = getattr(llm, 'general_prompt', '') or ''
    model = getattr(llm, 'model', None)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO prompt_versions
                       (stage, agent_id, agent_version, prompt_text, model,
                        prompt_chars, changed_by, change_note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (agent_id, agent_version)
                     WHERE agent_version IS NOT NULL
                     DO UPDATE SET prompt_text = EXCLUDED.prompt_text,
                                   model = EXCLUDED.model,
                                   prompt_chars = EXCLUDED.prompt_chars
                   RETURNING version, model, prompt_chars, agent_version""",
                (stage, cfg.AGENT_L1, version, prompt, model,
                 len(prompt), changed_by, note))
            return cur.fetchone()


_sync_cache = {}          # agent_id -> {'at': float, 'error': str|None}
SYNC_MAX_AGE = 120.0      # seconds


def sync_versions(cfg, stage: str = 'L1', force: bool = False):
    """
    Pull Retell's agent versions into prompt_versions.

    INCREMENTAL. `get_versions()` is ONE call and carries each version's
    last-modification timestamp, so a version we already hold with the same
    timestamp is skipped without fetching its prompt. A full detail fetch is
    two API calls per version - seventeen versions meant thirty-four calls,
    which is why this only ever ran from a button and the list sat eight
    versions behind what Retell actually had.

    Notes are preserved: a version already recorded keeps its change_note,
    because that is the operator's text, not Retell's.
    """
    import datetime
    from retell import Retell
    c = Retell(api_key=cfg.RETELL_API_KEY)
    agent_id = cfg.AGENT_L1

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT agent_version, retell_updated_at
                             FROM prompt_versions
                            WHERE agent_id = %s AND agent_version IS NOT NULL""",
                        (agent_id,))
            known = {r['agent_version']: r['retell_updated_at'] for r in cur.fetchall()}

    seen = fetched = 0
    for v in c.agent.get_versions(agent_id):
        ver = getattr(v, 'version', None)
        if ver is None:
            continue
        seen += 1
        ms = getattr(v, 'last_modification_timestamp', None)
        when = (datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC)
                if ms else None)
        if not force and ver in known and known[ver] == when:
            continue                      # unchanged - do not pay for details
        try:
            a = c.agent.retrieve(agent_id, version=ver)
            eng = a.response_engine
            d = eng if isinstance(eng, dict) else json.loads(eng.model_dump_json())
            llm = c.llm.retrieve(d['llm_id'], version=int(d['version']))
            prompt = getattr(llm, 'general_prompt', '') or ''
            model = getattr(llm, 'model', None)
        except Exception:
            prompt, model = '', None
        fetched += 1
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO prompt_versions
                          (stage, agent_id, agent_version, prompt_text, model,
                           prompt_chars, changed_by, change_note,
                           retell_updated_at, is_published)
                       VALUES (%s,%s,%s,%s,%s,%s,'retell-sync','',%s,%s)
                       ON CONFLICT (agent_id, agent_version)
                         WHERE agent_version IS NOT NULL
                         DO UPDATE SET prompt_text=EXCLUDED.prompt_text,
                                       model=EXCLUDED.model,
                                       prompt_chars=EXCLUDED.prompt_chars,
                                       retell_updated_at=EXCLUDED.retell_updated_at,
                                       is_published=EXCLUDED.is_published""",
                    (stage, agent_id, ver, prompt, model, len(prompt), when,
                     bool(getattr(v, 'is_published', False))))
    return {'stage': stage, 'versions': seen, 'fetched': fetched}


def sync_if_stale(cfg, stage: str = 'L1', max_age: float = SYNC_MAX_AGE):
    """
    Refresh the list on page load. Returns an error string, or None.

    NEVER RAISES. A prompts page that will not load because Retell is slow is
    worse than one showing a slightly stale list with a note saying so - and
    the whole point is that you should not have to click a button to see a
    version you published an hour ago.
    """
    import time
    agent_id = cfg.AGENT_L1
    ent = _sync_cache.setdefault(agent_id, {'at': 0.0, 'error': None})
    if time.time() - ent['at'] < max_age:
        return ent['error']
    try:
        sync_versions(cfg, stage)
        ent.update(at=time.time(), error=None)
    except Exception as exc:
        ent.update(at=time.time(), error=f'{type(exc).__name__}: {exc}'[:140])
    return ent['error']


def listing(cfg, stage: str = 'L1', include_version=None):
    """
    PUBLISHED versions only, newest first.

    A draft in the Retell dashboard is not a thing you can dial - it is
    somebody mid-edit. Offering drafts in the picker is how v8 went live by
    accident in the first place: the list implied they were choosable.

    `include_version` keeps a campaign's CURRENT version in the list even if it
    is unpublished, so a config that already exists is never silently dropped
    off the screen it is edited on. It is returned flagged, not hidden.
    """
    agent_id = cfg.AGENT_L1
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT agent_version, model, prompt_chars, change_note,
                          retell_updated_at, is_published,
                          (SELECT count(*) FROM calls c
                            WHERE c.agent_version = pv.agent_version) AS calls_run
                     FROM prompt_versions pv
                    WHERE agent_id = %s AND agent_version IS NOT NULL
                      AND (is_published OR agent_version = %s)
                    ORDER BY agent_version DESC""",
                (agent_id, include_version))
            return cur.fetchall()
