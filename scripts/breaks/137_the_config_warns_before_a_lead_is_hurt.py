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
# ⚠️ RETARGETED 2026-09-10: reported per STATUS rather than per call campaign,
# because the status is what decides membership. The warning is the same one.
TARGET = 'api/campaigns.py'
EXPECT = 'test_today_warns_about_the_CONFIG_before_any_lead_is_affected'
LABEL = 'stop warning about a campaign wired to nothing'
OLD = """                 WHERE l.status = ANY(%s)"""
NEW = """                 WHERE false AND l.status = ANY(%s)"""
