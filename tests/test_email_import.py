"""
EMAIL-ONLY LEADS: a list of firms with addresses, no calls involved.

⚠️ THIS CHANGE REMOVED A STRUCTURAL GUARANTEE. leads.phone_e164 was NOT NULL, so
a lead without a number could not exist and therefore could not be dialled. From
migration 038 only CODE stands between a phoneless lead and a call attempt, and
most of this file is about that trade.

PROVENANCE IS NOT THE GATE. Phone presence is. An imported lead that turns out to
be worth calling gets a number by hand and becomes dialable, staying
lead_source='import' as the record of where it came from - see
test_an_imported_lead_with_a_phone_added_by_hand_dials, which is the test that
stops somebody "fixing" this into `imported never dials`.
"""
import pytest

from api import (archive, autosend, campaigns, dialer, drip, guards, stages,
                 upload, web)
from api import db as dbm
from tests.conftest import running_campaign_id
from tests.test_drip import open_all_hours

CSV = ('company,email,website,demands_per_month\n'
       'Whitfield Law,intake@whitfield.test,whitfield.test,12\n'
       'Bergman & Co,hello@bergman.test,bergman.test,\n')


@pytest.fixture
def client(db, cfg_env):
    from fastapi.testclient import TestClient
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _get(db, lid):
    with db.cursor() as cur:
        cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lid,))
        return cur.fetchone()


def _by_email(db, email):
    with db.cursor() as cur:
        cur.execute('SELECT * FROM leads WHERE dm_email = %s', (email,))
        return cur.fetchone()


# ==========================================================================
# the parse
# ==========================================================================

def test_company_and_email_are_enough():
    rows, rejects = upload.parse_email_csv(CSV)
    assert rejects == []
    assert len(rows) == 2
    assert rows[0]['company'] == 'Whitfield Law'
    assert rows[0]['dm_email'] == 'intake@whitfield.test'
    assert rows[0]['website'] == 'whitfield.test'
    assert rows[0]['demands_per_month'] == 12
    # Blank means NOT ANSWERED, never zero.
    assert rows[1]['demands_per_month'] is None


def test_a_phone_column_is_not_required():
    """The whole point. The call path still requires one; this path does not."""
    rows, rejects = upload.parse_email_csv('company,email\nW,a@w.test\n')
    assert rejects == [] and len(rows) == 1
    # And the CALL parser still refuses the same file, unchanged.
    _, call_rejects = upload.parse_csv('company,email\nW,a@w.test\n')
    assert call_rejects and 'missing required column' in call_rejects[0]['reason']


def test_a_missing_email_column_is_refused_with_a_useful_reason():
    rows, rejects = upload.parse_email_csv('company,phone\nW,+14245551212\n')
    assert rows == []
    assert 'email' in rejects[0]['reason']


def test_unusable_addresses_are_rejected_per_row():
    rows, rejects = upload.parse_email_csv(
        'company,email\nA,not-an-email\nB,two@@at.test\nC,no@domain\nD,ok@fine.test\n')
    assert len(rows) == 1 and rows[0]['company'] == 'D'
    assert len(rejects) == 3


def test_a_repeated_address_inside_one_file_is_named_as_such():
    """Otherwise the first inserts, the rest are silently skipped, and the report
    calls them database duplicates."""
    rows, rejects = upload.parse_email_csv(
        'company,email\nA,same@w.test\nB,same@w.test\n')
    assert len(rows) == 1
    assert 'appears earlier in this file' in rejects[0]['reason']


# ==========================================================================
# the upload
# ==========================================================================

def test_an_imported_lead_lands_in_the_pool_with_no_phone(db):
    r = upload.upload_emails(CSV)
    assert r['inserted'] == 2 and r['rejected'] == 0
    lead = _by_email(db, 'intake@whitfield.test')
    assert lead['phone_e164'] is None
    assert lead['timezone'] is None
    assert lead['pool_status'] == 'pool', 'uploading must never queue'
    # ⚠️ 'imported', NOT 'new'. The status IS the drip gate now, so it has to say
    # what the lead actually is: 'new' means waiting to be dialled and these have
    # no phone. 'emailed' would be a worse lie - nothing has been sent to them.
    assert lead['status'] == 'imported'
    assert lead['lead_source'] == 'import'
    assert lead['dm_email_confirmed'] is not True, \
        'import must NOT fake the agent-confirmed flag'


