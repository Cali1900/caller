"""
ARCHIVE, and the one thing it must never do.

A suppressed number coming back out of archive and being dialed is the
failure this entire system exists to prevent, so that case is tested first
and tested against the REAL selection query rather than against a helper
that agrees with the code by construction.
"""
import datetime

import pytest

from api import archive, autosend, campaigns, dialer, guards
from tests.conftest import running_campaign_id

LA = 'America/Los_Angeles'


@pytest.fixture(autouse=True)
def _dialable(db, cfg_env, monkeypatch):
    """
    Open the calling windows and lift the cap for every test in this file.

    Without this the window is the thing excluding a lead, and 'archived was
    not dialed' would pass for the wrong reason - the masked-guard shape that
    has now caused a dozen tests to test nothing.
    """
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    campaigns.update(running_campaign_id(), daily_cap=1000)


def _lead(db, **kw):
    cols = {'company': 'Whitfield Law', 'phone_e164': '+15552240001',
            'timezone': LA, 'pool_status': 'active', 'status': 'new',
            'stage': 'L1', 'campaign_id': running_campaign_id()}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


def _get(db, lid):
    with db.cursor() as cur:
        cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lid,))
        return cur.fetchone()


def _age_out(db, lid):
    """Move the DATA, not the clock. Every now() lives in SQL, so a test
    cannot pretend it is six months from now - it makes the row old."""
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET returns_at = now() - interval '1 day' "
                    "WHERE lead_id = %s", (lid,))
    db.commit()


# ==========================================================================
# ⚠️  THE RETURN MUST NOT CLEAR AN EXCLUSION LIST
# ==========================================================================

def test_a_suppressed_number_returning_from_archive_is_still_not_dialable(db, cfg_env):
    """
    THE FAILURE THIS WHOLE SYSTEM EXISTS TO PREVENT.

    Suppression is keyed on the PHONE. The lead row is not the record - it is
    a thing that points at one. Archiving, resting six months and returning
    rewrites the lead; it must not rewrite the obligation.

    ISOLATED ON PURPOSE: after the return this lead passes every other filter
    in the selection query - it is on the campaign, in the queue, at L1,
    status 'new', never dialed, inside its calling window. Suppression is the
    only thing that can exclude it, so if the join were dropped this test
    goes red on its own rather than being masked by another filter.
    """
    lid = _lead(db, phone_e164='+15552240099')
    with db.cursor() as cur:
        cur.execute("INSERT INTO suppression (phone_e164, reason, source) "
                    "VALUES ('+15552240099', 'asked to be removed', 'call')")
    db.commit()

    archive.archive(lid, 'refused', by='test')
    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1

    row = _get(db, lid)
    assert row['status'] == 'new' and row['campaign_id'] is None, \
        'the premise: the lead really did come back as a fresh prospect'

    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM suppression '
                    "WHERE phone_e164 = '+15552240099'")
        assert cur.fetchone()['n'] == 1, \
            'THE RETURN DELETED A SUPPRESSION ROW - a person who asked to be ' \
            'removed is now dialable again'

    # And prove it through the real selection path, not just the table.
    campaigns.assign([lid], running_campaign_id())
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='active' WHERE lead_id=%s", (lid,))
    db.commit()
    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lid) not in picked, \
        'a suppressed number came back out of archive and was selected to dial'


def test_the_return_does_not_clear_the_email_do_not_send_list(db):
    """
    The other half, and the reason the list is keyed on the ADDRESS.

    bad_email used to be only a STATUS, and the sweep rewrites status to
    'new' - so a hard-bounced address would have come back fully sendable
    with nothing on the row remembering the bounce.
    """
    lid = _lead(db, dm_email='bounced@whitfield.example')
    archive.archive(lid, 'bad_email', by='test')
    assert archive.is_do_not_send('bounced@whitfield.example')

    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1
    assert _get(db, lid)['status'] == 'new', 'the premise: it really returned'

    assert archive.is_do_not_send('bounced@whitfield.example'), \
        'THE RETURN CLEARED THE DO-NOT-SEND LIST - a hard-bounced address ' \
        'is sendable again'


