"""
Outbound email. Brevo transactional API.

One place that sends, so the digest and the immediate alerts cannot drift
apart in how they authenticate or how failures are recorded.
"""

import json
import urllib.error
import urllib.request

BREVO_URL = 'https://api.brevo.com/v3/smtp/email'


def send(cfg, to: str, subject: str, text: str, timeout: int = 20,
         attachments=None, sender_email: str = None,
         sender_name: str = None) -> dict:
    """
    Returns {'ok': bool, 'detail': str}. Never raises.

    attachments: [(filename, bytes)] - base64'd inline. Used by the weekly
    suppression backup, which must leave this droplet.
    """
    # The digest sends as itself; a campaign email sends as THE CAMPAIGN's
    # verified sender. Defaulting to the digest identity would quietly send
    # firm mail from the wrong address.
    payload = {
        'sender': {'email': sender_email or cfg.DIGEST_FROM,
                   'name': sender_name or cfg.DIGEST_FROM_NAME},
        'to': [{'email': to}],
        'subject': subject,
        'textContent': text,
    }
    if attachments:
        import base64
        payload['attachment'] = [
            {'name': name, 'content': base64.b64encode(blob).decode()}
            for name, blob in attachments
        ]
    req = urllib.request.Request(
        BREVO_URL, data=json.dumps(payload).encode(), method='POST',
        headers={'api-key': cfg.BREVO_API_KEY,
                 'content-type': 'application/json',
                 'accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {'ok': 200 <= r.status < 300,
                    'detail': f'HTTP {r.status} {r.read()[:200].decode()}'}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'detail': f'HTTP {e.code} {e.read()[:300].decode()}'}
    except Exception as e:
        return {'ok': False, 'detail': f'{type(e).__name__}: {e}'}
