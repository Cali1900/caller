# ⚠️ REPLACES A BREAK WHOSE PREMISE WAS REVERSED, and saying so is the point.
#
# 146 used to guard "an imported lead needs no wiring": it has no call campaign, so
# the gate was its only condition. Migration 048 reversed that, because the
# carve-out carried the same flaw the wiring exists to fix - a California list and a
# Hawaii list are both `imported`, so every drip accepting `imported` got both.
#
# What needs guarding now is the other half: A BATCH UPLOADED WITH NO DRIP NAMED
# RECEIVES NOTHING, and /today says so. NULL is a legitimate answer at upload; a
# legitimate answer that is invisible is how a list sits in the database receiving
# nothing for a month.
TARGET = 'api/campaigns.py'
EXPECT = 'test_an_unwired_batch_receives_nothing_and_says_so'
LABEL = 'stop reporting imported batches that are wired to nothing'
OLD = """                   AND l.import_drip_id IS NULL"""
NEW = """                   AND false"""