def test_a_duplicate_address_is_skipped_not_updated(db):
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='dnc' WHERE lead_id=%s",
                    (lead['lead_id'],))
    db.commit()
    r = upload.upload_emails(CSV)
    assert r['inserted'] == 0 and r['duplicates'] == 2
    assert _get(db, lead['lead_id'])['status'] == 'dnc', \
        're-uploading resurrected a lead that had gone dnc'


def test_an_address_we_already_call_is_not_imported_again(db):
    """Checked against EVERY lead, not just imported ones - otherwise importing
    a firm we already call creates a second row for it."""
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone, dm_email)
                       VALUES ('Whitfield Law','+14245559911',
                               'America/Los_Angeles','intake@whitfield.test')""")
    db.commit()
    r = upload.upload_emails(CSV)
    assert r['inserted'] == 1, 'the already-known firm should have been skipped'


# ==========================================================================
# ⚠️ THE DIAL GUARDS - what replaced the NOT NULL
# ==========================================================================

def test_a_phoneless_lead_is_never_a_candidate(db, cfg_env, monkeypatch):
    """
    The SILENT half. A filter excluding a row is the right shape for a filter.

    ISOLATED: this lead is on the running campaign, queued, status 'new', never
    replied, not suppressed, and the windows are blanked - so only the missing
    phone can exclude it.
    """
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    campaigns.update(running_campaign_id(), daily_cap=1000)
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    campaigns.assign([lead['lead_id']], running_campaign_id())
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='active' WHERE lead_id=%s",
                    (lead['lead_id'],))
    db.commit()
    picked = {str(c['lead_id'])
              for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lead['lead_id']) not in picked


def test_the_pre_dial_guard_refuses_loudly_and_is_audited(db, cfg_env):
    """
    The LOUD half, and why it exists separately from the filter.

    A lead reaching the dial path without a number is a DIFFERENT event from one
    being filtered out: something put it there, and "nothing happened" is the
    worst possible report. It raises DialRefused, which the dialer audits.
    """
    with pytest.raises(guards.DialRefused) as e:
        guards.assert_has_phone({'phone_e164': None})
    assert 'no phone' in str(e.value).lower()
    with pytest.raises(guards.DialRefused):
        guards.assert_has_phone({'phone_e164': '   '})
    guards.assert_has_phone({'phone_e164': '+14245551212'})   # does not raise

    # And the dialer maps it to a named audit outcome rather than 'refused_other'.
    outcome = next((o for k, o in dialer._REFUSAL_OUTCOMES
                    if k in 'no phone number on this lead'), None)
    assert outcome == 'refused_no_phone'


def test_an_imported_lead_with_a_phone_added_by_hand_dials(db, cfg_env, monkeypatch):
    """
    ⚠️ THE TEST THAT MATTERS MOST HERE.

    PHONE PRESENCE IS THE GATE; PROVENANCE IS ONLY A RECORD. An imported lead
    that turns out to be worth calling gets a number and a timezone by hand and
    becomes dialable - and stays lead_source='import', because the record is
    where it came from, not what has happened to it since.

    Without this test the obvious wrong turn is to filter the dialer on
    lead_source, which would make this case impossible and would look like a
    tightening rather than a regression.
    """
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')
    campaigns.update(running_campaign_id(), daily_cap=1000)
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')

    # ⚠️ THROUGH THE MOVE, which is the flow that exists: 'imported' is not a
    # dialable status, so a number alone changes nothing about dialling. Moving
    # sets 'new' and assigns the call campaign, and the timezone is DERIVED from
    # the state rather than typed.
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET state = 'CA' WHERE lead_id = %s",
                    (lead['lead_id'],))
    db.commit()
    from fastapi.testclient import TestClient
    import api.web  # noqa: F401
    from api.main import app
    c = TestClient(app)
    r = c.post(f"/leads/{lead['lead_id']}/edit",
               data={'phone_e164': '+14245557788', 'timezone': '',
                     'move_to': running_campaign_id(),
                     'dm_name': 'Pat', 'dm_email': 'intake@whitfield.test'},
               follow_redirects=False)
    assert 'REJECTED' not in r.headers['location'], r.headers['location']
    moved = _get(db, lead['lead_id'])
    assert moved['status'] == 'new', f"the move did not set 'new': {moved['status']}"
    assert moved['timezone'] == 'America/Los_Angeles', moved['timezone']
    assert moved['lead_source'] == 'import', \
        'the move rewrote provenance - that records where it came from, not what '\
        'has happened to it since'
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status = 'active' WHERE lead_id = %s",
                    (lead['lead_id'],))
    db.commit()

    picked = {str(c['lead_id'])
              for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lead['lead_id']) in picked, \
        'an imported lead with a real number was not dialable - the guard is ' \
        'keyed on provenance instead of on the phone'
    assert _get(db, lead['lead_id'])['lead_source'] == 'import', \
        'the provenance record was overwritten by making it dialable'


# ==========================================================================
# a NULL timezone must not silently exclude a lead that HAS a phone
# ==========================================================================

def test_an_offset_timezone_is_still_refused(db):
    """
    The IANA guard's real job is unchanged. Postgres accepts '-05:00' in
    AT TIME ZONE without complaint, and that failure is SILENT for weeks after
    each DST change. ABSENT is allowed now; WRONG still is not.
    """
    with db.cursor() as cur:
        cur.execute('SAVEPOINT s')
        try:
            cur.execute("""INSERT INTO leads (company, phone_e164, timezone)
                           VALUES ('W','+14245550001','-05:00')""")
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT s')
        else:
            cur.execute('ROLLBACK TO SAVEPOINT s')
            raise AssertionError('an OFFSET was accepted as a timezone')
    db.rollback()


def test_a_phone_without_a_timezone_matches_no_window(db, cfg_env):
    """
    ⚠️ WHY THE CONTACT SAVE REQUIRES A TIMEZONE ALONGSIDE A PHONE.

    PREFERENCE_WINDOW joins on l.timezone. A NULL there makes the row match no
    window, so the lead is silently excluded while LOOKING dialable - a phone, on
    a campaign, queued, and never called with nothing saying why. That is the
    archive bug's shape, and this test is the reason the save refuses.
    """
    campaigns.update(running_campaign_id(), daily_cap=1000)
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET phone_e164='+14245556655',
                              pool_status='active', campaign_id=%s
                        WHERE lead_id=%s""",
                    (running_campaign_id(), lead['lead_id']))
    db.commit()
    # Windows NOT blanked here: that is the point.
    picked = {str(c['lead_id'])
              for c in dialer.select_and_claim(cfg_env, limit=10)}
    assert str(lead['lead_id']) not in picked, \
        'the premise: a NULL timezone matches no window'


