# ⚠️ THE DRIP'S VIEW BEING LITERAL INSTEAD OF USEFUL.
#
# LAST SENT read "—" for a firm that HAD been emailed, because the query counted
# only THIS drip's steps and email 1 from a call campaign carries step_id NULL. An
# inner join dropped it before anything else happened.
#
# The firm was emailed. The screen said nothing had happened - and the operator's
# next move depends on knowing when that firm last heard from us, whoever sent it.
#
# Two questions need two CTEs: `own` counts this drip's steps for the sequence
# number, `any_send` counts every send for LAST SENT and CLICKS. Answering both
# from one join is what produced the lie.
TARGET = 'api/drip.py'
EXPECT = 'test_a_lead_emailed_only_by_the_call_campaign_shows_a_REAL_last_sent'
LABEL = 'count only this drip\'s steps, so email 1 reads as never emailed'
OLD = """                any_send AS (
                    SELECT es.lead_id, es.sent_at, es.send_id,
                           st.position, st.campaign_id AS step_campaign
                      FROM email_sends es
                      LEFT JOIN drip_steps st ON st.step_id = es.step_id
                     WHERE es.sent_at IS NOT NULL
                ),"""
NEW = """                any_send AS (
                    SELECT es.lead_id, es.sent_at, es.send_id,
                           st.position, st.campaign_id AS step_campaign
                      FROM email_sends es
                      JOIN drip_steps st ON st.step_id = es.step_id
                     WHERE es.sent_at IS NOT NULL
                       AND st.campaign_id = %(cid)s
                ),"""
