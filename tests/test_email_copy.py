"""
THE EMAIL COPY IS A PROPERTY OF THE CAMPAIGN.

Two campaigns running different copy is most of the reason to have a second
campaign, so the thing worth testing is not that a template renders - it is
that two campaigns CANNOT end up sharing one.
"""

import pytest
from fastapi.testclient import TestClient

from api import campaigns as c, drafts


@pytest.fixture
def client(db, cfg_env):
    import api.web as web            # noqa: F401 - registers the routes
    from api.main import app
    return TestClient(app)


def _lead(db, campaign_id, **over):
    row = {'phone_e164': '+14245559100', 'company': 'Whitfield Injury Law',
           'dm_name': 'Sara Whitfield', 'dm_email': 'sara@w.example',
           'dm_email_confirmed': True, 'gatekeeper_name': 'Denise',
           'timezone': 'America/Los_Angeles', 'last_called_at': None,
           'campaign_id': campaign_id}
    row.update(over)
    return row


def test_a_new_campaign_starts_with_the_current_copy(db):
    """'Keep the current template as the default on new campaigns.'"""
    row = c.create('FRESH')
    for f in c.TEMPLATE_FIELDS:
        assert row[f] == c.DEFAULT_TEMPLATE[f]


def test_two_campaigns_can_run_different_copy(db):
    a = c.create('A')
    b = c.create('B')
    c.update(a['campaign_id'], body_with_name='AAA {{first_name}} AAA',
             subject_with_name='SUBJ-A')
    c.update(b['campaign_id'], body_with_name='BBB {{first_name}} BBB',
             subject_with_name='SUBJ-B')

    da = drafts.build(_lead(db, a['campaign_id']))
    dbb = drafts.build(_lead(db, b['campaign_id']))
    assert da['subject'] == 'SUBJ-A' and da['body'] == 'AAA Sara AAA'
    assert dbb['subject'] == 'SUBJ-B' and dbb['body'] == 'BBB Sara BBB'


def test_editing_one_campaigns_copy_does_not_touch_another(db):
    a, b = c.create('A'), c.create('B')
    before = c.get(b['campaign_id'])['body_with_name']
    c.update(a['campaign_id'], body_with_name='changed')
    assert c.get(b['campaign_id'])['body_with_name'] == before


@pytest.mark.parametrize('ph,field,expect', [
    ('{{first_name}}', 'dm_name', 'Sara'),
    ('{{gatekeeper_name}}', 'gatekeeper_name', 'Denise'),
    ('{{company}}', 'company', 'Whitfield Injury Law'),
])
def test_each_placeholder_is_substituted(db, ph, field, expect):
    row = c.create('PH-' + field)
    c.update(row['campaign_id'], body_with_name='[' + ph + ']')
    d = drafts.build(_lead(db, row['campaign_id']))
    assert d['body'] == '[' + expect + ']'


def test_sender_and_footer_are_placeholders_so_editing_the_sender_flows_through(db):
    """Baking the signature in as literal text would mean re-editing every
    template after changing the sender."""
    row = c.create('SND', sender_name='Dana', sender_company_line='ACME LLC')
    c.update(row['campaign_id'], body_with_name='{{sender_name}} / {{footer}}')
    assert drafts.build(_lead(db, row['campaign_id']))['body'] == 'Dana / ACME LLC'
    c.update(row['campaign_id'], sender_name='Robin')
    assert drafts.build(_lead(db, row['campaign_id']))['body'] == 'Robin / ACME LLC'


def test_an_unknown_placeholder_is_left_visible_not_blanked(db):
    """A stray {{typo}} in the preview is a mistake the operator can see. A
    silently-deleted one is a sentence with a hole in it that nobody notices."""
    row = c.create('TYPO')
    c.update(row['campaign_id'], body_with_name='Hi {{frist_name}}, all good')
    assert '{{frist_name}}' in drafts.build(_lead(db, row['campaign_id']))['body']


def test_the_variant_follows_the_gatekeeper_name(db):
    row = c.create('VAR')
    c.update(row['campaign_id'], body_with_name='WITH', body_without='WITHOUT',
             subject_with_name='SW', subject_without='SO')
    with_name = drafts.build(_lead(db, row['campaign_id']))
    without = drafts.build(_lead(db, row['campaign_id'], gatekeeper_name=None))
    assert (with_name['variant'], with_name['body']) == ('with_name', 'WITH')
    assert (without['variant'], without['body']) == ('without_name', 'WITHOUT')