def test_the_contact_save_refuses_a_phone_without_a_timezone(db, client):
    """
    ⚠️ THE TIMEZONE IS DERIVED FROM THE STATE, NOT ASKED FOR - every other entry
    path derives it, and asking here would make one fact arrive two ways, one of
    which a person can get wrong.

    So the refusal moved: a phone is accepted when a state exists to derive from,
    and refused when there is none, because the calling window is evaluated in the
    called party's local time and a number with no timezone would look dialable
    and never dial. That is the archive bug's exact shape - a row that reads as
    workable and is structurally unreachable.
    """
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET state = NULL WHERE lead_id = %s",
                        (lead['lead_id'],))
    r = client.post(f"/leads/{lead['lead_id']}/edit",
                    data={'phone_e164': '+14245554433', 'timezone': '',
                          'dm_name': 'Pat', 'dm_email': 'intake@whitfield.test'},
                    follow_redirects=False)
    assert 'REJECTED' in r.headers['location'], \
        'a phone was saved with no derivable timezone - the lead would look ' \
        'dialable and be permanently filtered out'
    assert _get(db, lead['lead_id'])['phone_e164'] is None, 'it saved anyway'


def test_a_phone_gets_its_timezone_from_the_state(db, client):
    """The other half: with a state there is nothing to ask about."""
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET state = 'NY' WHERE lead_id = %s",
                        (lead['lead_id'],))
    r = client.post(f"/leads/{lead['lead_id']}/edit",
                    data={'phone_e164': '+12125554433', 'timezone': '',
                          'dm_name': 'Pat', 'dm_email': 'intake@whitfield.test'},
                    follow_redirects=False)
    assert 'REJECTED' not in r.headers['location'], r.headers['location']
    got = _get(db, lead['lead_id'])
    assert got['phone_e164'] == '+12125554433'
    assert got['timezone'] == 'America/New_York', got['timezone']


