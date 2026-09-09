"""
The STANDING QUEUE.

One ongoing queue, no per-day ritual. The property that matters most:
removing the start button removed what stopped "add 500 leads" becoming
"dial 500 now", so it is replaced by ONE explicit switch that DEFAULTS TO OFF.
"""

import pytest

from api import campaigns, dialer, upload

LA = 'America/Los_Angeles'


@pytest.fixture(autouse=True)
def _no_window(monkeypatch):
    monkeypatch.setattr('api.windows.LEGAL_WINDOW', '')
    monkeypatch.setattr('api.windows.PREFERENCE_WINDOW', '')


@pytest.fixture
def no_real_calls(monkeypatch):
    placed = []

    class R:
        def __init__(self, n): self.call_id = f'call_fake_{n}'

    def fake(cfg, to_number, lead, dynamic=None):
        placed.append(to_number)
        return R(len(placed))
    monkeypatch.setattr('api.retell.create_phone_call', fake)
    monkeypatch.setenv('DIAL_MODE', 'unrestricted')
    return placed


def _pool_leads(db, n, prefix='+1555100', **kw):
    ids = []
    cols = {'pool_status': 'pool', 'status': 'new'}
    cols.update(kw)
    with db.cursor() as cur:
        for i in range(n):
            cur.execute(
                f"""INSERT INTO leads (company, phone_e164, timezone,
                                       pool_status, status
                                       {''.join(',' + k for k in kw if k not in ('pool_status','status'))})
                    VALUES (%s,%s,%s,%s,%s
                            {''.join(',%s' for k in kw if k not in ('pool_status','status'))})
                    RETURNING lead_id""",
                [f'Firm {i}', f'{prefix}{i:04d}', LA, cols['pool_status'], cols['status']]
                + [kw[k] for k in kw if k not in ('pool_status', 'status')])
            ids.append(cur.fetchone()['lead_id'])
    db.commit()
    return ids


def _cfg():
    from api.config import load_config
    return load_config()


# --------------------------------------------------------------------------
# THE SWITCH - defaults off, and adding leads can never turn it on
# --------------------------------------------------------------------------

def test_dialing_defaults_to_off(db):
    """
    Reads the REAL switch. This asserted on settings['dialing_enabled'], which
    nothing had consulted since campaigns became named configurations - so the
    property everything else rests on was being checked against a dead key.

    A newly created campaign is created STOPPED, and nothing runs until one is
    started deliberately.
    """
    assert campaigns.running() is None
    row = campaigns.create('DEFAULTS-OFF')
    assert row['is_running'] is False
    assert campaigns.running() is None


def test_queueing_five_hundred_leads_places_zero_calls(db, no_real_calls):
    """
    THE POINT OF THE SWITCH. The old guard was the ABSENCE of a start button;
    a guard by omission disappears with the ritual it depended on.
    """
    rows = ['company,phone,timezone'] + [f'Firm {i},+1555200{i:04d},{LA}'
                                         for i in range(500)]
    assert upload.upload('\n'.join(rows))['inserted'] == 500
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='active'")   # add ALL to the queue
    db.commit()

    assert dialer.run_once(_cfg(), limit=50) == 0
    assert no_real_calls == []


def test_turning_the_switch_on_starts_dialing(db, queued, no_real_calls):
    ids = _pool_leads(db, 3, prefix='+1555210')
    queued(ids)                       # queues AND switches dialing on
    # no explicit limit: run_once must honour max_concurrent, which is 1
    assert dialer.run_once(_cfg()) == 1
    assert len(no_real_calls) == 1


def test_pausing_stops_selection_immediately(db, queued, no_real_calls):
    """
    ISOLATES the selection-level switch.

    An earlier version claimed the leads first, which left them
    status='dialing' - so the second selection returned nothing whether the
    switch was checked or not, and removing the guard changed nothing. The
    candidates here are UNCLAIMED, so only the switch can exclude them.
    """
    ids = _pool_leads(db, 3, prefix='+1555220')
    queued(ids)
    campaigns.stop(queued.campaign_id)
    assert dialer.select_and_claim(_cfg(), limit=10) == []

    campaigns.start(queued.campaign_id)
    assert len(dialer.select_and_claim(_cfg(), limit=10)) > 0


def test_pausing_blocks_a_lead_that_was_already_claimed(db, queued, no_real_calls):
    """
    ISOLATES the pre-dial switch check.

    Selection already refuses when paused, and that masks this path. Here the
    lead is claimed while dialing is ON and paused afterwards, so only the
    guard inside dial_one can stop it.
    """
    ids = _pool_leads(db, 1, prefix='+1555225')
    queued(ids)
    claimed = dialer.select_and_claim(_cfg(), limit=5)
    assert len(claimed) == 1

    campaigns.stop(queued.campaign_id)
    assert dialer.dial_one(_cfg(), claimed[0]) is None
    assert no_real_calls == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM dial_audit "
                    "WHERE outcome='refused_paused'")
        assert cur.fetchone()['n'] == 1


