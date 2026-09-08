"""
EMAIL VALIDATION - the exclusion set for auto-send.

FAILS CLOSED, like assert_dialable. If a check cannot run - DNS unreachable, a
lookup times out, anything unexpected - the verdict is HOLD. Not "log and
continue". An email sent to the wrong person cannot be recalled; a held email
costs Sean thirty seconds.

THE DOMAIN CHECK HAS THREE OUTCOMES, not pass/fail:

    matches the firm's website   -> send
    known free-mail provider     -> send, flagged quietly on the lead
    neither                      -> HOLD, loud

A strict website match is wrong: plenty of small PI firms genuinely use gmail,
and holding all of them would put most of the queue in review, which defeats
auto-send entirely.

The check that earns its place is MX. `bob@gmial.com` - a receptionist saying
"yes that's right" to a one-character typo - is invisible at reading speed and
obvious to a DNS lookup.
"""

import re
import socket

# Free-mail providers a real firm might legitimately use. Not exhaustive and
# does not need to be: an unknown provider lands in `neither`, which is HOLD -
# the safe direction. Adding to this list only ever loosens, so it is a
# deliberate edit, never a guess.
FREEMAIL = frozenset({
    'gmail.com', 'googlemail.com', 'yahoo.com', 'ymail.com', 'hotmail.com',
    'outlook.com', 'live.com', 'msn.com', 'aol.com', 'icloud.com', 'me.com',
    'mac.com', 'comcast.net', 'verizon.net', 'sbcglobal.net', 'att.net',
    'bellsouth.net', 'cox.net', 'earthlink.net', 'protonmail.com', 'proton.me',
})

# Mail nobody reads. A demand-letter pitch to info@ reaches a shared inbox.
ROLE_LOCALPARTS = frozenset({
    'info', 'admin', 'office', 'contact', 'hello', 'sales', 'support',
    'billing', 'help', 'team', 'mail', 'enquiries', 'inquiries', 'reception',
    'frontdesk', 'front desk', 'noreply', 'no-reply', 'donotreply',
    'postmaster', 'webmaster', 'abuse', 'legal', 'intake',
})

DISPOSABLE = frozenset({
    'mailinator.com', 'guerrillamail.com', '10minutemail.com', 'tempmail.com',
    'throwaway.email', 'yopmail.com', 'trashmail.com', 'sharklasers.com',
    'getnada.com', 'temp-mail.org',
})

# Deliberately permissive on the local part - the RFC allows more than people
# expect, and this is a typo screen, not a spec implementation. The MX check is
# what actually establishes the domain is real.
ADDRESS = re.compile(r'^[^@\s]+@([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
                     r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+)$')

DNS_TIMEOUT = 5.0


def domain_of(value: str):
    m = ADDRESS.match((value or '').strip())
    return m.group(1).lower() if m else None


def site_domain(website: str):
    """'https://www.whitfieldlaw.com/contact' -> 'whitfieldlaw.com'."""
    w = (website or '').strip().lower()
    if not w:
        return None
    w = re.sub(r'^[a-z]+://', '', w).split('/')[0].split('?')[0]
    w = w.split('@')[-1].split(':')[0]
    if w.startswith('www.'):
        w = w[4:]
    return w or None


def _registrable(host: str):
    """
    Last two labels. Crude on purpose: it exists so mail.firm.com and
    firm.com count as the same firm, not to be a public-suffix implementation.
    It over-matches on co.uk-style suffixes, which errs toward MATCHING - so
    the two-label comparison is only ever used to say "same firm", never to
    reject.
    """
    parts = (host or '').split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


def has_mx(domain: str):
    """
    (True, None) | (False, reason) | (None, reason) when the lookup FAILED.

    None is not False. A domain we could not check is not a domain we know is
    bad, and both are held - but only one of them is the lead's fault, and the
    reason shown to Sean has to say which.
    """
    try:
        import dns.resolver
    except ImportError:
        return None, 'dns library unavailable'
    try:
        r = dns.resolver.Resolver()
        r.timeout = r.lifetime = DNS_TIMEOUT
        answers = r.resolve(domain, 'MX')
        return (True, None) if len(answers) else (False, 'no MX records')
    except Exception as exc:
        name = type(exc).__name__
        # THE DOMAIN'S FAULT vs OURS. Both hold the email, but only one is
        # something Sean can act on, and a DNS outage reported as a bad address
        # sends him hunting for typos that are not there.
        #
        # NoNameservers belongs on the domain side: the name exists but nothing
        # answers for it, which is the signature of a parked typo domain -
        # gmial.com resolves exactly this way. Classifying it as "our problem"
        # made the one case this check exists for read like an outage.
        if name in ('NXDOMAIN', 'NoAnswer', 'NoNameservers'):
            return False, f'domain does not accept mail ({name})'
        return None, f'MX lookup failed ({name})'


def check(email: str, website: str = None, mx=None) -> dict:
    """
    Returns {ok, domain_class, reasons, domain}.

    ok=True means SAFE TO AUTO-SEND. Every other case holds for review with a
    reason Sean can read on the lead.

    `mx` is injectable so tests never touch the network - real DNS in a test
    suite is a test that fails on a train.
    """
    mx = mx or has_mx
    out = {'ok': False, 'domain_class': None, 'reasons': [], 'domain': None}
    addr = (email or '').strip()

    if not addr:
        out['reasons'].append('no email captured')
        return out

    domain = domain_of(addr)
    if not domain:
        out['reasons'].append('not a valid address format')
        return out
    out['domain'] = domain

    local = addr.split('@')[0].strip().lower()
    if local in ROLE_LOCALPARTS:
        out['reasons'].append(f'role address ({local}@) - nobody owns that inbox')
    if domain in DISPOSABLE:
        out['reasons'].append('disposable domain')

    ok_mx, why = mx(domain)
    if ok_mx is None:
        out['reasons'].append(why or 'MX could not be checked')
    elif ok_mx is False:
        out['reasons'].append(why or 'no MX records')

    site = site_domain(website)
    if site and _registrable(domain) == _registrable(site):
        out['domain_class'] = 'website'
    elif domain in FREEMAIL:
        out['domain_class'] = 'freemail'
    else:
        out['domain_class'] = 'neither'
        out['reasons'].append(
            f'{domain} is neither the firm\'s website domain'
            f'{" (" + site + ")" if site else " (no website on file)"}'
            f' nor a known free-mail provider')

    out['ok'] = not out['reasons']
    return out