# ==========================================================================
# the gate is source-aware, and the CALL path is untouched
# ==========================================================================

def _pass_domain(*a, **k):
    return {'domain_class': 'firm', 'reasons': []}


def test_an_imported_lead_passes_the_gate_without_a_name_or_confirmation(db):
    """
    Those two exclusions substitute for a human verifying the contact. For an
    import the evidence is a person choosing to upload the file - different in
    kind, asserted once for a batch.
    """
    upload.upload_emails(CSV)
    lead = dict(_by_email(db, 'intake@whitfield.test'))
    assert lead['dm_name'] is None and lead['dm_email_confirmed'] is not True
    d = autosend.eligibility(lead, {'is_running': True}, validate=_pass_domain,
                             drip_step=True)
    assert d['ok'] is True, d['reasons']


def test_the_call_path_is_unchanged_by_source_awareness(db):
    """
    THE OTHER HALF, and the one worth guarding. A call-sourced lead with no
    confirmation or no name is still HELD, exactly as before.
    """
    upload.upload_emails(CSV)
    lead = dict(_by_email(db, 'intake@whitfield.test'))
    lead['lead_source'] = 'call'            # same row, call provenance
    d = autosend.eligibility(lead, {'is_running': True}, validate=_pass_domain,
                             drip_step=True)
    assert d['ok'] is False
    assert any('confirm' in r for r in d['reasons']), d['reasons']
    assert any('name' in r for r in d['reasons']), d['reasons']


def test_the_domain_check_is_NOT_relaxed_for_an_import(db):
    """An imported address still has to look like it belongs to the firm. This is
    the exclusion that does the work once the other two are source-aware."""
    upload.upload_emails(CSV)
    lead = dict(_by_email(db, 'intake@whitfield.test'))
    d = autosend.eligibility(
        lead, {'is_running': True}, drip_step=True,
        validate=lambda *a, **k: {'domain_class': 'other',
                                  'reasons': ['email domain is neither the '
                                              "firm's website nor known free-mail"]})
    assert d['ok'] is False


def test_the_do_not_send_list_is_NOT_relaxed_for_an_import(db):
    upload.upload_emails(CSV)
    lead = dict(_by_email(db, 'intake@whitfield.test'))
    archive.do_not_send('intake@whitfield.test', 'unsubscribed', 'test')
    d = autosend.eligibility(lead, {'is_running': True}, validate=_pass_domain,
                             drip_step=True)
    assert d['ok'] is False
    assert any('do-not-send' in r for r in d['reasons']), d['reasons']


# ==========================================================================
# THE ANCHOR: step 1 of an imported lead IS email 1
# ==========================================================================

@pytest.fixture
def dripc(db):
    c = campaigns.create('IMP-DRIP', campaign_type='drip')
    cid = c['campaign_id']
    campaigns.start(cid)
    # THE GATE IS THE MEMBERSHIP: accept both an imported lead and one that has
    # had email 1, because this file tests both entry paths.
    campaigns.update(cid, accepted_statuses=['imported', 'emailed'])
    # ⚠️ BUSINESS HOURS GATE SELECTION SINCE THE PACING WORK, and a campaign is
    # created Mon-Fri 09:00-17:00 - so without this these tests pass or fail on
    # the wall clock, for a reason that has nothing to do with what they assert.
    # The hours have their own tests in test_pacing.py, which narrow the window
    # deliberately. Same rule as everywhere: a guard is only tested if the test
    # isolates it.
    open_all_hours(cid)
    drip.save_steps(cid, [
        {'delay_days': 0,  'subject': 'Opener',  'body': 'One {{sample_link}}'},
        {'delay_days': 4,  'subject': 'Second',  'body': 'Two'},
        {'delay_days': 10, 'subject': 'Third',   'body': 'Three'},
    ])
    return campaigns.get(cid)


