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
# ⚠️ RETARGETED 2026-09-10: the net asked "was it assigned a drip"; membership is
# derived now, so it asks "does any RUNNING drip accept its status". Same failure,
# asked of the mechanism that decides it.
TARGET = 'api/web.py'
EXPECT = 'test_emailed_and_on_no_drip_shows_in_the_needs_you_queue'
LABEL = 'hide a stalled sequence from the needs-you queue'
OLD = """     AND NOT EXISTS (SELECT 1 FROM campaign_configs dc
                      WHERE dc.type = 'drip' AND dc.is_running
                        AND l.status = ANY(dc.accepted_statuses))"""
NEW = """"""
