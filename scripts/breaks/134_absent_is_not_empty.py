# ⚠️ A POST MUST NOT BLANK A FIELD IT NEVER MENTIONED.
#
# lead_edit wrote dm_name, dm_title, dm_email, dm_email_confirmed,
# demands_per_month and notes unconditionally through NULLIF(%s,''), because they
# were declared Form('') - so an absent field arrived as an empty string,
# indistinguishable from a box the operator cleared on purpose.
#
# Any partial post therefore ERASED what it did not mention. dm_email included,
# which ends a drip silently: the address is the only thing a step can be sent to,
# and nothing would say where it went.
#
# The PHONE directly above was already protected against exactly this, with a
# comment explaining why an empty box must mean "unchanged" - and the reasoning
# was never applied to the six fields beside it.
TARGET = 'api/web.py'
EXPECT = 'test_a_partial_lead_edit_does_not_blank_the_contact_facts'
LABEL = 'let a partial edit blank the fields it never mentioned'
OLD = """                if raw is not None:
                    sets.append(f"{col} = NULLIF(%s,'')")
                    vals.append(raw.strip())"""
NEW = """                sets.append(f"{col} = NULLIF(%s,'')")
                vals.append((raw or '').strip())"""
