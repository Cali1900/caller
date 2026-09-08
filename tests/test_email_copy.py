"""
THE EMAIL COPY IS A PROPERTY OF THE CAMPAIGN.

Two campaigns running different copy is most of the reason to have a second
campaign, so the thing worth testing is not that a template renders - it is
that two campaigns CANNOT end up sharing one.
"""

import pytest

from api import campaigns as c, drafts


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
