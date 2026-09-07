"""
Webhook handler: signature, idempotency, speed.

The signature is built here with Retell's own signer rather than by calling
our verifier's inverse, so the test would catch us drifting from the real
scheme rather than agreeing with our own mistake.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from retell.lib.webhook_auth import symmetric

KEY = 'key_test_webhook'


def _sign(body: str, key: str = KEY) -> str:
    return symmetric['sign'](body, key)


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv('RETELL_API_KEY', KEY)
    import api.webhooks as wh
    wh._cfg = None                     # drop the cached config
    from api.main import app
    yield TestClient(app)
    wh._cfg = None


def _payload(call_id='call_test_1', event='call_ended'):
    return {'event': event, 'call': {'call_id': call_id,
                                     'agent_id': 'agent_test'}}


def test_valid_signature_accepted_and_inserts_one_row(client, db):
    body = json.dumps(_payload())
    r = client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': _sign(body),
                             'content-type': 'application/json'})
    assert r.status_code == 200, r.text
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM webhook_events')
        assert cur.fetchone()['n'] == 1


def test_replayed_identical_webhook_creates_no_second_row(client, db):
    """Assume every event arrives more than once, because it does."""
    body = json.dumps(_payload())
    sig = _sign(body)
    for _ in range(3):
        r = client.post('/webhooks/retell', content=body,
                        headers={'x-retell-signature': sig,
                                 'content-type': 'application/json'})
        assert r.status_code == 200
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM webhook_events')
        assert cur.fetchone()['n'] == 1


def test_same_call_different_events_are_separate_rows(client, db):
    for event in ('call_started', 'call_ended', 'call_analyzed'):
        body = json.dumps(_payload(event=event))
        client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': _sign(body),
                             'content-type': 'application/json'})
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM webhook_events')
        assert cur.fetchone()['n'] == 3


def test_unsigned_post_is_401(client):
    body = json.dumps(_payload())
    r = client.post('/webhooks/retell', content=body,
                    headers={'content-type': 'application/json'})
    assert r.status_code == 401


def test_bad_signature_is_401(client):
    body = json.dumps(_payload())
    r = client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': 'v=1,d=' + 'a' * 64,
                             'content-type': 'application/json'})
    assert r.status_code == 401


def test_signature_from_a_different_key_is_401(client):
    body = json.dumps(_payload())
    r = client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': _sign(body, 'key_wrong'),
                             'content-type': 'application/json'})
    assert r.status_code == 401


def test_stale_signature_is_rejected(client):
    """Retell's scheme carries a 5-minute replay window. Honour it."""
    body = json.dumps(_payload())
    old_ms = int(time.time() * 1000) - (10 * 60 * 1000)
    sig = symmetric['sign'](body, KEY, old_ms)
    r = client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': sig,
                             'content-type': 'application/json'})
    assert r.status_code == 401


def test_handler_returns_under_200ms(client, db):
    body = json.dumps(_payload(call_id='call_speed'))
    sig = _sign(body)
    start = time.perf_counter()
    r = client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': sig,
                             'content-type': 'application/json'})
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert r.status_code == 200
    assert elapsed_ms < 200, f'handler took {elapsed_ms:.1f}ms'


def test_missing_call_id_is_400_not_500(client):
    """Retell must not retry a payload we can never store."""
    body = json.dumps({'event': 'call_ended', 'call': {}})
    r = client.post('/webhooks/retell', content=body,
                    headers={'x-retell-signature': _sign(body),
                             'content-type': 'application/json'})
    assert r.status_code == 400
