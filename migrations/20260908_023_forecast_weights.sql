-- STAGE PROBABILITIES for the pipeline forecast. Sean's starting guesses, and
-- they are guesses - configurable per campaign because two campaigns aimed at
-- different segments will not convert alike.
ALTER TABLE campaign_configs
  ADD COLUMN p_demo_booked numeric(4,3) NOT NULL DEFAULT 0.400
      CHECK (p_demo_booked >= 0 AND p_demo_booked <= 1),
  ADD COLUMN p_engaged     numeric(4,3) NOT NULL DEFAULT 0.150
      CHECK (p_engaged >= 0 AND p_engaged <= 1),
  ADD COLUMN p_emailed     numeric(4,3) NOT NULL DEFAULT 0.030
      CHECK (p_emailed >= 0 AND p_emailed <= 1);

COMMENT ON COLUMN campaign_configs.p_engaged IS
  'Clicked or replied. Everything not demo_booked/engaged/emailed weighs ZERO '
  '- a lead we have not emailed has no forecastable value.';
