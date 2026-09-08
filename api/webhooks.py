"""
The PUBLIC routes this service exposes: the Retell webhook, and the click
redirect a recipient's browser hits.

It verifies the signature, does ONE insert, and returns 200. That is all.

Retell times out at 10 seconds and retries up to 3 times on any non-2xx. Real
work in this handler - a lead update, a Sheets write, an LLM call - turns a
slow dependency into duplicate deliveries, and those duplicates do not look
like duplicates when you are reading the logs later. All real work happens in
the drain (api/drain.py), off a durable inbox.

Idempotency is the (call_id, event) primary key plus ON CONFLICT DO NOTHING.
Assume every event arrives more than once, because it does.
"""

import json

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from api import clicks, db, retell
from api.config import load_config

router = APIRouter()

_cfg = None


def _config():
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def insert_webhook_event(call_id: str, event: str, payload: dict) -> None:
    """The single insert. Duplicates are dropped by the primary key."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO webhook_events (call_id, event, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (call_id, event) DO NOTHING
                """,
                (call_id, event, json.dumps(payload)),
            )


@router.post('/webhooks/retell')
async def retell_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get('x-retell-signature')

    # An unsigned POST to this route is someone writing rows into the lead
    # database. Reject before parsing anything.
    if not retell.verify_signature(raw, sig, _config().RETELL_API_KEY):
        raise HTTPException(status_code=401, detail='invalid signature')

    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail='malformed json')

    call_id = (body.get('call') or {}).get('call_id')
    event = body.get('event')
    if not call_id or not event:
        # 400 not 500: Retell should not retry a payload we can never store.
        raise HTTPException(status_code=400, detail='missing call_id or event')

    # psycopg2 is blocking; keep it off the event loop so a slow database
    # cannot stall the handler into Retell's 10s timeout.
    await run_in_threadpool(insert_webhook_event, call_id, event, body)

    return Response(content='{"ok":true}', media_type='application/json')


# ---------------------------------------------------------------------------
# THE CLICK REDIRECT
#
# Public because a recipient's browser has to reach it. It must expose NOTHING
# but a redirect - no body, no lead data, and no signal about whether the token
# was real.
#
# An unknown token REDIRECTS ANYWAY. A 404 would tell a scanner it guessed
# wrong, and a real recipient whose link got mangled by a mail client should
# still land on the page rather than see an error from us. Tracking is our
# problem; their click is not.
# ---------------------------------------------------------------------------

@router.get('/c/{token}')
async def click(token: str, request: Request):
    from fastapi.responses import RedirectResponse

    ua = request.headers.get('user-agent', '')
    # X-Forwarded-For first: nginx proxies this, so request.client is the proxy.
    fwd = (request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
    ip = fwd or (request.client.host if request.client else None)

    dest = None
    try:
        dest = await run_in_threadpool(clicks.record, token, ua, ip)
    except Exception as exc:                      # never let logging break the redirect
        print(f'[click] could not record {token[:6]}...: {exc}', flush=True)

    # 302, not 301: a permanent redirect would be cached by the browser and the
    # SECOND click would never reach us. He may well click twice.
    return RedirectResponse(dest or clicks.DESTINATION, status_code=302)
