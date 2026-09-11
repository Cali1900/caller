# ⚠️ A REACTIVE NET CANNOT WARN ABOUT A CONFIGURATION.
#
# _STALLED_AFTER_EMAIL finds leads with emailed_at set and no drip. It is a good
# net and it fires TOO LATE by construction: by the time it matches, email 1 has
# gone and nothing is following it. The failure it was believed to cover was a call
# campaign CONFIGURED to send people nowhere - observable before any lead exists,
# and completely silent for as long as it took to ask three times.
#
# wiring_problems() is the proactive half: a call campaign whose follow-up drip is
# NULL, or points at a stopped drip. Both matter whether or not the campaign is
# running, because email 1 can be sent BY HAND from any lead page.
# ⚠️ RETARGETED TWICE. It reported per CALL CAMPAIGN when only wiring existed, per
# STATUS when only the gate existed, and per campaign again now that BOTH conditions
# are real - because the fix is made on a campaign. Four causes share one
# consequence: wired to nothing, wired to a stopped drip, wired to a drip whose gate
# refuses `emailed`, or no campaign at all (reported by orphan_statuses instead).
# This anchors the first, which is the one that started it: a silent null.
TARGET = 'api/campaigns.py'
EXPECT = 'test_today_warns_about_the_CONFIG_before_any_lead_is_affected'
LABEL = 'stop warning about a campaign wired to nothing'
OLD = """                if not r['drip_id']:
                    r['why'] = 'wired to no drip'"""
NEW = """                if False:
                    r['why'] = 'wired to no drip'"""
