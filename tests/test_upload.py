"""
CSV upload. Bad rows are REPORTED, never silently fixed or dropped.
"""

import pytest

from api import upload

LA = 'America/Los_Angeles'


@pytest.mark.parametrize('raw,expected', [
    ('+14242002295', '+14242002295'),
    ('4242002295', '+14242002295'),
    ('(424) 200-2295', '+14242002295'),
    ('424-200-2295', '+14242002295'),
    ('1 424 200 2295', '+14242002295'),
    ('+44 20 7946 0958', '+442079460958'),
])
def test_phone_normalisation(raw, expected):
    assert upload.normalize_phone(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', 'not a phone', '12345', 'abc',
                                 '00000000000', '+0123456789'])
def test_unparseable_phones_are_rejected_not_guessed(raw):
    assert upload.normalize_phone(raw) is None


@pytest.mark.parametrize('raw', ['America/New_York', 'Europe/London', 'Asia/Tokyo'])
def test_valid_iana_timezones(raw):
    assert upload.validate_timezone(raw) == raw


@pytest.mark.parametrize('raw', ['-05:00', '+0530', 'EST', 'PST', 'Eastern',
                                 'America/New_york', '', 'UTC-5'])
def test_offsets_and_abbreviations_are_rejected(raw):
    """An offset is wrong twice a year; an abbreviation is ambiguous."""
    assert upload.validate_timezone(raw) is None


def test_missing_required_column_rejects_the_whole_file():
    rows, rejects = upload.parse_csv('company,phone\nA,+14242002295')
    assert rows == []
    assert 'timezone' in rejects[0]['reason']


def test_bad_rows_are_reported_with_line_numbers(db):
    csv_text = '\n'.join([
        'company,phone,timezone',
        f'Good Firm,+14242002295,{LA}',
        f'Bad Phone,not-a-number,{LA}',
        'Bad TZ,+14242002296,-05:00',
        f',+14242002297,{LA}',
    ])
    report = upload.upload(csv_text)
    assert report['inserted'] == 1
    assert report['rejected'] == 3
    lines = {r['line'] for r in report['rejects']}
    assert lines == {3, 4, 5}


def test_upload_lands_in_the_pool_never_active(db):
    upload.upload(f'company,phone,timezone\nA Firm,+14242002295,{LA}')
    with db.cursor() as cur:
        cur.execute('SELECT pool_status, status FROM leads')
        row = cur.fetchone()
    assert row['pool_status'] == 'pool'
    assert row['status'] == 'new'


def test_duplicate_phone_is_skipped_not_updated(db):
    """Re-uploading a list must not resurrect a lead that has gone dnc."""
    upload.upload(f'company,phone,timezone\nA Firm,+14242002295,{LA}')
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET status='dnc' WHERE phone_e164='+14242002295'")
    db.commit()

    report = upload.upload(f'company,phone,timezone\nA Firm,+14242002295,{LA}')
    assert report['inserted'] == 0
    assert report['duplicates'] == 1
    with db.cursor() as cur:
        cur.execute("SELECT status FROM leads WHERE phone_e164='+14242002295'")
        assert cur.fetchone()['status'] == 'dnc'


def test_optional_columns_are_carried(db):
    upload.upload('company,phone,timezone,city,state,segment,external_ref\n'
                  f'A Firm,+14242002295,{LA},Los Angeles,CA,pi,EXT-1')
    with db.cursor() as cur:
        cur.execute('SELECT city, state, segment, external_ref FROM leads')
        r = cur.fetchone()
    assert (r['city'], r['state'], r['segment'], r['external_ref']) == \
           ('Los Angeles', 'CA', 'pi', 'EXT-1')
