"""
ARCHIVE, and the one thing it must never do.

A suppressed number coming back out of archive and being dialed is the
failure this entire system exists to prevent, so that case is tested first
and tested against the REAL selection query rather than against a helper
that agrees with the code by construction.
"""
import datetime

import pytest

from api import archive, autosend, campaigns, dialer, guards, stages
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
            'has_confirmed_email': False, 'campaign_id': running_campaign_id()}
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


# ==========================================================================
# THE RETURN CLEARS GATES AND KEEPS FACTS
#
# Every test ABOVE builds its lead at has_confirmed_email=False. That is what makes the
# suppression test properly isolated, and it is also what hid the bug these
# tests cover: the three EMAIL-derived archive reasons (no_reply, bad_email,
# unsubscribed) can only be reached from L2, because they all require email 1
# to have gone out and mark_emailed() refuses anything not at L2.
#
# Same shape as masked guard #1 in README.md - a fixture value that production
# does not supply at that point in the lead's life.
# ==========================================================================

def _emailed_l2_lead(db, **kw):
    """A lead as it ACTUALLY is when archived for no_reply: at L2, emailed,
    with a confirmed address. This is the state no other test in this file
    constructs."""
    cols = {'has_confirmed_email': True, 'dm_name': 'Pat Kelly', 'dm_title': 'Office Manager',
            'dm_email': 'pat@whitfield.test', 'dm_email_confirmed': True,
            'website': 'whitfield.test'}
    cols.update(kw)
    lid = _lead(db, **cols)
    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET emailed_at = now() - interval '40 days',
                              emailed_by = 'operator' WHERE lead_id = %s""", (lid,))
    db.commit()
    return lid


def _return_and_requeue(db, lid):
    """Age the lead out, sweep it back, and put it on the campaign the way a
    person would from /leads."""
    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1
    campaigns.assign([lid], running_campaign_id())
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='active' WHERE lead_id=%s", (lid,))
    db.commit()


def test_a_lead_archived_from_L2_comes_back_dialable(db, cfg_env):
    """
    THE BUG. A firm we emailed, that never replied, archived for no_reply and
    rested six months, must be callable again.

    It was not. return_due() never reset `stage`, nothing in the codebase ever
    writes has_confirmed_email=False (api/stages.py only writes 'L2'), and STAGE_DIALABLE is
    "AND l.stage = 'L1'" - so the lead came back to the pool looking fresh and
    could never be dialed. It could not be emailed either: emailed_at survived
    and mark_emailed() is write-once.

    ISOLATED: after the return this lead passes every other filter - on the
    campaign, queued, status 'new', never replied, not suppressed, windows
    blanked by the fixture. The stage gate is the only thing that can exclude
    it.
    """
    lid = _emailed_l2_lead(db, phone_e164='+15552241001')
    archive.archive(lid, 'no_reply')
    _return_and_requeue(db, lid)

    row = _get(db, lid)
    assert row['has_confirmed_email'] is False, 'the stage gate was not cleared'

    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lid) in picked, \
        'a firm we emailed six months ago came back to the pool and cannot be dialed'


def test_the_return_does_not_skip_straight_to_L2(db, cfg_env):
    """
    A returned lead still HOLDS an address, so it must not be treated as
    already-at-L2 and skipped. It gets CALLED, and that call either re-confirms
    the contact or updates it.

    advance_to_l2() is only ever called on a fresh capture (drain) or a person
    ticking confirmed (web) - never on a tick or at selection - so holding an
    address cannot promote a lead on its own. This pins that.
    """
    lid = _emailed_l2_lead(db, phone_e164='+15552241002')
    archive.archive(lid, 'no_reply')
    _return_and_requeue(db, lid)

    assert _get(db, lid)['has_confirmed_email'] is False
    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lid) in picked
    assert _get(db, lid)['has_confirmed_email'] is False, \
        'being selected must not advance the stage - only a capture does that'


def test_the_return_clears_the_confirmation_but_keeps_the_address(db):
    """
    dm_email_confirmed is a GATE, not a fact about the firm: it records that WE
    verified the address. Six months on that verification is stale even when
    the address is not, and it is read by autosend.eligibility(),
    drafts.generate_for() and advance_to_l2().

    So the confirmation is cleared and the ADDRESS IS KEPT - we still know
    where to write, it just has to be re-confirmed before anything auto-sends.
    """
    lid = _emailed_l2_lead(db, phone_e164='+15552241003')
    archive.archive(lid, 'no_reply')
    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1

    row = _get(db, lid)
    assert row['dm_email'] == 'pat@whitfield.test', 'the ADDRESS is a fact - keep it'
    assert row['dm_name'] == 'Pat Kelly', 'the NAME is a fact - keep it'
    assert row['dm_title'] == 'Office Manager'
    assert row['website'] == 'whitfield.test'
    assert not row['dm_email_confirmed'], \
        'a six-month-old confirmation must not still pass the auto-send gate'

    d = autosend.eligibility(row, {'email_1_mode': 'auto'},
                             validate=lambda *a, **k: {'domain_class': 'firm',
                                                       'reasons': []})
    assert d['ok'] is False and any('confirm' in r for r in d['reasons']), \
        'a returned lead must be held by the gate until the address is re-confirmed'


def test_a_returned_lead_can_be_emailed_again(db):
    """
    emailed_at is a GATE: mark_emailed() is write-once, so if it survives the
    return the lead can never be sent to again. Cleared, so the next send works.
    """
    lid = _emailed_l2_lead(db, phone_e164='+15552241004')
    archive.archive(lid, 'no_reply')
    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1
    assert _get(db, lid)['emailed_at'] is None, 'the send gate was not cleared'

    # Walk it back to L2 the way production does, then send.
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET dm_email_confirmed = TRUE WHERE lead_id=%s",
                    (lid,))
        stages.advance_to_l2(cur, lid, 'reconfirmed on the follow-up call')
    db.commit()
    assert _get(db, lid)['has_confirmed_email'] is True
    assert stages.mark_emailed(lid, emailed_by='operator') is not None, \
        'the second send was refused because the first one is still on the row'


def test_the_old_send_record_survives_the_return(db):
    """
    "We emailed this firm in September and heard nothing" must still be
    answerable after the lead comes back looking fresh. The return CLEARS the
    send gate, so the record is snapshotted into archived_contacts first -
    otherwise the reason the lead was archived becomes unreconstructable at
    the exact moment it returns.
    """
    lid = _emailed_l2_lead(db, phone_e164='+15552241005')
    with db.cursor() as cur:
        # Distinct clicked_at: email_clicks is UNIQUE (lead_id, clicked_at),
        # so two clicks in the same statement would collide on now().
        cur.execute("""INSERT INTO email_clicks
                           (lead_id, clicked_at, minutes_since_sent)
                       VALUES (%s, now() - interval '2 days', 47),
                              (%s, now() - interval '1 day', 1180)""",
                    (lid, lid))
    db.commit()
    archive.archive(lid, 'no_reply')
    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1

    snaps = archive.contacts(lid)
    assert len(snaps) == 1, 'the send record was not preserved'
    s = snaps[0]
    assert s['dm_email'] == 'pat@whitfield.test'
    assert s['emailed_at'] is not None, 'the send DATE is the point of the snapshot'
    assert s['emailed_by'] == 'operator'
    assert s['click_count'] == 2
    assert s['first_click_minutes'] == 47
    assert s['archive_reason'] == 'no_reply'

    # And the clicks themselves are untouched: minutes_since_sent was computed
    # and stored at click time, so the timings stay true without emailed_at.
    with db.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM email_clicks WHERE lead_id=%s', (lid,))
        assert cur.fetchone()['n'] == 2


def test_unarchive_by_hand_leaves_the_same_state_as_the_sweep(db):
    """A hand pull and the nightly sweep must agree. Two code paths that leave
    a lead in different states is 'why is this one different' with no answer."""
    a = _emailed_l2_lead(db, phone_e164='+15552241006')
    b = _emailed_l2_lead(db, phone_e164='+15552241007')
    archive.archive(a, 'no_reply')
    archive.archive(b, 'no_reply')

    _age_out(db, a)
    assert archive.return_due()['returned'] == 1
    assert archive.unarchive(b, by='sean') is not None

    fields = ('has_confirmed_email', 'status', 'pool_status', 'campaign_id',
              'attempts',
              'emailed_at', 'emailed_by', 'dm_email_confirmed', 'dm_email',
              'archived_at', 'returns_at')
    ra, rb = _get(db, a), _get(db, b)
    assert {f: ra[f] for f in fields} == {f: rb[f] for f in fields}
    assert len(archive.contacts(b)) == 1, 'the hand pull must snapshot too'


def test_a_lead_that_replied_comes_back_dialable(db, cfg_env):
    """
    THE SECOND GATE, found by the same question that found the first.

    dialer.REPLIED_GUARD is "AND l.replied_at IS NULL". replied_at survived the
    return, so a firm that once said "not interested", was archived as refused
    and rested six months came back to the pool and could never be dialed.

    Six months on it is a legitimate prospect again - that is the entire
    premise of archive_reason='refused' having a return date. Clearing it
    defeats no exclusion list: suppression is keyed on the PHONE and
    email_do_not_send on the ADDRESS, and neither is keyed on the lead.

    ISOLATED: has_confirmed_email=False throughout, so the stage gate cannot be what excludes
    this lead - only replied_at can.
    """
    lid = _lead(db, phone_e164='+15552241010', has_confirmed_email=False)
    assert stages.record_reply(lid, note='not interested, we draft our own') is True
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='new' WHERE lead_id=%s", (lid,))
    db.commit()

    archive.archive(lid, 'refused')
    _return_and_requeue(db, lid)

    row = _get(db, lid)
    assert row['has_confirmed_email'] is False, 'the premise: the stage gate is not the cause'
    assert row['replied_at'] is None, 'the reply gate was not cleared'

    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lid) in picked, \
        'a firm that said no six months ago is still permanently undialable'


def test_the_reply_survives_as_history_after_the_gate_is_cleared(db):
    """
    Clearing the GATE must not lose the FACT. Somebody about to re-dial a
    returned firm needs to know it said no once, and what it said - that is
    the difference between a cold call and an informed one.
    """
    lid = _lead(db, phone_e164='+15552241011', has_confirmed_email=False)
    assert stages.record_reply(lid, note='not interested, we draft our own',
                               by='sean') is True
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='new' WHERE lead_id=%s", (lid,))
    db.commit()
    archive.archive(lid, 'refused')
    _age_out(db, lid)
    assert archive.return_due()['returned'] == 1

    assert _get(db, lid)['replied_at'] is None, 'the gate should be cleared'

    snaps = archive.contacts(lid)
    assert len(snaps) == 1, 'a reply-only lead still needs a snapshot row'
    assert snaps[0]['replied_at'] is not None, 'the reply DATE was lost'
    assert snaps[0]['reply_note'] == 'not interested, we draft our own'
    assert snaps[0]['replied_by'] == 'sean'


def test_a_suppressed_lead_that_replied_is_still_not_dialable(db, cfg_env):
    """
    The two clears together must not add up to a way out of suppression.

    Clearing replied_at removes a BUSINESS gate. Suppression is a COMPLIANCE
    list keyed on the phone, and it is untouched by any of this - so a lead
    that both replied AND asked to be removed stays unreachable.
    """
    lid = _lead(db, phone_e164='+15552241012', has_confirmed_email=False)
    stages.record_reply(lid, note='take me off your list')
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='new' WHERE lead_id=%s", (lid,))
        cur.execute("INSERT INTO suppression (phone_e164, reason, source) "
                    "VALUES ('+15552241012', 'asked to be removed', 'reply')")
    db.commit()

    archive.archive(lid, 'refused')
    _return_and_requeue(db, lid)

    assert _get(db, lid)['replied_at'] is None, 'the premise: the reply gate is gone'
    picked = {str(c['lead_id']) for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lid) not in picked, \
        'clearing the reply gate let a SUPPRESSED number through - suppression ' \
        'is compliance and is keyed on the phone, not the lead'