def test_step_1_is_due_immediately_for_a_lead_with_no_emailed_at(db, dripc):
    """
    ⚠️ OPTION B: ONE ANCHOR, NOT TWO.

    A call-sourced lead reaches a drip by having email 1 sent, so emailed_at is
    already stamped. An imported lead is added DIRECTLY - there is no email 1
    before the sequence, because step 1 IS email 1.

    So a lead on a drip with no emailed_at has step 1 due now. The alternative was
    a separate sequence_started_at column, i.e. two columns that must agree
    forever; emailed_at already means "when the sequence started".
    """
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    assert lead['emailed_at'] is None
    # ⚠️ NOTHING ROUTED IT. The batch landed as `imported` and this drip accepts
    # that status, which is the whole of how it got here.
    assert lead['status'] == 'imported'
    assert any(str(d['campaign_id']) == str(dripc['campaign_id'])
               for d in drip.qualifies_for(dict(lead))), \
        'the imported lead does not qualify for a drip accepting `imported`'

    due = [r for r in drip.due() if str(r['lead_id']) == str(lead['lead_id'])]
    assert len(due) == 1
    assert due[0]['position'] == 1, 'step 1 must be the one that is due'


def test_a_later_step_is_not_due_before_the_clock_has_started(db, dripc):
    """Only step 1. Steps 2 and 3 have nothing to measure from yet, and must not
    all fire at once because emailed_at is NULL."""
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    rows = drip._build_select()
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            # The selection binds the operator timezone since pacing landed: the
            # daily cap's day boundary and the fallback for a lead with no
            # timezone of its own both read it.
            cur.execute(rows, {'op_tz': drip._op_tz()})
            mine = [r for r in cur.fetchall()
                    if str(r['lead_id']) == str(lead['lead_id'])]
    assert [r['position'] for r in mine] == [1], \
        'a NULL emailed_at made every step due at once'


def test_sending_step_1_starts_the_clock(db, dripc, cfg_env, monkeypatch):
    """
    And it does so through stages.mark_emailed - the SAME write-once transition
    the button and the sender use, not a second implementation. From that instant
    the lead is indistinguishable from a call-sourced one.
    """
    sent = []
    monkeypatch.setattr('api.mail.send',
                        lambda cfg, to, subj, body, **k: (
                            sent.append((to, subj)) or {'ok': True, 'detail': 'ok'}))
    monkeypatch.setattr('api.email_validation.check', _pass_domain)
    monkeypatch.setenv('EMAIL_MODE', 'unrestricted')
    from api.config import load_config
    cfg = load_config()

    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    row = [r for r in drip.due() if str(r['lead_id']) == str(lead['lead_id'])][0]
    res = drip.send_step(cfg, row)
    assert res['sent'] is True, res['detail']
    assert sent, 'no mail was actually handed to the sender'

    after = _get(db, lead['lead_id'])
    assert after['emailed_at'] is not None, \
        'step 1 sent but did not start the clock - every later step would be ' \
        'permanently undue'
    # And step 2 now anchors to it: 4 days out, so not due yet.
    nxt = [r for r in drip.due() if str(r['lead_id']) == str(lead['lead_id'])]
    assert nxt == [], 'step 2 was due immediately after step 1'


