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


def snapshot(cfg, stage: str = 'L1', changed_by: str = 'system',
             note: str = 'auto-snapshot'):
    """
    Read the pinned Retell agent version and record it. Idempotent on
    (agent_id, agent_version).
    """
    from retell import Retell
    c = Retell(api_key=cfg.RETELL_API_KEY)
    agent = c.agent.retrieve(cfg.AGENT_L1, version=cfg.AGENT_L1_VERSION)
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
                (stage, cfg.AGENT_L1, cfg.AGENT_L1_VERSION, prompt, model,
                 len(prompt), changed_by, note))
            return cur.fetchone()


def active(cfg, stage: str = 'L1'):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT version, model, prompt_chars, agent_version
                     FROM prompt_versions
                    WHERE agent_id = %s AND agent_version = %s""",
                (cfg.AGENT_L1, cfg.AGENT_L1_VERSION))
            return cur.fetchone()


def sync_versions(cfg, stage: str = 'L1'):
    """
    Pull EVERY Retell agent version into prompt_versions so the picker can
    show version, date, model, char count and note without hitting the API on
    every page load.

    Notes are preserved: a version already recorded keeps its change_note,
    because that is the operator's text, not Retell's.
    """
    import datetime
    from retell import Retell
    c = Retell(api_key=cfg.RETELL_API_KEY)
    agent_id = cfg.AGENT_L1 if stage == 'L1' else cfg.AGENT_L3

    seen = 0
    for v in c.agent.get_versions(agent_id):
        ver = getattr(v, 'version', None)
        if ver is None:
            continue
        try:
            a = c.agent.retrieve(agent_id, version=ver)
            eng = a.response_engine
            d = eng if isinstance(eng, dict) else json.loads(eng.model_dump_json())
            llm = c.llm.retrieve(d['llm_id'], version=int(d['version']))
            prompt = getattr(llm, 'general_prompt', '') or ''
            model = getattr(llm, 'model', None)
        except Exception:
            prompt, model = '', None
        ms = getattr(v, 'last_modification_timestamp', None)
        when = (datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC)
                if ms else None)
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
        seen += 1
    return {'stage': stage, 'versions': seen}


def set_note(agent_id, agent_version: int, note: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE prompt_versions SET change_note = %s
                    WHERE agent_id = %s AND agent_version = %s
                    RETURNING version""", (note, agent_id, agent_version))
            return cur.fetchone() is not None


def listing(cfg, stage: str = 'L1'):
    agent_id = cfg.AGENT_L1 if stage == 'L1' else cfg.AGENT_L3
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT agent_version, model, prompt_chars, change_note,
                          retell_updated_at, is_published,
                          (SELECT count(*) FROM calls c
                            WHERE c.agent_version = pv.agent_version) AS calls_run
                     FROM prompt_versions pv
                    WHERE agent_id = %s AND agent_version IS NOT NULL
                    ORDER BY agent_version DESC""", (agent_id,))
            return cur.fetchall()