def test_a_lead_not_in_the_queue_is_never_a_candidate(db, queued):
    """
    ISOLATES queue membership.

    The leads must be ASSIGNED to the running campaign, or CAMPAIGN_MEMBERSHIP
    excludes them first and removing the queue filter changes nothing - which
    is exactly how this passed while testing nothing. Assigned but left in the
    pool, so `pool_status = 'active'` is the only filter that can exclude them.
    """
    ids = _pool_leads(db, 2, prefix='+1555230')
    queued([])                        # start the campaign, queue nothing
    campaigns.assign([str(i) for i in ids], queued.campaign_id)
    with db.cursor() as cur:
        cur.execute("UPDATE leads SET pool_status='pool' "
                    "WHERE lead_id = ANY(%s::uuid[])", ([str(i) for i in ids],))
    db.commit()

    with db.cursor() as cur:      # the premise: they would pass every other filter
        cur.execute("""SELECT count(*) AS n FROM leads
                        WHERE campaign_id=%s AND pool_status='pool'
                          AND stage IN ('L1','L2') AND replied_at IS NULL""",
                    (queued.campaign_id,))
        assert cur.fetchone()['n'] == 2

    assert dialer.select_and_claim(_cfg(), limit=10) == []


# --------------------------------------------------------------------------
# the daily cap counts NEW leads only
# --------------------------------------------------------------------------

def test_the_cap_counts_new_leads(db, queued, no_real_calls):
    campaigns.update(queued.campaign_id, daily_cap=2, max_concurrent=1)
    queued(_pool_leads(db, 5, prefix='+1555240'))
    for _ in range(5):
        dialer.run_once(_cfg())
    assert len(no_real_calls) == 2, 'cap 2 means two NEW leads today'


def test_a_carryover_is_exempt_from_the_cap(db, queued, no_real_calls):
    """
    ISOLATES the carry-over exemption.

    The cap counts leads first dialed TODAY. An earlier version gave the
    carry-overs yesterday's date, so they never counted toward today either
    way and removing the exemption changed nothing. Here the carry-over was
    first dialed TODAY and today's cap is already spent, so only the
    exemption can let it through.
    """
    import datetime
    campaigns.update(queued.campaign_id, daily_cap=1, max_concurrent=1)
    spent = _pool_leads(db, 1, prefix='+1555251')
    carry = _pool_leads(db, 1, prefix='+1555252')
    now = datetime.datetime.now(datetime.UTC)
    with db.cursor() as cur:
        # already dialed today AND finished: it consumes the cap but must
        # not itself be a candidate, or it competes with the carry-over
        cur.execute("UPDATE leads SET first_dialed_at=%s, status='completed' "
                    "WHERE lead_id=%s", (now, spent[0]))
        cur.execute("UPDATE leads SET status='callback', first_dialed_at=%s, "
                    "next_attempt_at=now() WHERE lead_id=%s", (now, carry[0]))
        cur.execute("SELECT phone_e164 FROM leads WHERE lead_id=%s", (carry[0],))
        carry_phone = cur.fetchone()['phone_e164']
    db.commit()
    queued(spent + carry)

    dialer.run_once(_cfg())
    assert no_real_calls == [carry_phone]


