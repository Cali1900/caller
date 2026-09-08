"""
WHO A CAMPAIGN SENDS AS.

Free text let you save a sender that cannot send. Brevo will only deliver from
a sender verified on the account, so the address is a CHOICE FROM A LIST, not
something you type.

The list is pulled from Brevo so it cannot go stale, cached briefly, and
unioned with the addresses the operator has chosen to offer. The union matters:
an address can be intended-but-not-yet-verified (a domain still warming), and
hiding it would make the screen lie about what is configured. It is offered and
LABELLED instead.

WHAT VERIFICATION ACTUALLY GATES: nothing auto-sends today. The campaign sender
is the From: shown on the draft, which is copied into a mail client by hand, so
an unverified address still works for that. It gates the digest and whatever
sends automatically later. That is why an unverified sender is a WARNING here
and not a refusal - refusing would block a legitimate setup for a send path
that does not exist yet.
"""

import json
import time
import urllib.request

BREVO_SENDERS_URL = 'https://api.brevo.com/v3/senders'

# Offered whether or not Brevo has verified them yet. First entry is the
# default for a new campaign.
OFFERED = (
    ('info@counselorai.io', 'CounselorAI'),
    ('sean@demandcounselor.com', 'Demand Counselor'),
)

DEFAULT_SENDER = OFFERED[0][0]

_cache = {'at': 0.0, 'senders': None, 'error': None}
CACHE_SECONDS = 600


def _fetch(cfg, timeout: int = 10):
    # A REAL User-Agent. Brevo answers curl with 200 and Python's default
    # urllib UA with 403 on this endpoint - same key, same host, same second.
    # Without this the list silently falls back to the hard-coded pair and
    # looks like a permissions problem.
    req = urllib.request.Request(
        BREVO_SENDERS_URL,
        headers={'api-key': cfg.BREVO_API_KEY, 'accept': 'application/json',
                 'User-Agent': 'caller/1.0 (+counselorai.io)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return [(s['email'], s.get('name') or '')
            for s in data.get('senders', []) if s.get('active')]


def verified(cfg, force: bool = False):
    """
    Brevo's verified senders, cached. Returns (emails, error).

    NEVER RAISES. A campaign screen that will not load because Brevo is slow is
    worse than one showing a slightly stale list with a note saying so.
    """
    now = time.time()
    if not force and _cache['senders'] is not None \
            and now - _cache['at'] < CACHE_SECONDS:
        return _cache['senders'], _cache['error']
    try:
        got = _fetch(cfg)
        _cache.update(at=now, senders=got, error=None)
    except Exception as exc:
        # Keep whatever we had; say why it is stale.
        _cache.update(at=now, error=f'{type(exc).__name__}: {exc}'[:120])
        if _cache['senders'] is None:
            _cache['senders'] = []
    return _cache['senders'], _cache['error']


def options(cfg, current: str = ''):
    """
    [{email, name, verified, offered, current}] for the dropdown.

    Includes the campaign's CURRENT sender even if it is neither offered nor
    verified, so an existing config is never silently rewritten by rendering a
    page - the operator sees it, labelled, and chooses.
    """
    ver, err = verified(cfg)
    ver_emails = {e for e, _ in ver}
    ver_names = dict(ver)

    out, seen = [], set()
    for email, name in OFFERED:
        out.append({'email': email, 'name': ver_names.get(email) or name,
                    'verified': email in ver_emails, 'offered': True,
                    'current': email == current})
        seen.add(email)
    for email, name in sorted(ver):
        if email not in seen:
            out.append({'email': email, 'name': name, 'verified': True,
                        'offered': False, 'current': email == current})
            seen.add(email)
    if current and current not in seen:
        out.append({'email': current, 'name': '', 'verified': False,
                    'offered': False, 'current': True})
    return out, err


def is_allowed(cfg, email: str) -> bool:
    """A sender may be saved if it is offered or verified. Anything else is a
    typo - which is the whole reason this stopped being a text field."""
    email = (email or '').strip()
    if not email:
        return False
    if any(email == e for e, _ in OFFERED):
        return True
    ver, _ = verified(cfg)
    return any(email == e for e, _ in ver)
