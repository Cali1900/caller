"""
EMAIL VALIDATION — the auto-send exclusion set.

This is the first thing that mails without a person, so the tests are mostly
about REFUSING. Every exclusion has a break definition; a silently-stopped
exclusion is an email to the wrong person, not a red test.

DNS is injected everywhere. Real lookups in a suite make a test that fails on
a train.
"""

import pytest

from api import email_validation as ev

GOOD_MX = lambda d: (True, None)
NO_MX = lambda d: (False, 'no MX records')
DNS_DOWN = lambda d: (None, 'MX lookup failed (Timeout)')

SITE = 'https://www.whitfieldlaw.com'


def test_a_matching_domain_is_clean(db=None):
    r = ev.check('bob@whitfieldlaw.com', SITE, mx=GOOD_MX)
    assert r['ok'] is True
    assert r['domain_class'] == 'website'
    assert r['reasons'] == []


def test_a_subdomain_still_counts_as_the_firm():
    """mail.firm.com and firm.com are the same firm."""
    r = ev.check('bob@mail.whitfieldlaw.com', SITE, mx=GOOD_MX)
    assert r['domain_class'] == 'website' and r['ok'] is True


@pytest.mark.parametrize('addr', ['bob@gmail.com', 'sara@outlook.com',
                                  'j@icloud.com', 'x@protonmail.com'])
def test_free_mail_sends_but_is_flagged(addr):
    """
    Plenty of small PI firms genuinely use gmail. Holding all of them would put
    most of the queue in review, which defeats auto-send.
    """
    r = ev.check(addr, SITE, mx=GOOD_MX)
    assert r['ok'] is True, r['reasons']
    assert r['domain_class'] == 'freemail'


def test_neither_matching_nor_freemail_is_held_loudly():
    r = ev.check('bob@some-random-domain.biz', SITE, mx=GOOD_MX)
    assert r['ok'] is False
    assert r['domain_class'] == 'neither'
    assert any('neither' in x for x in r['reasons'])


def test_the_gmial_typo_is_caught_twice():
    """
    THE CASE THIS EXISTS FOR. A receptionist says "yes that's right" to a
    one-character typo and it is invisible at reading speed.
    """
    r = ev.check('bob@gmial.com', SITE, mx=NO_MX)
    assert r['ok'] is False
    assert any('MX' in x or 'mail' in x for x in r['reasons'])
    assert r['domain_class'] == 'neither'


@pytest.mark.parametrize('addr', ['info@whitfieldlaw.com', 'admin@whitfieldlaw.com',
                                  'office@whitfieldlaw.com', 'intake@whitfieldlaw.com'])
def test_role_addresses_are_held(addr):
    """A demand-letter pitch to info@ reaches a shared inbox nobody owns."""
    r = ev.check(addr, SITE, mx=GOOD_MX)
    assert r['ok'] is False
    assert any('role address' in x for x in r['reasons'])


def test_disposable_domains_are_held():
    r = ev.check('bob@mailinator.com', SITE, mx=GOOD_MX)
    assert r['ok'] is False
    assert any('disposable' in x for x in r['reasons'])


@pytest.mark.parametrize('addr', ['', '   ', 'not-an-email', 'bob@', '@firm.com',
                                  'bob@firm', 'bob smith@firm.com'])
def test_malformed_addresses_are_held(addr):
    assert ev.check(addr, SITE, mx=GOOD_MX)['ok'] is False


def test_no_mx_is_held():
    r = ev.check('bob@whitfieldlaw.com', SITE, mx=NO_MX)
    assert r['ok'] is False


# ---------------------------------------------------------------------------
# FAIL CLOSED
# ---------------------------------------------------------------------------

def test_a_dns_outage_holds_rather_than_sends():
    """
    THE PROPERTY THAT MATTERS MOST. If the check cannot run, do not send. Not
    "log and continue" - an email cannot be recalled, a held one costs seconds.
    """
    r = ev.check('bob@whitfieldlaw.com', SITE, mx=DNS_DOWN)
    assert r['ok'] is False


def test_a_dns_outage_says_so_instead_of_blaming_the_address():
    """
    Both a bad domain and a DNS outage hold, but only one is the lead's fault.
    A DNS outage reported as "no MX records" sends Sean hunting for bad
    addresses that are all fine.
    """
    r = ev.check('bob@whitfieldlaw.com', SITE, mx=DNS_DOWN)
    assert any('lookup failed' in x for x in r['reasons'])
    assert not any('no MX records' in x for x in r['reasons'])


def test_a_missing_dns_library_holds_rather_than_sends(monkeypatch):
    """Fail closed includes 'the dependency is not installed'."""
    import builtins
    real = builtins.__import__

    def no_dns(name, *a, **k):
        if name.startswith('dns'):
            raise ImportError('no dns')
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, '__import__', no_dns)
    ok, why = ev.has_mx('whitfieldlaw.com')
    assert ok is None and 'unavailable' in why
    assert ev.check('bob@whitfieldlaw.com', SITE)['ok'] is False


def test_no_website_on_file_holds_a_non_freemail_address():
    """
    Without a website we cannot say the domain is the firm's. Free-mail still
    sends (it is a known-good shape); anything else is unverifiable and holds.
    """
    assert ev.check('bob@gmail.com', None, mx=GOOD_MX)['ok'] is True
    r = ev.check('bob@whitfieldlaw.com', None, mx=GOOD_MX)
    assert r['ok'] is False
    assert any('no website on file' in x for x in r['reasons'])


# ---------------------------------------------------------------------------
# whose fault is it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('exc_name,expect_ok', [
    ('NXDOMAIN', False),          # no such domain
    ('NoAnswer', False),          # exists, no MX
    ('NoNameservers', False),     # exists, nothing answers - a parked typo
    ('Timeout', None),            # OUR problem
    ('OSError', None),            # OUR problem
])
def test_dns_failures_are_attributed_correctly(monkeypatch, exc_name, expect_ok):
    """
    Both a bad domain and a DNS outage HOLD the email - but only one is
    something Sean can act on. A DNS outage reported as a bad address sends him
    hunting for typos that are not there; a parked typo domain reported as an
    outage hides the one case this check exists for.

    gmial.com really does resolve as NoNameservers.
    """
    import dns.resolver

    class FakeResolver:
        timeout = lifetime = 1

        def resolve(self, domain, rdtype):
            raise type(exc_name, (Exception,), {})()

    monkeypatch.setattr(dns.resolver, 'Resolver', FakeResolver)
    ok, why = ev.has_mx('example.com')
    assert ok is expect_ok, f'{exc_name} classified wrong: {why}'
    if expect_ok is False:
        assert 'does not accept mail' in why
    else:
        assert 'lookup failed' in why
