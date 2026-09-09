# A LEAD THAT HAS STOPPED AND CANNOT SAY SO.
#
# drip.drip_for() routes to the campaign's default_drip_id, falling back to
# only_drip(). When neither answers - no drip running, several with no default,
# or a default pointing at a stopped one - email 1 goes out and NOTHING FOLLOWS
# UP. There is no error and no failed send; the lead sits at 'emailed' forever.
#
# It used to be visible only by opening that lead, i.e. one firm at a time. This
# is the net, and it is the same family as the bounce that did not show and the
# archive return that produced unreachable leads: the system worked, and nothing
# said what had happened.
TARGET = 'api/web.py'
EXPECT = 'test_emailed_and_on_no_drip_shows_in_the_needs_you_queue'
LABEL = 'hide a stalled sequence from the needs-you queue'
OLD = """    (l.emailed_at IS NOT NULL
     AND l.drip_campaign_id IS NULL
     AND l.replied_at IS NULL"""
NEW = """    (false
     AND l.drip_campaign_id IS NULL
     AND l.replied_at IS NULL"""