def test_a_lead_with_no_campaign_still_gets_the_default_copy(db):
    """An empty body is a blank email that could reach a person."""
    d = drafts.build(_lead(db, None))
    assert d['subject'].strip() and 'Sara' in d['body']


def test_the_preview_renders_unsaved_text_without_saving_it(db):
    row = c.create('PV')
    out = drafts.preview(row, lead=_lead(db, row['campaign_id']),
                         overrides={'body_with_name': 'UNSAVED {{first_name}}'})
    assert out['with_name']['body'] == 'UNSAVED Sara'
    assert c.get(row['campaign_id'])['body_with_name'] == c.DEFAULT_TEMPLATE['body_with_name']


def test_the_preview_shows_both_variants(db):
    row = c.create('PV2')
    c.update(row['campaign_id'], body_with_name='W', body_without='N')
    fresh = c.get(row['campaign_id'])          # update() returns a new row
    out = drafts.preview(fresh, lead=_lead(db, row['campaign_id']))
    assert out['with_name']['body'] == 'W' and out['without_name']['body'] == 'N'


def test_the_preview_says_so_when_it_has_no_real_lead(db):
    """A preview against invented data must announce itself, or it is
    indistinguishable from a preview against a lead."""
    lead, real = drafts.preview_lead(c.create('EMPTY')['campaign_id'])
    assert real is False and lead['company'] == 'Whitfield Injury Law'


def test_the_generated_draft_uses_the_campaigns_copy(db):
    """Not just build() - the draft that lands in the database."""
    from api import db as dbm
    row = c.create('GEN')
    c.update(row['campaign_id'], body_with_name='CAMPAIGN COPY {{first_name}}')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, gatekeeper_name,
                               timezone, campaign_id)
                           VALUES (%s,%s,%s,%s,true,%s,%s,%s) RETURNING lead_id""",
                        ('+14245559101', 'W', 'Sara Whitfield', 's@w.example',
                         'Denise', 'America/Los_Angeles', row['campaign_id']))
            lid = cur.fetchone()['lead_id']
    assert drafts.generate_for(lid)['body'] == 'CAMPAIGN COPY Sara'
    assert drafts.get(lid)['body'] == 'CAMPAIGN COPY Sara'


# ---------------------------------------------------------------------------
# the draft's To: must follow a corrected contact email
# ---------------------------------------------------------------------------

def _lead_with_draft(db, email='old@firm.example'):
    from api import campaigns as c, db as dbm, drafts as d
    cid = c.create('RT-' + email[:4])['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, gatekeeper_name,
                               timezone, campaign_id, stage)
                           VALUES (%s,'W','Sara Whitfield',%s,true,'Denise',
                                   'America/Los_Angeles',%s,'L2')
                           RETURNING lead_id""",
                        ('+1424555' + str(abs(hash(email)) % 9000 + 1000), email, cid))
            lid = cur.fetchone()['lead_id']
    d.generate_for(lid)
    return lid


def test_correcting_the_email_retargets_an_unsent_draft(db):
    """
    The bug: the draft keeps its own copy of the address, so correcting the
    contact left the OLD address sitting in the draft you copy from.
    """
    from api import drafts as d
    lid = _lead_with_draft(db)
    assert d.get(lid)['to_email'] == 'old@firm.example'
    assert d.retarget(lid, 'new@firm.example') == 'updated'
    assert d.get(lid)['to_email'] == 'new@firm.example'