def test_email_1_counts_as_step_1_for_a_call_sourced_lead(db, dripc):
    """
    ⚠️ THE MIRROR CASE, AND IT WAS A REAL BUG.

    A call-sourced lead's email 1 is recorded with step_id NULL, because no drip
    existed when it went. ALREADY_SENT_STOP matches on step_id, so without
    linking it to step 1 the drip's step 1 (delay 0) is unsent and due
    IMMEDIATELY - the firm gets the same opener twice, minutes apart.
    """
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone, dm_email,
                              dm_email_confirmed, dm_name, has_confirmed_email,
                              campaign_id, status)
                       VALUES ('Called Co','+14245552323','America/Los_Angeles',
                               'bob@called.test',true,'Bob',true,%s,'completed')
                    RETURNING lead_id""", (running_campaign_id(),))
        lid = cur.fetchone()['lead_id']
    db.commit()
    # ⚠️ NO ROUTING. mark_emailed sets the status to `emailed` and this drip
    # accepts it - that is the entry. What has to hold is the OLD guarantee: the
    # firm must not get the opener twice.
    assert stages.mark_emailed(lid, emailed_by='operator') is not None
    after = _get(db, lid)
    assert after['status'] == 'emailed', after['status']
    assert any(str(d['campaign_id']) == str(dripc['campaign_id'])
               for d in drip.qualifies_for(dict(after))), \
        'sending email 1 did not put the lead in a drip accepting `emailed`'

    # THE LAZY LINK. Step 1 is considered once and linked instead of sent, which
    # is enter()'s surviving half moved to selection time.
    first = [r for r in drip.due() if str(r['lead_id']) == str(lid)
             and r['position'] == 1]
    if first:
        from api.config import load_config
        r = drip.send_step(load_config(), first[0])
        assert r['sent'] is False and 'linked' in r['detail'], r

    due = [r for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert [r['position'] for r in due] != [1], \
        'step 1 was due right after email 1 - the firm would get the opener twice'
    assert due == [], 'step 2 is four days out; nothing should be due yet'


# ==========================================================================
# default_drip_id: the mechanism, not only_drip()
# ==========================================================================

def test_emailed_and_on_no_drip_shows_in_the_needs_you_queue(db, client):
    """
    ⚠️ THE NET FOR WHATEVER default_drip_id DOES NOT COVER.

    No drip running, several with no default, or a default pointing at a stopped
    one: email 1 goes out and nothing follows up. No error, no failed send - the
    lead just sits at 'emailed'. It used to be visible only by opening that lead,
    which means finding out one firm at a time.
    """
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone, dm_email,
                              dm_email_confirmed, dm_name, has_confirmed_email,
                              campaign_id, status, emailed_at)
                       VALUES ('Stalled LLP','+14245554545','America/Los_Angeles',
                               'a@stalled.test',true,'Ann',true,%s,'emailed',now())
                    RETURNING lead_id""", (running_campaign_id(),))
        lid = cur.fetchone()['lead_id']
    db.commit()
    r = client.get('/today')
    assert r.status_code == 200
    assert 'Stalled LLP' in r.text, 'a stalled lead is invisible on /today'
    assert 'on no drip' in r.text, 'the queue does not say WHY it is there'

    # ⚠️ AND THE NEGATIVE, WITHOUT WHICH THIS TEST CANNOT FAIL. Break 110 removed
    # the "no running drip accepts its status" predicate and the test stayed GREEN,
    # because dropping it makes MORE leads match - including this one. A net that
    # catches everything is not a net. So: a lead a RUNNING drip does accept must
    # NOT be called stalled.
    cid = campaigns.create('DRIP-COVER', campaign_type='drip')['campaign_id']
    campaigns.start(cid)
    campaigns.update(cid, accepted_statuses=['emailed'])
    r = client.get('/today')
    assert 'Stalled LLP' not in r.text, \
        'a lead a running drip accepts is still reported as stalled - the net ' \
        'is matching every emailed lead rather than the unaccepted ones'


