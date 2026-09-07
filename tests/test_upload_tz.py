"""State -> IANA derivation, and draft emails."""
import pytest
from api import drafts, timezones, upload


@pytest.mark.parametrize('state,tz', [
    ('CA', 'America/Los_Angeles'), ('NY', 'America/New_York'),
    ('IL', 'America/Chicago'), ('CO', 'America/Denver'),
    ('HI', 'Pacific/Honolulu'), ('AK', 'America/Anchorage'),
])
def test_single_zone_states_are_derived_without_review(state, tz):
    got, source, review = timezones.for_state(state)
    assert got == tz and source == 'state' and review is False


def test_arizona_is_phoenix_not_denver():
    """AZ does not observe DST - Denver would be an hour out most of the year."""
    tz, _, review = timezones.for_state('AZ')
    assert tz == 'America/Phoenix'
    assert review is False


@pytest.mark.parametrize('state', ['TX', 'FL', 'TN', 'KY', 'IN', 'ND',
                                   'SD', 'NE', 'KS', 'MI', 'OR', 'ID'])
def test_split_states_use_the_dominant_zone_and_are_flagged(state):
    tz, source, review = timezones.for_state(state)
    assert source == 'state'
    assert review is True, f'{state} spans zones and must be flagged for review'
    assert tz.startswith(('America/', 'Pacific/'))


def test_an_unknown_state_is_flagged_not_guessed():
    _, source, review = timezones.for_state('ZZ')
    assert source == 'default' and review is True


def test_upload_derives_timezone_from_state(db):
    r = upload.upload('company,phone,state\nWhitfield,+14245551000,CA')
    assert r['inserted'] == 1
    with db.cursor() as cur:
        cur.execute('SELECT timezone, tz_source, tz_needs_review FROM leads')
        row = cur.fetchone()
    assert row['timezone'] == 'America/Los_Angeles'
    assert row['tz_source'] == 'state'
    assert row['tz_needs_review'] is False


def test_an_explicit_timezone_column_overrides_the_state(db):
    """The CSV is authoritative when it says so."""
    r = upload.upload('company,phone,state,timezone\n'
                      'Whitfield,+14245551001,CA,America/New_York')
    assert r['inserted'] == 1
    with db.cursor() as cur:
        cur.execute('SELECT timezone, tz_source FROM leads')
        row = cur.fetchone()
    assert row['timezone'] == 'America/New_York'
    assert row['tz_source'] == 'csv'


def test_a_split_state_row_is_flagged_for_review(db):
    r = upload.upload('company,phone,state\nBigTex,+14245551002,TX')
    assert r['timezone_needs_review'] == 1
    with db.cursor() as cur:
        cur.execute('SELECT tz_needs_review FROM leads')
        assert cur.fetchone()['tz_needs_review'] is True


def test_a_file_with_neither_timezone_nor_state_is_rejected(db):
    rows, rejects = upload.parse_csv('company,phone\nX,+14245551003')
    assert rows == []
    assert 'state' in rejects[0]['reason']


# --------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------

def _lead(db, **kw):
    cols = {'company': 'Whitfield & Associates', 'phone_e164': '+14245552000',
            'timezone': 'America/Los_Angeles', 'pool_status': 'active',
            'status': 'completed', 'dm_name': 'Bob Smith',
            'dm_email': 'bob@whitfieldlaw.com', 'dm_email_confirmed': True}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


def test_the_draft_references_the_call_not_cold_outreach(db):
    """Without this it is cold outreach and the phone call was wasted."""
    lid = _lead(db)
    d = drafts.generate_for(lid)
    assert 'pointed me your way' in d['body']
    assert 'front desk' in d['body']


def test_it_never_says_gave_me_your_contact_info(db):
    """That phrasing reads like she handed over data and gets her in trouble."""
    lid = _lead(db, gatekeeper_name='Sara')
    d = drafts.generate_for(lid)
    assert 'contact info' not in d['body'].lower()
    assert 'pointed me your way' in d['body']


def test_the_with_name_variant_uses_the_gatekeepers_name(db):
    lid = _lead(db, gatekeeper_name='Sara')
    d = drafts.generate_for(lid)
    assert d['variant'] == 'with_name'
    assert 'Sara at your front desk pointed me your way' in d['body']
    assert d['subject'] == 'Following up — spoke with your front desk'


def test_the_without_name_variant_is_used_when_she_did_not_give_it(db):
    lid = _lead(db)
    d = drafts.generate_for(lid)
    assert d['variant'] == 'without_name'
    assert 'I called your office' in d['body']


def test_subject_and_body_agree_on_the_time_of_day(db):
    """They are one email - 'this morning' in the subject and 'this afternoon'
    in the body would be visibly wrong."""
    import datetime
    for hour, word in ((9, 'this morning'), (15, 'this afternoon')):
        called = datetime.datetime(2026, 9, 7, hour, 0,
                                   tzinfo=datetime.timezone.utc)
        d = drafts.build({'company': 'X', 'dm_name': 'Bob',
                          'dm_email': 'b@x.com', 'timezone': 'UTC',
                          'last_called_at': called})
        assert word in d['body']
        assert word in d['subject']


def test_the_sample_is_linked_never_attached(db):
    """Law-firm mail security strips attachments; a click is a signal."""
    lid = _lead(db)
    d = drafts.generate_for(lid)
    assert 'https://counselorai.io/#letter' in d['body']
    assert 'attach' not in d['body'].lower()


def test_the_draft_carries_an_unsubscribe_line(db):
    lid = _lead(db)
    d = drafts.generate_for(lid)
    assert 'unsubscribe' in d['body'].lower()


def test_no_draft_without_a_CONFIRMED_email(db):
    assert drafts.generate_for(_lead(db, dm_email_confirmed=False)) is None
    assert drafts.generate_for(_lead(db, phone_e164='+14245552001',
                                     dm_email=None, dm_email_confirmed=None)) is None


def test_a_human_edit_is_not_clobbered_by_regeneration(db):
    lid = _lead(db)
    drafts.generate_for(lid)
    drafts.save_edit(lid, 'My subject', 'My body')
    assert drafts.generate_for(lid) is None          # not forced -> left alone
    assert drafts.get(lid)['body'] == 'My body'
    drafts.generate_for(lid, force=True)             # explicit -> replaced
    assert drafts.get(lid)['body'] != 'My body'


def test_nothing_in_the_repo_sends_a_draft():
    """The whole point: generated, never sent."""
    import inspect
    src = inspect.getsource(drafts)
    for token in ('smtp', 'mail.send', 'sendgrid', 'brevo'):
        assert token not in src.lower(), f'{token} must not appear in drafts.py'


def test_sender_identity_comes_from_settings_not_env(db):
    from api import settings as s
    s.set_many({'sender_name': 'Testy', 'sender_email': 't@example.com'})
    d = drafts.build({'company': 'X', 'dm_name': 'Bob', 'dm_email': 'b@x.com',
                      'timezone': 'UTC', 'last_called_at': None})
    assert 'Testy' in d['body']