def test_a_sent_draft_keeps_the_address_it_was_sent_to(db):
    """to_email on a sent draft is a record of where the mail went. Rewriting
    it would falsify history."""
    from api import db as dbm, drafts as d, stages
    lid = _lead_with_draft(db, 'sent@firm.example')
    stages.mark_emailed(lid, emailed_by='operator')
    assert d.retarget(lid, 'changed@firm.example') == 'already_sent'
    assert d.get(lid)['to_email'] == 'sent@firm.example'
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM activity
                            WHERE lead_id=%s
                              AND summary='contact email changed AFTER sending'""",
                        (lid,))
            assert cur.fetchone()['n'] == 1, 'the divergence must be visible'


def test_retargeting_to_the_same_address_is_a_no_op(db):
    from api import drafts as d
    lid = _lead_with_draft(db, 'same@firm.example')
    assert d.retarget(lid, 'same@firm.example') is None


def test_editing_the_contact_through_the_ui_retargets_the_draft(client, db):
    """End to end - the route, not just the helper."""
    from api import drafts as d
    lid = _lead_with_draft(db, 'ui@firm.example')
    r = client.post(f'/leads/{lid}/edit',
                    data={'dm_name': 'Sara Whitfield', 'dm_title': '',
                          'dm_email': 'corrected@firm.example',
                          'dm_email_confirmed': 'true', 'notes': ''},
                    follow_redirects=False)
    assert r.status_code == 303
    assert d.get(lid)['to_email'] == 'corrected@firm.example'


# ---------------------------------------------------------------------------
# the email state on the leads list
# ---------------------------------------------------------------------------

def _state_of(lid):
    """Read the state through the SAME expression the list and filter use."""
    from api import db as dbm, web
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            # The SAME joins the list query uses - the expression references
            # both, and a helper that joins differently would test a state the
            # screen never shows.
            cur.execute(f"""SELECT ({web._EMAIL_STATE.strip()}) AS s
                              FROM leads l
                              LEFT JOIN email_drafts d ON d.lead_id = l.lead_id
                              LEFT JOIN LATERAL (
                                  SELECT count(*) AS clicks
                                    FROM email_clicks ec
                                   WHERE ec.lead_id = l.lead_id) ck ON true
                             WHERE l.lead_id = %s""", (lid,))
            return cur.fetchone()['s']


def test_a_lead_with_no_draft_has_no_email_state(db):
    from api import db as dbm
    cid = c.create('ES-none')['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, timezone, campaign_id)
                           VALUES ('+14245558800','W','America/Los_Angeles',%s)
                           RETURNING lead_id""", (cid,))
            lid = cur.fetchone()['lead_id']
    assert _state_of(lid) == 'none'


def test_a_generated_draft_reads_as_draft_ready(db):
    lid = _lead_with_draft(db, 'ready@firm.example')
    assert _state_of(lid) == 'draft_ready'


def test_marking_it_sent_moves_the_state_on(db):
    from api import stages
    lid = _lead_with_draft(db, 'moved@firm.example')
    stages.mark_emailed(lid, emailed_by='operator')
    assert _state_of(lid) == 'sent'


def test_a_reply_outranks_a_send(db):
    """Precedence is the order things happen in. `replied_at` is INERT today -
    nothing writes it - so this pins the rendering, not the detection."""
    from api import db as dbm, stages
    lid = _lead_with_draft(db, 'replied@firm.example')
    stages.mark_emailed(lid, emailed_by='operator')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE leads SET replied_at = now() WHERE lead_id = %s', (lid,))
    assert _state_of(lid) == 'replied'


def test_reply_detection_is_not_wired_up_yet(db):
    """
    Guards the claim that 'replied' is INERT.

    stages.record_reply() DOES exist - it is a deliberate seam so the dialer's
    replied_at guard has a matching writer. What does not exist is anything
    that DETECTS a reply and calls it. When that lands, this test is what says
    so, instead of the column quietly starting to mean something.
    """
    import glob
    import re
    callers = []
    for path in glob.glob('/app/api/**/*.py', recursive=True):
        src = open(path).read()
        for m in re.finditer(r'record_reply\s*\(', src):
            line = src[:m.start()].count('\n') + 1
            if 'def record_reply' in src.splitlines()[line - 1]:
                continue
            callers.append(f'{path}:{line}')
    assert not callers, (
        f'something now calls record_reply(): {callers}. Reply detection is '
        f'live, so "replied" is no longer inert - update the UI copy and this '
        f'test.')


@pytest.mark.parametrize('state', ['draft_ready', 'sent', 'replied', 'none'])
def test_each_email_state_is_filterable(client, db, state):
    r = client.get(f'/?email_state={state}')
    assert r.status_code == 200


def test_the_filter_and_the_column_cannot_disagree(client, db):
    """Both read _EMAIL_STATE. A lead with a draft must appear under
    draft_ready and NOT under sent."""
    lid = _lead_with_draft(db, 'filter@firm.example')
    assert str(lid) in client.get('/?email_state=draft_ready').text
    assert str(lid) not in client.get('/?email_state=sent').text