def test_carryovers_are_dialed_before_new_leads(db, queued, no_real_calls):
    import datetime
    campaigns.update(queued.campaign_id, daily_cap=100, max_concurrent=1)
    new_ids = _pool_leads(db, 2, prefix='+1555260')
    old_ids = _pool_leads(db, 2, prefix='+1555270')
    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET status='callback', first_dialed_at=%s
                        WHERE lead_id = ANY(%s::uuid[])""",
                    (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
                     [str(i) for i in old_ids]))
        cur.execute("SELECT phone_e164 FROM leads WHERE lead_id = ANY(%s::uuid[])",
                    ([str(i) for i in old_ids],))
        carry_phones = {r['phone_e164'] for r in cur.fetchall()}
    db.commit()
    queued(new_ids + old_ids)
    dialer.run_once(_cfg()); dialer.run_once(_cfg())
    assert set(no_real_calls) == carry_phones, 'carry-overs go first'


def test_first_dialed_at_is_stamped_once(db, queued, no_real_calls):
    """A retry must never consume new-lead budget twice."""
    ids = _pool_leads(db, 1, prefix='+1555280')
    queued(ids)
    dialer.run_once(_cfg())
    with db.cursor() as cur:
        cur.execute('SELECT first_dialed_at FROM leads WHERE lead_id=%s', (ids[0],))
        first = cur.fetchone()['first_dialed_at']
        cur.execute("UPDATE leads SET status='no_answer', next_attempt_at=now() "
                    "WHERE lead_id=%s", (ids[0],))
    db.commit()
    dialer.run_once(_cfg())
    with db.cursor() as cur:
        cur.execute('SELECT first_dialed_at FROM leads WHERE lead_id=%s', (ids[0],))
        assert cur.fetchone()['first_dialed_at'] == first


# --------------------------------------------------------------------------
# uploading still never dials
# --------------------------------------------------------------------------

def test_upload_lands_in_the_pool_not_the_queue(db):
    upload.upload(f'company,phone,timezone\nA Firm,+14245559999,{LA}')
    with db.cursor() as cur:
        cur.execute('SELECT pool_status FROM leads')
        assert cur.fetchone()['pool_status'] == 'pool'


def test_nothing_is_lost_when_the_day_ends(db, queued, no_real_calls):
    """Anything not reached stays queued - there is no rollover step to miss."""
    campaigns.update(queued.campaign_id, daily_cap=1, max_concurrent=1)
    ids = _pool_leads(db, 4, prefix='+1555290')
    queued(ids)
    for _ in range(4):
        dialer.run_once(_cfg())
    with db.cursor() as cur:
        cur.execute("""SELECT count(*) AS n FROM leads
                        WHERE pool_status='active' AND first_dialed_at IS NULL""")
        assert cur.fetchone()['n'] == 3, 'the undialed leads stay in the queue'


def test_a_switch_mid_flight_does_not_dial_the_lead_under_the_new_campaign(db, queued,
                                                                          no_real_calls):
    """
    ISOLATES the re-read.

    dial_one used the campaign row captured on the lead at SELECTION time, so
    a pause during an in-flight batch was invisible to the guard that exists
    for exactly that case. Worse: stopping A and starting B let A's claimed
    lead dial under B's cap, spacing and prompt version.

    Claimed under A, switched to B before dialing. B is running, so a guard
    that only asks "is anything running" passes.
    """
    ids = _pool_leads(db, 1, prefix='+1555226')
    queued(ids)
    claimed = dialer.select_and_claim(_cfg(), limit=5)
    assert len(claimed) == 1

    other = campaigns.create('OTHER')
    campaigns.start(other['campaign_id'], stop_running=True)
    assert campaigns.running()['campaign_id'] == other['campaign_id']

    assert dialer.dial_one(_cfg(), claimed[0]) is None
    assert no_real_calls == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM dial_audit "
                    "WHERE outcome='refused_paused'")
        assert cur.fetchone()['n'] == 1


def test_fresh_leads_are_not_selected_in_upload_order(db, queued, no_real_calls):
    """
    An uploaded list is usually sorted - alphabetically by firm, which clusters
    by region and by firm type. Taking it in insert order means the first
    hundred calls are not a SAMPLE of the list, and the script gets tuned
    against a biased slice without anyone knowing.

    Stable random: the same lead sorts to the same place every tick, so nothing
    is starved and an interrupted selection resumes where it was.
    """
    ids = _pool_leads(db, 40, prefix='+1555310')
    queued(ids)
    got = [str(r['lead_id']) for r in dialer.select_and_claim(_cfg(), limit=40)]
    inserted = [str(i) for i in ids]
    assert len(got) == 40
    assert set(got) == set(inserted), 'every lead is still selected'

    # Not merely "different from insert order" - UNCORRELATED with it. A weak
    # assertion here passes on any incidental reordering, which is how the
    # first version of this test let a broken guard through.
    pos = {lid: i for i, lid in enumerate(inserted)}
    seen = [pos[l] for l in got]
    inversions = sum(1 for i in range(len(seen)) for j in range(i + 1, len(seen))
                     if seen[i] > seen[j])
    total_pairs = len(seen) * (len(seen) - 1) // 2
    assert 0.3 < inversions / total_pairs < 0.7, (
        f'selection order correlates with upload order: '
        f'{inversions}/{total_pairs} inversions (a shuffle sits near 0.5, '
        f'upload order at 0.0, exact reverse at 1.0)')


def test_the_random_order_is_stable_across_ticks(db, queued):
    """Not reshuffled every tick - a lead cannot be starved, and a selection
    interrupted halfway resumes where it was."""
    from api import db as dbm
    ids = _pool_leads(db, 25, prefix='+1555311')
    queued(ids)
    with dbm.get_conn() as conn:
        with conn.cursor() as cur:
            order = []
            for _ in range(2):
                cur.execute("""SELECT lead_id FROM leads
                                WHERE phone_e164 LIKE '+1555311%'
                                ORDER BY md5(lead_id::text)""")
                order.append([str(r['lead_id']) for r in cur.fetchall()])
    assert order[0] == order[1], 'the same order every time'


def test_carryovers_still_come_before_fresh_leads(db, queued, no_real_calls):
    """The randomisation is ONLY about which fresh lead is next. A promise
    already made still goes first."""
    import datetime
    fresh = _pool_leads(db, 10, prefix='+1555312')
    carry = _pool_leads(db, 1, prefix='+1555313')
    now = datetime.datetime.now(datetime.UTC)
    with db.cursor() as cur:
        cur.execute("""UPDATE leads SET status='callback', first_dialed_at=%s,
                              next_attempt_at=now() - interval '1 hour'
                        WHERE lead_id=%s""", (now, carry[0]))
    db.commit()
    queued(fresh + carry)
    first = dialer.select_and_claim(_cfg(), limit=1)
    assert str(first[0]['lead_id']) == str(carry[0]), 'carry-over first'


# --------------------------------------------------------------------------
# campaign type: call is exclusive, drip is not
# --------------------------------------------------------------------------

def test_a_campaign_is_a_call_campaign_unless_told_otherwise(db):
    """Every campaign that existed before type did is a call campaign, and
    the default must keep it that way without anyone restating it."""
    c = campaigns.create('T-default')
    assert c['type'] == 'call'


def test_two_call_campaigns_cannot_run_at_once(db):
    a = campaigns.create('T-call-a')
    b = campaigns.create('T-call-b')
    campaigns.start(a['campaign_id'])
    with pytest.raises(campaigns.CampaignConflict) as e:
        campaigns.start(b['campaign_id'])
    assert e.value.running['campaign_id'] == a['campaign_id'], \
        'the refusal must carry what is running, so the caller can ask'


def test_many_drips_run_at_once(db):
    """
    one_running_campaign was UNIQUE (is_running) WHERE is_running. The second
    drip would have been refused by Postgres, with an error naming an index
    instead of a reason. A lead's sequence belongs to the lead, not to
    whichever call campaign sourced it, so many drips is the normal case.
    """
    a = campaigns.create('T-drip-a', type='drip')
    b = campaigns.create('T-drip-b', type='drip')
    campaigns.start(a['campaign_id'])
    campaigns.start(b['campaign_id'])      # must NOT raise
    assert {c['name'] for c in campaigns.running_drips()} == {'T-drip-a', 'T-drip-b'}


def test_a_running_drip_does_not_block_or_masquerade_as_the_call_campaign(db):
    """
    The premise every other filter would pass: the drip IS running, so an
    unscoped `WHERE is_running` returns it.

    running() feeds the dialer, the version picker and the sender, and all
    three mean "the campaign that is dialing". Handing them a drip gives the
    dialer a drip's cap and windows.
    """
    d = campaigns.create('T-drip-solo', type='drip')
    campaigns.start(d['campaign_id'])
    assert campaigns.running() is None, 'a drip is not the dialing campaign'

    c = campaigns.create('T-call-solo')
    campaigns.start(c['campaign_id'])       # the drip must not have blocked it
    assert campaigns.running()['campaign_id'] == c['campaign_id']
    assert [x['name'] for x in campaigns.running_drips()] == ['T-drip-solo'], \
        'starting a call campaign must not have stopped the drip'


def test_bare_stop_stops_dialing_and_leaves_the_drips_running(db):
    """stop() with no argument has always meant "stop dialing". Without the
    type scope it would silently stop every drip as well."""
    d = campaigns.create('T-drip-keep', type='drip')
    c = campaigns.create('T-call-go')
    campaigns.start(d['campaign_id'])
    campaigns.start(c['campaign_id'])
    campaigns.stop()
    assert campaigns.running() is None
    assert [x['name'] for x in campaigns.running_drips()] == ['T-drip-keep']


def test_type_cannot_be_edited_after_creation(db):
    """Moving a lead's whole ladder sideways is not an edit. update() refuses
    it the same way it refuses any field not in CONFIG_FIELDS."""
    c = campaigns.create('T-frozen')
    with pytest.raises(ValueError) as e:
        campaigns.update(c['campaign_id'], type='drip')
    assert 'type' in str(e.value)


def test_create_refuses_a_type_that_is_not_call_or_drip(db):
    with pytest.raises(ValueError):
        campaigns.create('T-bad', type='email')


def test_type_is_not_silently_dropped_by_the_override_filter(db):
    """
    THE FAULT THIS PREVENTS, twice seen: create(**overrides) filters to
    CONFIG_FIELDS, and type is deliberately not in it. Routed through there,
    every drip would be created as a call campaign, and the only symptom
    would be a drip that refuses to start alongside another one.
    """
    c = campaigns.create('T-drip-kw', type='drip')
    assert campaigns.get(c['campaign_id'])['type'] == 'drip', \
        'type must reach the INSERT, not be filtered out on the way'
