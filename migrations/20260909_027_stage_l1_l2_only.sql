-- THE STAGE CONSTRAINT STILL ALLOWED FOUR VALUES NOTHING CAN WRITE.
--
--   CHECK (stage = ANY (ARRAY['L1','L2','L3','L4','won','lost']))
--
-- Only api/stages.py writes stage, and it writes 'L2'. L3 calling was
-- descoped - a follow-up is its own campaign, by email - and L4 never
-- existed as anything but a placeholder next to it.
--
-- 'won' and 'lost' are the same fault as agent_l3_version, one shape up:
-- one fact with two homes. They are STATUSES, on leads.status, alongside
-- engaged / demo_booked / lost_no_response / bad_email. Leaving them here
-- as stages too means two columns can disagree about whether a lead is won,
-- and nothing decides which is right.
--
-- Narrowing this is not cosmetic. Three tests proved an L3 lead would not be
-- picked up by the L1 campaign; with L3 unrepresentable the database refuses
-- the state outright, which is stronger than a test that has to remember to
-- look for it.
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_stage_check;
ALTER TABLE leads ADD CONSTRAINT leads_stage_check CHECK (stage IN ('L1', 'L2'));