# ---------------------------------------------------------------------------
# there must be a way OUT of an unconfirmed email
# ---------------------------------------------------------------------------

def _unconfirmed_lead(db, email='wrong@firm.example'):
    from api import campaigns as cc, db as dbm
    cid = cc.create('UC-' + email[:6])['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, stage, status, last_called_at)
                           VALUES (%s,'W','Sean',%s,false,
                                   'America/Los_Angeles',%s,'L1','human_review',
                                   now())
                           RETURNING lead_id""",
                        ('+1424555' + str(abs(hash(email)) % 9000 + 1000), email, cid))
            return cur.fetchone()['lead_id']


def test_an_unconfirmed_email_has_no_draft(db):
    """The precondition. An unconfirmed address is how a sending domain ends
    up in a spam trap."""
    from api import drafts as d
    lid = _unconfirmed_lead(db)
    assert d.generate_for(lid) is None
    assert d.get(lid) is None


def test_confirming_by_hand_generates_the_draft(db, client):
    """
    THE GAP THIS CLOSES. There was NO path from human_review to a draft:
    generate_for() refuses an unconfirmed email, and its only caller was the
    "regenerate" button, which the page hides when no draft exists. A lead the
    agent failed to get a confirmation for was simply stuck.
    """
    from api import drafts as d
    lid = _unconfirmed_lead(db, 'typo@firm.example')
    r = client.post(f'/leads/{lid}/edit',
                    data={'dm_name': 'Sean', 'dm_title': '',
                          'dm_email': 'correct@firm.example',
                          'dm_email_confirmed': 'true', 'notes': ''},
                    follow_redirects=False)
    assert r.status_code == 303
    draft = d.get(lid)
    assert draft is not None, 'confirming by hand must produce a draft'
    assert draft['to_email'] == 'correct@firm.example'


def test_confirming_without_changing_the_address_still_generates(db, client):
    """The address can be right and merely unconfirmed - the agent read it back
    and moved on without asking. Ticking the box alone must be enough."""
    from api import drafts as d
    lid = _unconfirmed_lead(db, 'right@firm.example')
    client.post(f'/leads/{lid}/edit',
                data={'dm_name': 'Sean', 'dm_title': '',
                      'dm_email': 'right@firm.example',
                      'dm_email_confirmed': 'true', 'notes': ''},
                follow_redirects=False)
    assert d.get(lid) is not None


def test_confirming_does_not_clobber_an_edited_draft(db, client):
    """generate_for is only called when there is NO draft. A human's edits must
    survive a later confirm."""
    from api import drafts as d
    lid = _unconfirmed_lead(db, 'edited@firm.example')
    client.post(f'/leads/{lid}/edit',
                data={'dm_name': 'Sean', 'dm_title': '',
                      'dm_email': 'edited@firm.example',
                      'dm_email_confirmed': 'true', 'notes': ''},
                follow_redirects=False)
    d.save_edit(lid, 'My subject', 'My body')
    client.post(f'/leads/{lid}/edit',
                data={'dm_name': 'Sean', 'dm_title': '',
                      'dm_email': 'edited@firm.example',
                      'dm_email_confirmed': 'true', 'notes': ''},
                follow_redirects=False)
    assert d.get(lid)['body'] == 'My body'


def test_the_page_offers_a_way_forward_from_every_no_draft_state(db, client):
    """
    A dead end with no button is what made this look unfixable. Each state must
    say what to do next.
    """
    from api import db as dbm
    # unconfirmed: tells you to correct and tick
    lid = _unconfirmed_lead(db, 'state1@firm.example')
    body = client.get(f'/leads/{lid}').text
    assert 'not confirmed' in body and 'tick' in body

    # confirmed but no draft: the sweep is coming, and the page SAYS so
    # rather than inviting a click. Drafts are written by the worker, not
    # inline in the webhook, so there is a gap of up to one tick - a page that
    # reads "one can be written now" makes that gap look like a failure.
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads SET dm_email_confirmed = true
                            WHERE lead_id = %s""", (lid,))
    body = client.get(f'/leads/{lid}').text
    assert 'on its way' in body, 'the page must say a draft is coming'
    assert 'Generate draft now' in body, 'and still offer recovery'