def test_a_returned_bad_email_lead_is_still_refused_by_the_send_gate(db):
    """Through the gate, not the table: the list is only worth having if
    the thing that sends actually asks it."""
    lid = _lead(db, dm_email='bounced2@whitfield.example', dm_name='Bob',
                dm_email_confirmed=True, website='whitfield.example')
    archive.archive(lid, 'unsubscribed', by='test')
    _age_out(db, lid)
    archive.return_due()

    lead = dict(_get(db, lid))
    d = autosend.eligibility(lead, {'email_1_mode': 'auto'},
                             validate=lambda *a, **k: {'domain_class': 'match',
                                                       'reasons': []})
    assert d['ok'] is False
    assert autosend.HoldReason.DO_NOT_SEND in d['reasons']


def test_an_unsubscribe_stops_email_and_leaves_the_phone_alone(db):
    """
    STANDING RULE: an email unsubscribe suppresses EMAIL ONLY. Someone who
    does not want our emails has not given up the right to be phoned about a
    case they asked about. The two lists must never be merged.
    """
    lid = _lead(db, phone_e164='+15552240077', dm_email='nomail@whitfield.example')
    archive.archive(lid, 'unsubscribed', by='test')

    assert archive.is_do_not_send('nomail@whitfield.example')
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM suppression "
                    "WHERE phone_e164 = '+15552240077'")
        assert cur.fetchone()['n'] == 0, \
            'an email unsubscribe put a PHONE on the suppression list'


# ==========================================================================
# archiving
# ==========================================================================

def test_the_reason_is_recorded_and_required(db):
    lid = _lead(db)
    with pytest.raises(archive.ArchiveRefused):
        archive.archive(lid, 'because', by='test')
    row = archive.archive(lid, 'max_attempts', by='test')
    assert row['archive_reason'] == 'max_attempts'
    assert _get(db, lid)['status'] == 'archived'


def test_returns_at_is_six_months_out(db):
    lid = _lead(db)
    archive.archive(lid, 'no_reply', by='test')
    row = _get(db, lid)
    gap = row['returns_at'] - row['archived_at']
    assert abs(gap - archive.RETURN_AFTER) < datetime.timedelta(seconds=5)


def test_archiving_twice_does_not_move_the_return_date(db):
    """
    A restamp would rest a lead archived in March until September. Same
    reasoning as emailed_at being write-once: the date anchors a schedule,
    so moving it silently moves everything measured from it.
    """
    lid = _lead(db)
    first = archive.archive(lid, 'refused', by='test')['returns_at']
    again = archive.archive(lid, 'refused', by='test')
    assert _get(db, lid)['returns_at'] == first
    assert again is not None


def test_a_lead_that_is_not_due_stays_put(db):
    lid = _lead(db)
    archive.archive(lid, 'no_reply', by='test')
    assert archive.return_due()['returned'] == 0
    assert _get(db, lid)['status'] == 'archived'


def test_an_archived_lead_is_never_dialed(db, cfg_env):
    """
    ISOLATED: this lead is on the running campaign, queued, at L1, never
    dialed and inside its window. Its status is the only thing excluding it.
    """
    live = _lead(db, phone_e164='+15552240011')
    rest = _lead(db, phone_e164='+15552240012')
    archive.archive(rest, 'refused', by='test')
    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(live) in picked, 'the premise: an identical un-archived lead IS picked'
    assert str(rest) not in picked


def test_the_activity_line_says_why_and_when_it_returns(db):
    """Six months on, 'archived' alone is undifferentiated and useless -
    a firm that ran out of no-answers is not a firm that said no."""
    lid = _lead(db)
    archive.archive(lid, 'max_attempts', by='sean')
    with db.cursor() as cur:
        cur.execute("SELECT summary, detail FROM activity WHERE lead_id=%s "
                    "AND kind='archive'", (lid,))
        row = cur.fetchone()
    assert 'max_attempts' in row['summary'] and 'sean' in row['summary']


def test_unarchive_brings_it_back_without_touching_suppression(db):
    lid = _lead(db, phone_e164='+15552240066')
    with db.cursor() as cur:
        cur.execute("INSERT INTO suppression (phone_e164, reason) "
                    "VALUES ('+15552240066', 'dnc')")
    db.commit()
    archive.archive(lid, 'manual', by='test')
    assert archive.unarchive(lid, by='sean') is not None
    assert _get(db, lid)['status'] == 'new'
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM suppression "
                    "WHERE phone_e164 = '+15552240066'")
        assert cur.fetchone()['n'] == 1
