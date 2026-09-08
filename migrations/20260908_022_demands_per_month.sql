-- HOW MANY DEMANDS THE FIRM SENDS IN A MONTH.
--
-- The agent has been asking since v15 and the answer has been landing nowhere:
-- every call so far said a number out loud and lost it.
--
-- TWO COLUMNS ON PURPOSE:
--   demands_per_month_raw  what she ACTUALLY said, always kept
--   demands_per_month      a number, ONLY where one is clearly stated
--
-- NULL MEANS SHE DID NOT ANSWER. It does not mean zero, and nothing may treat
-- it as zero - not a sort default, not an average, not a forecast. A firm that
-- declined to answer is not a firm that sends no demands.
ALTER TABLE leads
  ADD COLUMN demands_per_month     integer,
  ADD COLUMN demands_per_month_raw text;

ALTER TABLE leads ADD CONSTRAINT leads_demands_per_month_sane
  CHECK (demands_per_month IS NULL
         OR (demands_per_month >= 0 AND demands_per_month <= 10000));

COMMENT ON COLUMN leads.demands_per_month IS
  'Parsed monthly demand volume. NULL = not answered, NEVER zero-by-default. '
  'A RECEPTIONIST ESTIMATE - directional, not a contract.';
COMMENT ON COLUMN leads.demands_per_month_raw IS
  'Her words, verbatim: "about 20", "maybe 5 or 6", "no idea".';

-- What one demand is worth. A campaign setting, because two campaigns can
-- target segments that price differently.
ALTER TABLE campaign_configs
  ADD COLUMN price_per_demand numeric(10,2) NOT NULL DEFAULT 150.00
  CHECK (price_per_demand >= 0 AND price_per_demand <= 100000);

COMMENT ON COLUMN campaign_configs.price_per_demand IS
  'Revenue per demand package, for the pipeline forecast. Default $150.';
