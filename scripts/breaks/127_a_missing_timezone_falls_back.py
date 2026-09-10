# ⚠️ A LEAD WITH NO TIMEZONE MUST NOT VANISH FROM THE SCHEDULE.
#
# leads.timezone became NULLABLE with the email-only import (038), and
# `now() AT TIME ZONE NULL` is NULL - which fails every comparison silently. So
# without the coalesce an imported lead is never due, never sends, and never says
# why: the exact silent-vanishing failure the pacing work was asked to avoid,
# affecting precisely the leads a drip holds most of.
#
# The fallback is the operator's own hours, which is the best available proxy,
# and the campaign screen counts how many leads are on it rather than leaving it
# as a hidden behaviour.
TARGET = 'api/drip.py'
EXPECT = 'test_a_lead_with_no_timezone_falls_back_and_never_vanishes'
LABEL = 'let a NULL timezone silently exclude the lead'
OLD = """            .replace('l.timezone', "coalesce(l.timezone, %(op_tz)s)"))"""
NEW = """            )"""