def test_a_replied_lead_on_no_drip_is_not_called_stalled(db, client):
    """It stopped ON PURPOSE. Flagging it would make the queue noise."""
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads (company, phone_e164, timezone, dm_email,
                              dm_email_confirmed, dm_name, has_confirmed_email,
                              campaign_id, status, emailed_at, replied_at)
                       VALUES ('Answered LLP','+14245554646','America/Los_Angeles',
                               'a@answered.test',true,'Ann',true,%s,'engaged',
                               now(), now())
                    RETURNING lead_id""", (running_campaign_id(),))
    db.commit()
    r = client.get('/today')
    assert 'Answered LLP' not in r.text


def test_an_imported_lead_is_not_flagged_as_email_unconfirmed(db, client):
    """That flag means "an agent failed to confirm it". An imported address was
    never on a call, so flagging every import would drown the queue."""
    upload.upload_emails(CSV)
    r = client.get('/today')
    assert 'Whitfield Law' not in r.text


# ==========================================================================
# the follow-up drip, chosen on the CALL campaign's screen
# ==========================================================================

def _lead_on_drip(db, cid):
    """A lead mid-sequence: on the drip, email 1 sent."""
    with db.cursor() as cur:
        cur.execute("""INSERT INTO leads
                           (company, phone_e164, timezone, pool_status, status,
                            has_confirmed_email, dm_email, dm_email_confirmed,
                            campaign_id, drip_campaign_id, drip_entered_at,
                            emailed_at, emailed_by)
                       VALUES ('Midseq', '+15557770123', 'America/Los_Angeles',
                               'active', 'emailed', true, 'm@midseq.test', true,
                               %s, %s, now(), now(), 'operator')
                    RETURNING lead_id""", (running_campaign_id(), cid))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


# ==========================================================================
# THE STATUS GATE replaces routing. These six tests replace the six that
# tested default_drip_id, one property at a time - not deleted, restated
# against the mechanism that decides now.
# ==========================================================================

def test_the_STATUS_decides_which_drips_a_lead_is_in(db, dripc):
    """
    Replaces test_the_call_campaign_chooses_which_drip_its_leads_enter.

    Nothing chooses. A lead is in every RUNNING drip whose accepted_statuses
    contain its status - one fact, consulted continuously, instead of a routing
    decision taken once and stored where it could go stale.
    """
    other = campaigns.create('DRIP-B', campaign_type='drip')['campaign_id']
    campaigns.start(other)
    campaigns.update(other, accepted_statuses=['engaged'])
    campaigns.update(dripc['campaign_id'], accepted_statuses=['emailed'])

    assert [d['name'] for d in drip.qualifies_for({'status': 'emailed'})] \
        == [dripc['name']]
    assert [d['name'] for d in drip.qualifies_for({'status': 'engaged'})] \
        == ['DRIP-B']
    assert drip.qualifies_for({'status': 'won'}) == [], \
        'a status no drip accepts must qualify for nothing'


def test_a_stopped_drip_sends_to_nobody_however_many_qualify(db, dripc):
    """
    Replaces test_a_default_pointing_at_a_stopped_drip_routes_nowhere. The old
    failure was a campaign pointing at a stopped drip; the shape survives, with
    the gate deciding instead of the pointer.
    """
    cid = dripc['campaign_id']
    lid = _lead_emailed(db, cid)
    assert [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'the premise: it is due while the drip runs'
    campaigns.stop(cid)
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'a stopped drip still selected a lead'
    # AND THE LEAD STILL QUALIFIES - stopping is not un-gating, so restarting
    # picks the same leads back up with their history intact.
    assert any(str(d['campaign_id']) == str(cid)
               for d in drip.qualifies_for(_by_id(db, lid)))


def test_a_gate_accepting_SEVERAL_statuses_admits_any_of_them(db, dripc):
    """Replaces the only_drip fallback test - there is no fallback to have."""
    cid = dripc['campaign_id']
    campaigns.update(cid, accepted_statuses=['emailed', 'max_attempts'])
    for st in ('emailed', 'max_attempts'):
        assert any(str(d['campaign_id']) == str(cid)
                   for d in drip.qualifies_for({'status': st})), st
    assert not drip.qualifies_for({'status': 'completed'})


def test_the_gate_is_settable_from_the_screen(db, dripc, client):
    """Replaces test_the_follow_up_drip_is_settable_and_unsettable_from_the_screen."""
    cid = dripc['campaign_id']
    base = {'name': dripc['name'], 'notes': '',
            'sender_email': dripc['sender_email'], 'sender_name': 'S',
            'sender_company_line': 'C', 'email_hourly_cap': '15',
            'email_daily_cap': '50', 'email_gap_min_seconds': '60',
            'email_gap_max_seconds': '300'}
    r = client.post(f'/campaign/{cid}/save',
                    data={**base, 'accepted_statuses': ['', 'emailed', 'imported']},
                    follow_redirects=False)
    assert 'REJECTED' not in r.headers['location'], r.headers['location']
    assert set(campaigns.get(cid)['accepted_statuses']) == {'emailed', 'imported'}

    # AND EMPTYING IT IS A REAL ANSWER - a drip that accepts nobody.
    r = client.post(f'/campaign/{cid}/save',
                    data={**base, 'accepted_statuses': ['']},
                    follow_redirects=False)
    assert campaigns.get(cid)['accepted_statuses'] == [], \
        'the last status could not be removed'


def test_a_gate_refuses_something_that_is_not_a_status(db, dripc, client):
    """Replaces the "cannot point at another call campaign" refusal."""
    cid = dripc['campaign_id']
    r = client.post(f'/campaign/{cid}/save', data={
        'name': dripc['name'], 'notes': '',
        'sender_email': dripc['sender_email'], 'sender_name': 'S',
        'sender_company_line': 'C', 'email_hourly_cap': '15',
        'email_daily_cap': '50', 'email_gap_min_seconds': '60',
        'email_gap_max_seconds': '300',
        'accepted_statuses': ['', 'not_a_status'],
    }, follow_redirects=False)
    assert 'REJECTED' in r.headers['location'], r.headers['location']


def test_leaving_and_returning_RESUMES_and_does_not_resend(db, dripc):
    """
    Replaces test_changing_the_follow_up_drip_does_not_move_leads_already_on_one -
    and inverts it, because the guarantee changed on purpose.

    ⚠️ THE OLD RULE was that a config change must not move a lead mid-sequence.
    Under the gate a status change is EXACTLY how a lead leaves, immediately, and
    that is the property that fixes a replied lead still being queued. What must
    survive is the HISTORY: emailed -> engaged -> emailed picks up where it left
    off, because ALREADY_SENT_STOP matches on (lead_id, step_id) and those rows
    persist through the gap. A restart would re-send step 1 to a firm that has
    already had it.
    """
    cid = dripc['campaign_id']
    campaigns.update(cid, accepted_statuses=['emailed'])
    lid = _lead_emailed(db, cid, sent_steps=2)
    before = [r['position'] for r in drip.due()
              if str(r['lead_id']) == str(lid)]
    assert before == [3], f'expected step 3 next, got {before}'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='engaged' WHERE lead_id=%s",
                        (lid,))
    assert not [r for r in drip.due() if str(r['lead_id']) == str(lid)], \
        'leaving the accepted set did not stop the next send'

    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET status='emailed' WHERE lead_id=%s",
                        (lid,))
    after = [r['position'] for r in drip.due() if str(r['lead_id']) == str(lid)]
    assert after == [3], \
        f'returning restarted the sequence instead of resuming: {after}'


import itertools as _it
_counter = _it.count(1000)


def _lead_emailed(db, cid, sent_steps=0):
    """A lead that has had email 1, qualifying by status, with N steps sent."""
    from tests.test_drip import _lead as _mk, _join as _jn
    lid = _mk(db, days_ago=40, phone_e164=f'+1555111{next(_counter)}')
    _jn(db, lid, cid, sent_steps=sent_steps)
    return lid


def _by_id(db, lid):
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM leads WHERE lead_id = %s', (lid,))
            return dict(cur.fetchone())


def test_keeping_an_imported_lead_where_it_is_changes_nothing(db, client):
    """
    The other branch of the prompt. "Keep" means the lead gains a phone and stays
    `imported`, so it stays in whatever drip accepts that - the number is a fact
    about the firm, not a decision about what to do with it.
    """
    upload.upload_emails(CSV)
    lead = _by_email(db, 'intake@whitfield.test')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET state = 'CA' WHERE lead_id = %s",
                        (lead['lead_id'],))
    r = client.post(f"/leads/{lead['lead_id']}/edit",
                    data={'phone_e164': '+14245551122', 'timezone': '',
                          'dm_name': 'Pat', 'dm_email': 'intake@whitfield.test'},
                    follow_redirects=False)
    assert 'REJECTED' not in r.headers['location'], r.headers['location']
    got = _get(db, lead['lead_id'])
    assert got['status'] == 'imported', \
        f"'keep' changed the status to {got['status']} - it must not"
    assert got['phone_e164'] == '+14245551122', 'the number was not saved'
