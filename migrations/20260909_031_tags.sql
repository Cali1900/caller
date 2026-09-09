-- TAGS: free text, many per lead, applied by hand.
--
-- NOT a fixed vocabulary. `segment` covers the one case it was built for
-- and every other case needs a word Sean has not thought of yet - "referred
-- by X", "call after tax season", "big firm". A dropdown of options someone
-- has to extend in code is the thing this is instead of.
--
-- Stored lowercased and trimmed with the pair as the key, so applying the
-- same tag twice is a no-op rather than two rows that filter as one.
CREATE TABLE lead_tags (
  lead_id    uuid NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
  tag        text NOT NULL CHECK (tag = btrim(lower(tag)) AND length(tag) BETWEEN 1 AND 40),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by text,
  PRIMARY KEY (lead_id, tag)
);

-- The filter reads tag -> leads, so the index the query needs is on tag.
CREATE INDEX lead_tags_tag_idx ON lead_tags (tag);

COMMENT ON TABLE lead_tags IS
  'Free-text tags, many per lead, by hand. CASCADE with the lead: a tag is a note about a row, not a record of contact, so unlike dial_audit it has no reason to outlive it.';
