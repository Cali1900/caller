-- ONLY THE SAMPLE LINK IS TRACKED, and it is tracked because the copy says so.
--
-- The rewrite swept EVERY counselorai.io URL in the body. With one link that
-- was invisible; the moment a signature carries https://counselorai.io - which
-- is a normal thing to want, so people can find the site - that link becomes a
-- tracked one too, and a click on "find us here" reads as interest in the
-- sample.
--
-- Explicit beats positional: the copy can be reordered and can carry any
-- number of other links, and only {{sample_link}} is rewritten.

UPDATE campaign_configs
   SET body_with_name = replace(body_with_name,
                                'https://counselorai.io/#letter',
                                '{{sample_link}}'),
       body_without   = replace(body_without,
                                'https://counselorai.io/#letter',
                                '{{sample_link}}');

-- Anything already rewritten to a tracked URL by the old sweep goes back to
-- the placeholder too, so editing the copy does not preserve one lead's token
-- in the template every other lead renders from.
UPDATE campaign_configs
   SET body_with_name = regexp_replace(body_with_name,
           'https?://[^\s]*/c/[A-Za-z0-9_-]+', '{{sample_link}}', 'g'),
       body_without   = regexp_replace(body_without,
           'https?://[^\s]*/c/[A-Za-z0-9_-]+', '{{sample_link}}', 'g');
