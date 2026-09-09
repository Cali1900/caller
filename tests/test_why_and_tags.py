"""
"WHY IS IT HERE" - one line, two places, one generator.

The point of searching for a firm is to find out where it stands. A search
result that makes you click through to learn that has not answered the
question, so the line has to render on the LIST as well as on lead detail -
and it has to be the same line, because two generators drift and the one on
the list is the one that would quietly go stale.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from api import archive, campaigns, db as dbm, web, why
from tests.conftest import running_campaign_id

NY = 'America/New_York'


@pytest.fixture
def client(db, cfg_env):
    import api.web            # noqa: F401
    from api.main import app
    return TestClient(app)


def _lead(db, **kw):
    cols = {'company': 'Whitfield Law', 'phone_e164': '+19195570001',
            'timezone': NY, 'state': 'NC', 'pool_status': 'active',
            'status': 'new', 'stage': 'L1',
            'campaign_id': running_campaign_id()}
    cols.update(kw)
    keys = ', '.join(cols); ph = ', '.join(['%s'] * len(cols))
    with db.cursor() as cur:
        cur.execute(f'INSERT INTO leads ({keys}) VALUES ({ph}) RETURNING lead_id',
                    list(cols.values()))
        lid = cur.fetchone()['lead_id']
    db.commit()
    return lid


def _call(db, lid, reason):
    with db.cursor() as cur:
        cur.execute("""INSERT INTO calls (call_id, lead_id, stage,
                                         disconnection_reason)
                       VALUES (gen_random_uuid()::text, %s, 'L1', %s)""",
                    (lid, reason))
    db.commit()


def _row(lid):
    with dbm.get_conn() as conn:
        return web._why_row(conn, lid)


# --------------------------------------------------------------------------
# the sentence
# --------------------------------------------------------------------------

def test_it_counts_calls_and_says_how_often_a_human_answered(db):
    lid = _lead(db)
    _call(db, lid, 'dial_no_answer')
    _call(db, lid, 'dial_busy')
    _call(db, lid, 'user_hangup')          # a person picked up
    line = why.line(_row(lid))
    assert 'Called 3 times' in line
    assert 'reached a human once' in line


def test_never_reaching_anyone_says_so_rather_than_staying_silent(db):
    lid = _lead(db)
    _call(db, lid, 'dial_no_answer')
    _call(db, lid, 'dial_no_answer')
    assert 'Called twice, never reached a human' in why.line(_row(lid))


def test_an_untouched_lead_says_it_has_not_been_called(db):
    assert why.line(_row(_lead(db))).startswith('Not called yet')


def test_it_names_who_gave_the_email_and_when(db):
    lid = _lead(db, dm_name='Bob Smith', dm_email='bob@whitfield.example',
                dm_email_confirmed=True, stage='L2',
                stage_changed_at=datetime.datetime(2026, 9, 8, 15, 0,
                                                   tzinfo=datetime.timezone.utc))
    line = why.line(_row(lid))
    assert 'Bob Smith gave their email Sep 8' in line


def test_an_unconfirmed_email_is_called_unconfirmed(db):
    """It is the reason a lead sits at L1 with an address on it and nothing
    sends. Reading 'gave their email' would make that look like a bug."""
    lid = _lead(db, dm_name='Bob Smith', dm_email='bob@whitfield.example',
                dm_email_confirmed=False)
    assert 'unconfirmed' in why.line(_row(lid))


def test_it_reports_the_send_and_the_clicks(db):
    lid = _lead(db, dm_name='Bob', dm_email='bob@whitfield.example',
                dm_email_confirmed=True, stage='L2',
                emailed_at=datetime.datetime(2026, 9, 8, 15, 0,
                                             tzinfo=datetime.timezone.utc))
    with db.cursor() as cur:
        # DISTINCT instants: email_clicks is unique on (lead_id, clicked_at),
        # which is what stops a mail client's double-fetch counting twice.
        for mins in (47, 300):
            cur.execute("""INSERT INTO email_clicks (lead_id, clicked_at,
                                                     minutes_since_sent)
                           VALUES (%s, now() - (%s || ' minutes')::interval, %s)""",
                        (lid, mins, mins))
    db.commit()
    line = why.line(_row(lid))
    assert 'Emailed Sep 8' in line
    assert 'clicked the sample twice' in line
    assert 'Waiting on a reply' in line


def test_a_reply_replaces_waiting_with_yours_to_work(db):
    lid = _lead(db, stage='L2', status='engaged',
                emailed_at=datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc),
                replied_at=datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc))
    line = why.line(_row(lid))
    assert 'They replied Sep 9' in line
    assert 'Waiting on a reply' not in line


def test_an_archived_lead_says_why_and_when_it_comes_back(db):
    """'Archived Sep 20 after 4 emails, no reply. Returns to the pool Mar 20.'
    Without the reason the return queue is undifferentiated and useless."""
    lid = _lead(db)
    archive.archive(lid, 'no_reply', by='test')
    line = why.line(_row(lid))
    assert 'Archived' in line
    assert 'no reply' in line
    assert 'Returns to the pool' in line


def test_the_line_never_ends_mid_sentence(db):
    """Empty clauses used to leave a trailing comma or a bare full stop."""
    for lid in (_lead(db, phone_e164='+19195570090'),
                _lead(db, phone_e164='+19195570091', dm_name='Bob',
                      dm_email='b@w.example', dm_email_confirmed=True)):
        line = why.line(_row(lid))
        assert not line.endswith(', ') and '..' not in line
        assert '  ' not in line
        if line:
            assert line.endswith('.')


# --------------------------------------------------------------------------
# ONE generator, TWO places
# --------------------------------------------------------------------------

def test_the_same_line_renders_on_the_list_and_on_lead_detail(client, db):
    """
    ⚠️ THE POINT OF THE WHOLE ITEM. Sean searches a firm to find out where it
    stands; if the list and lead detail can say different things, the list is
    the one that goes stale and it is the one he reads first.

    Asserted against RENDERED HTML on both screens, not against the template
    source - two regex "alignment checkers" have already both lied.
    """
    lid = _lead(db, company='Seans Law', dm_name='Bob Smith',
                dm_email='bob@seanslaw.example', dm_email_confirmed=True,
                stage='L2', status='emailed',
                emailed_at=datetime.datetime(2026, 9, 8, 15, 0,
                                             tzinfo=datetime.timezone.utc))
    _call(db, lid, 'dial_no_answer')
    _call(db, lid, 'user_hangup')

    expected = why.line(_row(lid))
    assert expected, 'the fixture produced no line at all'

    list_body = ' '.join(client.get('/?q=Seans+Law').text.split())
    detail_body = ' '.join(client.get(f'/leads/{lid}').text.split())
    want = ' '.join(expected.split())
    assert want in list_body, 'the search result did not carry the line'
    assert want in detail_body, 'lead detail did not carry the line'


def test_a_search_answers_where_it_is_and_why_without_a_second_click(client, db):
    lid = _lead(db, company='Seans Law', phone_e164='+19195570002',
                dm_name='Bob Smith', dm_email='bob@seanslaw.example',
                dm_email_confirmed=True, stage='L2', status='engaged',
                emailed_at=datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc),
                replied_at=datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc))
    _call(db, lid, 'user_hangup')
    body = ' '.join(client.get('/?q=Seans+Law').text.split())
    assert 'Seans Law' in body
    assert 'reached a human once' in body
    assert 'Bob Smith gave their email' in body
    assert 'They replied Sep 9' in body


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------

def test_a_tag_is_free_text_and_several_can_be_added_at_once(client, db):
    lid = _lead(db)
    client.post(f'/leads/{lid}/tags',
                data={'tag': 'Big Firm, referred by X'},
                follow_redirects=False)
    assert web._tags_for(lid) == ['big firm', 'referred by x']


def test_the_same_tag_twice_is_one_tag(client, db):
    """Stored twice they filter as two and neither finds everything."""
    lid = _lead(db)
    client.post(f'/leads/{lid}/tags', data={'tag': 'big firm'}, follow_redirects=False)
    client.post(f'/leads/{lid}/tags', data={'tag': '  BIG   FIRM '}, follow_redirects=False)
    assert web._tags_for(lid) == ['big firm']


def test_filtering_by_tag_finds_only_the_tagged(client, db):
    a = _lead(db, company='Tagged Firm', phone_e164='+19195570010')
    _lead(db, company='Untagged Firm', phone_e164='+19195570011')
    client.post(f'/leads/{a}/tags', data={'tag': 'big firm'}, follow_redirects=False)
    body = client.get('/?tag=big+firm').text
    assert 'Tagged Firm' in body
    assert 'Untagged Firm' not in body


def test_the_tag_filter_reaches_the_count_and_the_bulk_add(client, db):
    """
    A filter the bulk add cannot see means "Add all N matching" adds a
    different set from the one on screen. That has shipped once already,
    reading six filters of sixteen.
    """
    a = _lead(db, company='Tagged Firm', phone_e164='+19195570020')
    _lead(db, company='Untagged Firm', phone_e164='+19195570021')
    client.post(f'/leads/{a}/tags', data={'tag': 'big firm'}, follow_redirects=False)
    _sql, _p, where, cparams = web._lead_query('', '', '', '', 100, 0,
                                               tag='big firm')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n {web._LEAD_JOINS} "
                        f"WHERE {' AND '.join(where)}", cparams)
            assert cur.fetchone()['n'] == 1


def test_a_removed_tag_is_gone(client, db):
    lid = _lead(db)
    client.post(f'/leads/{lid}/tags', data={'tag': 'big firm'}, follow_redirects=False)
    client.post(f'/leads/{lid}/tags/remove', data={'tag': 'big firm'},
                follow_redirects=False)
    assert web._tags_for(lid) == []


def test_deleting_a_lead_takes_its_tags_with_it(db):
    """A tag is a note about a row, not a record of contact - unlike
    dial_audit it has no reason to outlive the lead."""
    lid = _lead(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO lead_tags (lead_id, tag) VALUES (%s,'x')", (lid,))
        cur.execute('DELETE FROM leads WHERE lead_id = %s', (lid,))
        cur.execute('SELECT count(*) AS n FROM lead_tags WHERE lead_id = %s', (lid,))
        assert cur.fetchone()['n'] == 0
    db.rollback()