def test_the_worker_sweep_generates_drafts_for_confirmed_captures(db):
    """
    THE NORMAL PATH, and it is not the button.

    A confirmed capture from a call advances the stage in the drain and leaves
    the draft to drafts.generate_pending(), which the worker runs each tick.
    The button is a RECOVERY path for a hand-confirmed email, not how a clean
    capture gets its draft.
    """
    from api import campaigns as cc, db as dbm, drafts as d
    cid = cc.create('SWEEP')['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, stage)
                           VALUES ('+14245554321','W','Sara',
                                   'sara@firm.example',true,
                                   'America/Los_Angeles',%s,'L2')
                           RETURNING lead_id""", (cid,))
            lid = cur.fetchone()['lead_id']
    assert d.get(lid) is None
    out = d.generate_pending()
    assert out['drafted'] >= 1, out
    assert d.get(lid) is not None, 'the sweep must write it, with no button'


def test_the_sweep_skips_unconfirmed_emails(db):
    """The sweep is not a way round the confirmation guard."""
    from api import campaigns as cc, db as dbm, drafts as d
    cid = cc.create('SWEEP-UC')['campaign_id']
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (phone_e164, company, dm_name,
                               dm_email, dm_email_confirmed, timezone,
                               campaign_id, stage)
                           VALUES ('+14245554322','W','Sara',
                                   'sara@firm.example',false,
                                   'America/Los_Angeles',%s,'L2')
                           RETURNING lead_id""", (cid,))
            lid = cur.fetchone()['lead_id']
    d.generate_pending()
    assert d.get(lid) is None


# ---------------------------------------------------------------------------
# ONLY THE SAMPLE LINK IS TRACKED
# ---------------------------------------------------------------------------

def test_a_signature_link_is_left_alone(db):
    """
    THE BUG THIS FIXES. The old rewrite swept EVERY counselorai.io URL in the
    body. With one link that was invisible; the moment a signature carries
    https://counselorai.io - a normal thing to want, so people can find the
    site - that becomes a tracked link too, and a click on "find us here"
    reads as interest in the sample.
    """
    from api import drafts as d
    lid = _lead_with_draft(db, 'sig@firm.example')
    from api import campaigns as cc, db as dbm
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT campaign_id FROM leads WHERE lead_id=%s', (lid,))
            cid = cur.fetchone()['campaign_id']
    # BOTH variants - this lead has a gatekeeper name, so it renders the
    # with_name one, and setting only body_without tested nothing.
    copy = ('Sample: {{sample_link}}\n\n'
        'Sean\n'
        'CounselorAI · https://counselorai.io\n'
        'Careers: https://counselorai.io/jobs\n')
    cc.update(cid, body_without=copy, body_with_name=copy)
    d.generate_for(lid, force=True)
    body = d.get(lid)['body']

    assert '/c/' in body, 'the sample link must still be tracked'
    assert 'CounselorAI · https://counselorai.io\n' in body, \
        'the signature link must be untouched'
    assert 'https://counselorai.io/jobs' in body, \
        'every other link must be untouched'
    assert body.count('/c/') == 1, 'exactly ONE tracked link'


def test_the_placeholder_survives_reordering(db):
    """Explicit beats positional: the sample can move anywhere in the copy."""
    from api import campaigns as cc, db as dbm, drafts as d
    lid = _lead_with_draft(db, 'reorder@firm.example')
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT campaign_id FROM leads WHERE lead_id=%s', (lid,))
            cid = cur.fetchone()['campaign_id']
    copy = ('See https://counselorai.io first.\n'
            'Then the sample: {{sample_link}}\n')
    cc.update(cid, body_without=copy, body_with_name=copy)
    d.generate_for(lid, force=True)
    body = d.get(lid)['body']
    assert body.startswith('See https://counselorai.io first.')
    assert body.count('/c/') == 1


def test_no_tracking_configured_still_gives_a_working_link(db, monkeypatch):
    """A dead link to a lawyer is worse than an untracked one."""
    from api import clicks, drafts as d
    monkeypatch.setenv('CLICK_BASE_URL', '')
    monkeypatch.setenv('PUBLIC_BASE_URL', '')
    lid = _lead_with_draft(db, 'nobase@firm.example')
    d.generate_for(lid, force=True)
    body = d.get(lid)['body']
    assert clicks.DESTINATION in body
    assert '/c/' not in body
