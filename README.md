# caller

Outbound calling system that dials personal-injury law firms, talks to the
front desk, and comes back with **who handles intake, their email, and when to
reach them.** It does not pitch and it does not book demos.

Voice is Retell AI. We own the dialer, the state machine, the webhook handler
and the data.

**Separate repo, separate compose project, eventually a separate droplet.** It
is not part of CounselorAI and does not share `lf-postgres` — see
BUILD_BRIEF.md for the three load-bearing reasons.

## Dial spacing and windows — operator-controlled, no deploy

⚠️ **THE `settings` TABLE IS DELETED** (migration 015), and so is
`api/settings.py`. Every one of these values now lives on the CAMPAIGN
(`campaign_configs`), with the week in `campaign_windows`, edited on
`/campaigns`. `tests/test_no_dead_config.py` asserts the module stays deleted.

**Env is only the first-boot seed** — changing an env var needs a container
recreate, which is a deploy step, and a recreate during calling hours is how
you drop a call.

| control | default | why |
|---|---|---|
| `max_concurrent` | **1** | THE spacing control. With a batch limit of 10 a single tick could place ten calls 0.2s apart and defeat any interval. |
| `dial_interval_min/max` | **210–300s** | re-rolled every tick. A fixed cadence is itself a pattern. |
| `daily_cap` | **100** | first month |
| weekday windows | Mon–Fri 09:00–17:00 | client-local; can only NARROW the legal 08:00–20:30 window |

The screen shows the consequence, not just the number: at 1 call per ~4 min
that is **14.1 calls/hour**, so 100 spreads over **~7.1 hours**. Out-of-range
input is **refused, not clamped** — a typo that halves your spacing should be
visible.

**The gap is re-armed BEFORE the dial**, so the next call is a fixed
wall-clock wait from this one regardless of how the last one ended. A busy
signal cannot pull the next dial forward, and `busy` backs that lead off
**15 minutes** (not the generic ladder) because a busy means a human is there.

### Timezone spread — decided, not built

Selection today orders by carry-over source then `next_attempt_at`, with **no
timezone spread**. The exposure is real but the cause is the *window filter*,
not the sort: at 5am Pacific, East Coast leads are the only rows that pass, so
they can eat the cap before California opens.

**Decision (2026-09-07): segment the list and load one region per day.** One
region can absorb the cap (8h window x 14.1/hr = 113 capacity vs 100), so
proportional selection would buy nothing at this volume, and per-region days
give *attributable* score comparisons in phase 7 — the same reasoning that
made us pin the agent version.

Two things to know about that choice: `enroll()` does **not** filter on
`leads.segment`, so this is zero-code only if you upload one region at a time;
and carry-overs auto-enrol regardless of region, so days are clean for
`source='fresh'` but not perfectly clean overall.

## The standing queue

One ongoing queue, not a per-day ritual. There is no enrol step and no daily
start.

```
upload CSV ──▶ POOL ──select on /leads──▶ QUEUE ──the switch──▶ dialing
                              (never dials)      (defaults OFF)
```

- **`/leads` has checkboxes.** Filter by anything, select, "Add to campaign".
- **Carry-overs go first.** `ORDER BY (first_dialed_at IS NULL)` puts
  callbacks, L3 follow-ups and retries ahead of new leads.
- **The cap counts NEW leads only**, by `leads.first_dialed_at`, stamped once.
  A retry never consumes new-lead budget, and a carry-over is exempt entirely.
- **Nothing is lost.** Whatever is not reached stays queued — there is no
  rollover step to forget.

### THE SWITCH — and why it exists

Removing the start button removed the thing that stopped *"add 500 leads"*
becoming *"dial 500 now"*. **That was a guard by omission, and a guard by
omission disappears the moment the ritual it depended on does.**

It is replaced by STARTING A CAMPAIGN, which is a deliberate act on
`/campaigns` and **defaults to stopped**. (It was briefly a `dialing_enabled`
settings key; that store is gone — see above.) Queueing a thousand leads with
nothing running places zero calls — a test asserts exactly that with 500. It is
checked in the selection query *and* before each dial, so pausing mid-batch
stops leads already claimed.

Exactly one CALL campaign runs at a time, enforced by the unique partial index
`one_running_campaign`, scoped to `type='call'` so drips are not caught by it.

`scripts/deploy.sh` is the only way to restart: it **pauses before restarting
and resumes after**, with a `trap` so it resumes even if the build fails.

## ⚠️ STANDING RULE — a guard is only tested if the test ISOLATES it

**A guard is only tested if the test constructs a row that every OTHER filter
would pass, so only the guard under test can exclude it.**

Repeatedly now, a guard has been removed and the suite stayed green because
something else already excluded the same row. A test that passes for the wrong
reason is worse than no test: it reports coverage that does not exist.

**THE TABLE BELOW IS THE COUNT — it is exactly the rows you can read, and
every row names the specific thing that masked the guard.** Do not restate the
number in prose. It was written as six, nine and eleven in three different
places, each stale the moment a row was added, and no record survived saying
what the tenth and eleventh had been. A count nobody can reconstruct is the
same failure as a GREEN with no number. Add a row, or the instance did not
happen.

| # | guard | what masked it |
|---|---|---|
| 1 | TCPA legal window | the operator preference window (09:00–17:00) is strictly **narrower**, so the legal window never bound. Only observable once the preference is widened. |
| 2 | campaign started/paused SQL gate | `assert_campaign_running()` already refused at dial time. Only observable at **selection**, where the gate stops the lead being *claimed*. |
| 3 | `REPLIED_GUARD` | the test used `record_reply()`, which also sets `status='completed'` — already excluded. Only observable with `replied_at` set while status stays **dialable**. |
| 4 | the switch (pre-dial) | the selection-level check already refused. Only observable on a lead **claimed while on, paused after**. |
| 5 | the switch (selection) | **the test itself** claimed the leads on a first call, leaving them `status='dialing'`, so the second selection returned nothing either way. Only observable on **unclaimed** leads. |
| 6 | carry-over cap exemption | the cap counts `first_dialed_at::date = today`, and the test's carry-overs carried **yesterday's** date, so they never counted. Only observable with a carry-over first dialed **today** against a spent cap. |
| 7 | the in-transaction campaign re-check | `dial_one` trusted the campaign row captured on the lead at **selection** time, so a pause mid-batch — the one case that re-check exists for — was invisible. Worse, stopping A and starting B let A's claimed lead dial under **B's** cap, spacing and prompt version. Only observable on a lead **claimed under A and dialed after the switch**. |
| 8 | `QUEUE_MEMBERSHIP` (`pool_status='active'`) | the named-campaign change added `CAMPAIGN_MEMBERSHIP`, and the test's leads were never assigned to a campaign — so they were excluded before queue membership was consulted. Only observable on a lead **assigned to the running campaign but left in the pool**. |
| 9 | carry-over cap exemption, again | the test set `settings['daily_cap'] = 1`, which nothing has read since the cap moved onto the campaign. The real cap stayed at the default 100, so it never bound and the exemption was never exercised. Only observable with the cap set **where the dialer reads it**. |
| 10 | the archive return's confirmed-email reset (then called `stage`) | every lead in `tests/test_archive.py` is built at `stage='L1'` — which is what makes the suppression test properly isolated. But the three EMAIL-derived archive reasons (`no_reply`, `bad_email`, `unsubscribed`) can only be reached from **L2**, so "a returned lead is dialable" was only ever asserted for leads that never reach the state that breaks it. `return_due()` never reset `stage`, nothing writes `'L1'` back, and `STAGE_DIALABLE` is L1-only — the lead came back unreachable down **both** wires. Only observable on a lead archived **from L2**. Fixed 2026-09-09, breaks 91–93. |
| 11 | `REPLIED_GUARD` across the archive return | the same fixture shape, found by asking the same question of the next gate: no test set `replied_at` on a lead that was archived and returned, so the permanent block it created was unobserved. A firm that said "not interested", archived `refused`, could never be dialed again. Only observable on a returned lead with `replied_at` set. Fixed 2026-09-09, break 95 — and a companion test proves clearing it does **not** let a suppressed number through, because suppression is keyed on the phone. |

| 12 | `drip.REPLIED_STOP`, via a test that never clicked | ⚠️ **A NEW SUB-SHAPE: the test's SETUP silently no-opped.** `test_a_click_does_NOT_stop_the_sequence` asserted that a click leaves the drip running — while never producing a click. Its fixture inserted `email_sends` rows with no `click_token`, so `clicks.record()` had nothing to look up and returned `None`, and the test's own `if row and row['click_token']:` made the skip invisible. Break 100 removed the guard, nothing failed, and the pass reported GREEN. Only observable by asserting the click was RECORDED before asserting the consequence. Found 2026-09-09. |
| 13 | **the break pass itself**, laundering a live break | ⚠️ **THE TOOL, NOT A TEST.** `preflight` ran BEFORE the concurrency lock. It treats an existing `.break_pass_state` as a crashed run — restores from that snapshot and `rm -rf`s it — so a second invocation, *even one the lock would have refused*, deleted the running pass's only copy of the originals. The running pass then couldn't restore, correctly stopped, and left `ALREADY_SENT_STOP = ''` live in `api/drip.py`. Its FINAL VERIFY reported *"no state directory — nothing was left applied"* (with no state dir it had nothing to compare against) and four later `--only` runs each snapshotted the **broken** file as their original and reported `RESTORE VERIFIED ✓` against it. `--check` said clean. A full suite passed. Caught only by `breaks_anchor_check.py`, when break 98's `OLD` stopped matching. Fixed 2026-09-09: lock before preflight, `--check` runs the anchor check, and `break_pass.sh` refuses to start with a live break. |
| 14 | the sequence save's **count guard**, masked by a blank row | ⚠️ **THE GUARD'S TWO NUMBERS MEASURED DIFFERENT THINGS.** It refuses when fewer steps reach the database than were posted — `kept` counted posted steps WITH COPY, `rows` counted EVERY posted row including untouched blank clones. So a blank row could stand in the place of a real step that had been dropped: the totals matched and the guard stayed silent over exactly the loss it was added for. It had no test that posted a blank row alongside real ones, because the blank row was assumed to be refused earlier — and the template promised the opposite ("leave blank to skip"). Only observable by posting a blank row WITH content steps. Fixed 2026-09-10, break 121. |
| 15 | the email gap's WIDE fallback, masked by the test reading it | ⚠️ **THE TEST READ THE VALUE IT WAS ASSERTING.** `test_the_gap_falls_back_WIDE_when_no_drip_is_running` bounded its own assertion with `lo, hi = worker.FALLBACK_EMAIL_GAP` and then checked every roll fell inside `[lo, hi]`. Break 130 set the constant to `(1, 2)` seconds — a fallback that bursts when configuration is unreadable, the exact inversion of "fail slow, never fast" — the test read `(1, 2)`, every roll was inside it, and the pass reported **GREEN**. Only observable by asserting the LITERAL floor (60s) that matters. Same shape as row 9 turned inward: there the test configured one place and production read another; here the test read production's own answer and compared it to itself. Fixed 2026-09-10. |
| 16 | the bounce-visible status write, masked by its own successor | ⚠️ **A GUARD THAT BECAME REDUNDANT RATHER THAN ABSENT.** `stop()` began setting the status generically from `STOP_STATUS`, in the transaction that records the stop — so the second `UPDATE leads SET status='bad_email'` in the bounce branch was dead code. Break 105 removed it, nothing failed, and the pass reported GREEN. The property was never at risk; the BREAK was pointing at a line that no longer did the work, which is a guard nobody would notice had stopped being proven. Only observable by removing the mechanism that now provides it. Fixed 2026-09-10: dead code deleted, break retargeted at `STOP_STATUS['bounced']`. |
| 17 | `PHONE_REQUIRED`, masked by a landing status the test's own docstring denied | ⚠️ **THE TEST STATED THE PREMISE THAT HAD BROKEN.** `test_a_phoneless_lead_is_never_a_candidate` said "status 'new' … so only the missing phone can exclude it" — while `upload_emails()` had started landing leads as `imported`, which is not a dialable status. The STATUS excluded the lead, `PHONE_REQUIRED` was never consulted, and break 106 reported GREEN. The docstring was the documentation of an isolation that no longer existed, which is worse than no docstring. Only observable by setting the status the test claims to have set. Fixed 2026-09-10. |
| 18 | the per-mailbox cap predicate, masked by pointing at the POSITIVE test | ⚠️ **A BREAK THAT WIDENS A FILTER CAN ONLY BE CAUGHT BY A TEST ASSERTING EXCLUSION.** Break 128 changes `WHERE es.from_email = c.sender_email` to `WHERE true`, so the caps count every mailbox as one. Its `EXPECT` named `test_the_caps_count_per_mailbox_not_per_campaign` — "a send from the same mailbox counts" — which still passes under the break, because over-counting counts MORE, not less. GREEN on the full pass. The failure is over-counting, and only `test_a_different_mailbox_does_not_spend_this_ones_budget` asserts the exclusion it destroys. Fixed 2026-09-10 by repointing `EXPECT`. Same shape as row 17's negative-case gap: **for a break that removes a filter, ask which test would fail if MORE rows matched.** |
| 19 | `next_open()`, masked by a test whose premise was THE CURRENT TIME | ⚠️ **A TEST THAT PASSES RANDOMLY IS WORSE THAN ONE THAT FAILS RANDOMLY.** `test_a_send_time_is_moved_into_the_FIRMS_business_hours` set a 9–17 Mon–Fri window and asserted the send time landed inside it — but left the lead's step OVERDUE, so with `next_open()` removed the answer was `max(next_due, now)` = **now**, and "now" is inside business hours whenever the suite runs during them. Break 138 verified **RED at 20:00 PDT and GREEN at 12:54 PDT**: same code, opposite result, decided by the clock. It reads as coverage all working day. This is the hazard `open_all_hours()` was introduced to remove, reappearing in the one test that must narrow the window — where the same reasoning was not applied. Only observable by pinning the due time OUT of hours and in the FUTURE, so `max(next_due, now)` cannot satisfy the assertion. Fixed 2026-09-10. |
| 20 | ⚠️ **`REPLIED_STOP` — the guard that matters most — masked by the status gate** | `record_reply()` sets `replied_at` **and** `status = 'engaged'`. The test's gate was `['emailed']`, so the STATUS excluded the lead and `REPLIED_STOP` was never consulted: break 99 removed the reply gate and nothing failed. **Not a live defect** — both gates exclude a replied lead, and `test_a_reply_still_outranks_a_click_and_stops_everything` does isolate it. But the definition whose whole job is to prove the reply gate pointed at a test that could not fail, and the status gate only covers it **while no drip accepts `engaged`** — which is a configuration the operator is explicitly allowed to choose. **A guard that is covered by accident is not covered.** This is the gate the HANDOFF marks as not fail-closed, where a person reading their inbox is the real guard. Only observable with a gate that ACCEPTS the post-reply status. Fixed 2026-09-10. |

Note #5: the *test* was wrong, not the code. That is the usual shape.

**A THIRD INSTANCE OF "AN ABSENT FIELD IS NOT A FIELD SET TO EMPTY"**, found
2026-09-09 while adding the per-step enable toggle. An unchecked HTML checkbox
posts NOTHING, so from a form absent means off — but a programmatic caller (a
test, a seed, a script) passes rows without the key and means "a normal enabled
step". Collapsing those two silently disabled every step created outside the
form. The lucky version is what happened: every drip test went red at once. The
unlucky version is a seeded sequence that quietly never sends.

The previous two: an absent retry ladder is not a ladder set to empty (which made
every older form post reject a whole save), and an absent campaign `type` is not
`'call'` (which would have created every drip as a call campaign). **When a form
field can be legitimately absent, the handler supplies it explicitly and the
validator defaults it to the safe-for-code value, not the safe-for-forms one.**

Note #13 is the one to internalise: **a concurrency guard checked after the
thing it guards is not a guard**, and **"verified" against a snapshot you took
of a broken state verifies nothing.** Every report in that chain was truthful
about what it measured and wrong about what it implied. The only check that
caught it reads `api/` directly and depends on no state at all — which is the
property to prefer in a safety check.

Also: a break reporting `BASELINE: ... is ALREADY RED` reads as "your test is
order-dependent". It can equally mean **a guard is genuinely missing from
`api/`**. Check `api/` before suspecting the test.

Note #12 is worth reading on its own: **defensive coding inside a test can
turn an assertion into a no-op.** `if row and row['click_token']:` looks careful
and was the whole bug — it swallowed the case where the setup had not produced
what the test needed. A test's premise must be ASSERTED, never guarded.

Note #10 and #11: the *fixture* was wrong — it built a state production never
produces at that point in a lead's life. That is the shape to watch for in any
test whose subject is a TRANSITION rather than a filter.

### A second failure shape: a BREAK that cannot fail

Two break definitions were self-neutralising — the guard was fine, and so was
the test, but the *break* restored no bug:

* **24** substituted `campaign_id = NULL` into the selection. That matches no
  rows on its own, so removing the early return still returned `[]`. Rewritten
  to the realistic bug — select for *some* saved campaign rather than *the
  running* one.
* **27** replaced only the line that computes `cid`; the very next line
  (`campaign = campaigns.get(cid) if cid else None`) overwrote the stale
  campaign it had just injected with `None`, which refuses. Rewritten to
  replace the whole re-read.

Both reported GREEN, i.e. "the guard is not covered" — the safe direction. But
a break that cannot fail is not a break, and it hides a guard that may or may
not be tested. **When a break reports GREEN, check the break before the test.**

### A near miss worth naming

Break **19** (`max_concurrent`) passed, but its test set the value through
`settings`, which production stopped reading. The assertion held only because
the campaign default happens to be the same number. The guard *was* covered;
the test simply did not control the value it appeared to control, and would
have broken confusingly the first time that default changed. Fixed by setting
it on the campaign and asserting on a number that is **not** the default.

### ⚠️ Running the break pass

**Never run it under a timeout that can kill it, and never stage or commit
while it is running.** It mutates real source files and restores them at the
end; a killed run leaves a break LIVE in `api/`. This has now happened twice —
once leaving `raise` inside the scorer's `except`, once deleting the allowlist
check in `guards.py`.

Two rules that follow:

1. **Audit anchors with an explicit loop**, never a compressed one-liner. A
   clever `lambda`/`exec` one-liner reported "all 29 anchors intact" while a
   break was live in `api/scorer.py`, and the next full suite run was read as a
   code regression for twenty minutes.
2. **Restore by inverting the exact edit**, not by `git checkout` and not by a
   naive `replace(NEW, OLD)`. `NEW` is often a bare `return`, which matches
   somewhere else in the file first — that is how `guards.py` ended up with an
   `if` whose body was dedented.

### ⚠️ STANDING RULE — never report a UI change without FETCHING THE SERVED PAGE

**A template is not a page.** Between the two sit a deploy, a baked image, Jinja
that can render valid-but-wrong markup, and a browser that silently drops things
HTML forbids. Every one of those has produced a "fixed" report that was false:

| what was claimed | what was actually true |
|---|---|
| the sequence editor works | its `<form>` was nested inside another form, so the browser dropped it and every save posted to the wrong handler — a **422** on the only button that mattered |
| the editor was rebuilt | `api/web.py` **and** `api/templates/campaign.html` had been reverted by a concurrent break pass; the report described a page that never existed |
| it's deployed | the suite was at 82% and the deploy had not started; the containers still held the previous build |

In all three the *code* was right at some point and the *page* was not, and tests
passed throughout — because a test that asserts markup is PRESENT cannot tell you
the browser will honour it, and a test run against `tests/` says nothing about
what a container is serving.

**So the rule, and it is not "be more careful":**

1. `./scripts/deploy.sh`, and confirm the containers are NEW (`docker compose ps`
   — an old uptime means nothing you changed is running)
2. `curl` the actual route and assert against the returned HTML
3. Only then say it works

Cheap, mechanical, and it caught what three rounds of care did not. The same
shape as everything else in this file: prefer a check that reads the real artefact
over a claim about the thing that produces it.

### ⚠️ STANDING RULE — verification may READ live data, never WRITE it

The rule above says fetch the real route before reporting a UI fix. It has a hole,
and on **2026-09-10** the hole cost real work: verifying a **save** means POSTing,
and `POST /campaign/<id>/steps` REPLACES that campaign's sequence. Those POSTs went
to the live `Drip 1` while checking a blank-delay fix, and four steps of Sean's copy
were replaced with `b1`/`b2`/`b3`/`b4`.

It read as a save bug — "the editor shows old test content", "the database has two
rows per position", "editing a delay duplicated the rows" — and it was none of
those. `save_steps` had behaved exactly as designed: soft-delete what was not
posted, insert what was. The duplicate positions were soft-deleted history, which
is what keeps a sent step's record after its copy changes.

Two things saved it, and neither was a rule:

* `save_steps` SOFT-deletes, so every superseded row was still there to restore
* the copy had been written recently enough that `created_at` ordering told the
  story

**So: a curl that WRITES gets a scratch target, every time — and the diff catches
the time you forget.**

    ./scripts/scratch.sh snapshot            # before verifying anything
    CID=$(./scripts/scratch.sh new)          # a scratch DRIP campaign
    CID=$(./scripts/scratch.sh new-call)     # ... or a CALL campaign
    LID=$(./scripts/scratch.sh new-lead)     # ... or a LEAD, not dialable
    curl ... -X POST ".../campaign/$CID/steps" ...
    ./scripts/scratch.sh diff                # ⚠️ did I touch REAL data?
    ./scripts/scratch.sh clean               # refuses if a REAL lead attached

⚠️ **THE FIRST VERSION OF THIS RULE COVERED DRIPS ONLY, AND THAT IS WHY IT
RECURRED.** A day after it was written, a full config POST went to the live `C1`
to check a new selector — changing its prompt version and blanking its notes —
because there was no safe target for a call campaign and the rule's example only
showed a drip. **A rule with a hole in it gets used on the other side of the
hole.** The script was `scratch_drip.sh` while it already made call campaigns; the
name describing less than the thing is what made the hole readable as the whole.

`snapshot` / `diff` are the part that actually closes it. A scratch target only
helps when you remember to use one; **the diff catches the time you did not**,
within seconds, by hashing every live table — `leads`, `campaign_configs`,
`suppression`, `email_do_not_send`, `email_sends`, `email_clicks`, `drip_steps` —
excluding `SCRATCH-` rows.

⚠️ **TWO TABLES CANNOT BE PUT BACK: `suppression` and `email_do_not_send`.**
Deleting a mistaken row does not undo it, because their entire purpose is to
outlive the lead — a wrong entry is a firm never contacted again with nothing on
screen saying why, and a wrongly REMOVED one is a compliance failure. Verification
must never write to either. `/dnc` is the only route in, deliberately.

`list` shows leftovers. The `SCRATCH-` prefix is the whole mechanism: it labels
itself on /campaigns and /leads, which `DRIP-LIVETEST` and `DRIP-REPRO` did not —
they sat in the dev database for days and had to be asked about four times.

And the diagnostic lesson, which is the same one as the whole file: **a report of
what a bug looks like is evidence, not a diagnosis.** Duplicated rows and resurrected
content are exactly what an append bug looks like from the outside; the row
timestamps said something else. Check `created_at` against the session's own
commands before adding a constraint. The constraint asked for here — a plain
`UNIQUE (campaign_id, position)` — would have refused EVERY save, because
soft-deleted rows keep their positions. `drip_steps_position` is already
`UNIQUE (campaign_id, position) WHERE deleted_at IS NULL`, which is the same
intent expressed correctly, and `PARK_OFFSET` exists to reorder underneath it.

### ⚠️ STANDING RULE — NOT NULL does not mean "has a value"

`email_sends.to_email` was `text NOT NULL` from the day it was created, and the
database still ended up holding two rows that claimed a send to nobody. Migration
036's backfill wrote `coalesce(l.dm_email, '')`, because it had to satisfy the
constraint and had nothing to write.

**A writer that must satisfy NOT NULL and has nothing to say will write the empty
string** — and from then on every reader has to know that `''` means absent. That
knowledge lives nowhere, so it is forgotten, and `''` starts being counted: the
drip roster read "1 sent, sequence finished" for a lead that had received nothing.

The same shape in the same table, one column over: `sent_at timestamptz NOT NULL
DEFAULT now()`. Any insert that did not mention it declared a send. **The DEFAULT
did the lying** — no code ever decided to claim those emails had gone.

So, for any column where absence is possible:

| what you want | what to write |
|---|---|
| it must have a real value | `NOT NULL` **and** `CHECK (btrim(col) <> '')` |
| it may be absent | nullable, and **no default that fabricates content** |
| it records an event | require its attribution: `CHECK (at IS NULL OR by IS NOT NULL)` |

That last one is the general form of the fix: **an event column and its actor
column travel together.** `sent_at` without `sent_by` is a claim nobody made.

The corollary for handlers, which cost a separate field the same day: **absent is
not empty.** `Form('')` makes a missing field arrive as `''`, indistinguishable
from a box the operator cleared — so a partial POST silently blanked whatever it
did not mention (`api/web.py`'s `lead_edit` erased `dm_email`, which ends a drip;
`campaign_save` erased notes). Declare optional form fields `Form(None)` and write
only what was posted. Breaks 134 and 135.

⚠️ And when a constraint like this goes on, **expect the test fixtures to fail
first**. Twenty did here, because `_join()` inserted `sent_at` with no `sent_by` —
fabricating the exact shape the constraint exists to forbid. A fixture that builds
impossible data is not testing the real thing, so fix the fixture, never the
constraint.

### ⚠️ STANDING RULE — a table designed to OUTLIVE A LEAD will outlive TEST ISOLATION

`tests/conftest.py` truncates between tests, and `TRUNCATE ... CASCADE` only
reaches tables with a foreign key path to the ones named. **So the property that
makes an exclusion list correct is the same property that breaks test
isolation**, and it is not a quirk of one table — it is true of every table of
that shape:

| table | keyed on | has an FK to `leads`? |
|---|---|---|
| `suppression` | the **phone** | no |
| `email_do_not_send` | the **address** | no |
| `email_audit` | nothing that cascades | no |

Each of these is deliberately *not* keyed on the lead, because it has to survive
the lead being archived, returned to the pool, re-uploaded or deduplicated. That
is exactly why a cascade cannot clear them, and why a row written by one test is
still there for every test that runs after it.

`email_do_not_send` was leaking this way from migration 029 until 2026-09-09,
when three drip tests failed for a reason that had nothing to do with the drip: a
lead was excluded by an address a completely different test had blocked. It is
the masked-guard shape pointed at the harness instead of the code, and the
harness is the worse place for it — a false exclusion there is invisible in every
test at once.

**So: name these tables EXPLICITLY in the TRUNCATE, never rely on a cascade
path.** Relying on the cascade means the next table built to outlive a lead
silently starts leaking, and the symptom will be a test failing somewhere else
entirely. **When you add a table that is keyed on a phone, an address, or
anything other than a lead, add it to that list in the same commit.**

### ⚠️ STANDING CHECK — a test may not configure what production ignores

`tests/test_no_dead_config.py` fails when a test writes a settings key no
production code reads, or a table production never touches. Nine of the eleven
masked guards traced back to exactly that: the test configured one place, the
dialer read another, and the seeds matched so the assertion was green.

It is source-aware on purpose. A plain string search calls `daily_cap` alive
because `campaign_configs` has a column of that name — which is the very
confusion that let the dead key survive. Adding to its allowlist is a review
decision, not a convenience; the only legitimate entries are tests exercising
`set_many` itself.

After any interrupted run:

```bash
python3 - <<'EOF'
import glob
for f in sorted(glob.glob('scripts/breaks/*.py')):
    d = {}
    exec(open(f).read(), d)
    src = open(d['TARGET']).read()
    if src.count(d['OLD']) != 1:
        print('LIVE BREAK or stale anchor:', f, '->', d['TARGET'])
EOF
```

`scripts/break_pass.sh` enforces the second half — it requires the **named
expected test** to fail, not merely that something did. Every one of the six
was found by that check, never by review.

## Status: PHASE 5 COMPLETE — L2 and L3

```
L1 cold call  ──confirmed email──▶  L2 you email them  ──"I emailed them"──▶  L3 follow-up
   agent_f10e…v7                       nothing dials                    agent_934d…v0, +3 days
```

**L1 → L2 fires only on a CONFIRMED email**, in the same transaction as the
capture. A lead sitting at L1 with a confirmed email is a state nobody can
reason about, and an unconfirmed one must never walk the ladder — a wrong
email is a dead lead that looks live.

**L2 never dials.** At L2 we owe them an email and haven't sent it; calling
would ask a question we're about to answer ourselves. Enforced in the
selection query (`STAGE_DIALABLE`), with its own break-pass entry.

**L3 uses the name.** The opener is *"Hi, it's Alex calling back for Sara — I
sent over a demand letter sample a few days ago."* The prompt carries a KNOWN
block (firm, contact, title, email, when we emailed) and an explicit **never
re-ask** section. Its scoring rubric adds `reasked_known_info` as the worst
fault an L3 call can commit — deduct 4 — because re-asking tells the firm
nobody listened the first time.

### The seam for the coming email automation

`stages.mark_emailed(lead_id, emailed_by=…)` is the **only** implementation of
L2 → L3. The button calls it with `operator`; the sequencer from
demandcounselor.com will call it with `auto:demandcounselor.com`. Two callers,
one transition — a test asserts the web handler contains no `UPDATE leads`, so
the automation cannot quietly become a second implementation.

`leads.replied_at` and `dialer.REPLIED_GUARD` are **already in the selection
query** and nothing sets them yet. A reply must stop the follow-up call dead;
adding that guard later would mean changing the dialer at the same moment the
sender arrives. `stages.record_reply()` is the matching writer, waiting.

### ⚠️ A live bug this phase caught — and what it invalidates

The L1 prompt has referenced `{{company}}` since it was written and **the
dialer never passed it** — Retell leaves an unsupplied variable in the prompt
text verbatim, so the agent was reasoning about a literal `{{company}}`.
`retell.dynamic_vars()` now builds the whole variable set in one place, and a
test parses the shipped L3 prompt and asserts every `{{var}}` it uses is
supplied.

**⚠️ EVERY CALL BEFORE 2026-09-07 RAN WITH A LITERAL `{{company}}` IN THE
PROMPT.** The agent did not know which firm it was calling. **The five phase-3
call scores are therefore PROVISIONAL** — they measure an agent operating
without the firm's name, which is a different conversation. The **latency**
numbers stand (prompt size and model are unaffected by an unresolved
variable), but any conclusion drawn from `agent_score`, `outcome_score` or the
failure mix on those five calls needs re-measuring on the fixed prompt before
it is treated as real.

## Phase 4 — the CRM

Four server-rendered screens, no build step, no login. **It lands on the
leads list**, not a numbers page — open it at 11am and see where each firm
stands.

```bash
ssh -L 4100:localhost:4100 root@ssh.demand.legaltoolsgpt.com
# then http://localhost:4100
```

| route | what it is |
|---|---|
| `/` | **the landing page** — searchable leads list, one row per firm, last scores and last quote |
| `/leads/{id}` | full activity timeline: every call, both scores, deductions, what they said, transcript; editable contact; mark DNC |
| `/campaigns` | the named configurations — create, start / stop, cap, spacing, windows, sender, email copy, retry ladders, CSV upload |
| `/today` | the digest as it currently stands, plus the needs-you queue |
| `/export.csv` | CSV export, respects the current filter |

**NO LOGIN IS DELIBERATE AND THE BINDING IS THE AUTH.** `caller-api` listens on
`127.0.0.1` and the nginx vhost proxies exactly one path (`/webhooks/retell`)
and 404s everything else. Verified from the public internet: `/`, `/leads`,
`/campaign`, `/today`, `/export.csv` and `/health` all return **404**, while
`/webhooks/retell` returns 405 to a GET (the route exists, the method does
not). **If anyone ever publishes port 4100, these pages become an
unauthenticated lead database with a DNC button on the internet.**

**Templates autoescape.** Company names and call transcripts are text other
people produced; a receptionist who says `<script>` must not have it execute.
Two tests assert escaping on both.

## Phase 3 — scoring and the digest

Every call gets one LLM pass (`claude-opus-5`). Two scores, never combined.
One email at end of day.

```bash
./scripts/score.sh              # score any unscored calls (worker does this every 60s)
./scripts/digest.sh             # preview today's digest
./scripts/digest.sh send        # send it (idempotent per day)
```

**Two scores, and the pair is the diagnosis.** `agent_score` is what we
control and should reach 9-10; `outcome_score` is the business metric and
never will. **High agent + low outcome means the LIST or the ASK is wrong,
not the script** — the digest says so explicitly when it sees that shape.
There is no combined column and a test asserts one cannot appear.

**`their_words` is verbatim.** The percentage says *that* calls fail; the
quote says *what was said*, which is the only thing you can write a better
opener against.

**Scoring can never break the call flow.** `score_pending()` swallows
everything and records failures in `score_attempts`; the digest surfaces any
unscored calls, so a silent scorer outage cannot look like a quiet day.

**The LLM reports. It does not edit the prompt.** Nothing in `api/scorer.py`
writes to `prompt_versions` or any agent config — asserted by a test.

## Measured cost — gpt-4.1 vs sonnet

Retell returns `call_cost` per call; we store it in `calls.cost_cents` rather
than estimate. Unit prices are **cents per second** (verified: units billed
equals call seconds exactly), so x60 gives cents/minute:

| line item | c/min |
|---|---|
| `claude_5_sonnet` | **8.00** |
| `gpt_4_1_high_priority` | **6.75** |
| `retell_voice_engine` | 5.50 |
| `elevenlabs_tts_03_2026` (custom voice) | 4.00 |
| `platform_tts` (retell-Rita) | 1.50 |
| `us_twilio_telephony` | 1.50 |
| `gpt_4_1_text_testing` (post-call analysis) | 1.5c flat per call |

**gpt-4.1 is 1.25 c/min cheaper than sonnet — and it was also 4x faster**
(llm p50 508ms vs 2836ms on an identical prompt). There is no
speed-vs-cost tradeoff here; gpt-4.1 wins both.

Full stack per minute: sonnet+Rita **16.50**, gpt-4.1+Rita **15.25**,
gpt-4.1+ElevenLabs **17.75**. At 200 calls/day averaging 1.5 min that is
roughly **$52.50 / $48.75 / $56.25 per day** plus ~$3/day of post-call
analysis. **The custom ElevenLabs voice costs more than the model swap
saves** — +2.50 c/min against -1.25 c/min.

Scoring adds ~1.6c per call (~$3.20/day at 200), about 7% of call cost.

## Phase 2 — a real day can be run

Windows, cap, CSV upload, campaigns and suppression-in-the-selection-query are
in. 111 tests, break pass red on **eight** guards, each on its own named test.

```bash
./scripts/upload_csv.sh leads.csv      # -> the POOL. NEVER dials.
./scripts/campaign.sh cap 200
./scripts/campaign.sh enroll           # carry-overs first, then fresh to cap
./scripts/campaign.sh start            # nothing dials until this
./scripts/campaign.sh pause | resume | status | rollover
./scripts/why_not_dialed.sh <lead_id>  # "nothing dialed" is never a mystery
```

**The calling window** (`api/windows.py`) is two ANDed SQL fragments evaluated
in the CALLED PARTY's local time: the TCPA 08:00-20:30 legal window, and the
operator's per-weekday preference. The preference can only ever NARROW the
legal one - structurally, not by convention. Note that with the default
09:00-17:00 preference the legal fragment is redundant; it only binds when the
preference is widened, which is exactly what its test does.

**The cap is a TOTAL.** 200 with 50 carry-overs means 150 fresh. The one
asymmetry: if carry-overs alone meet the cap they still all dial and zero
fresh are added - a promised callback beats a cold call.

**Uploading never dials.** Rows land `pool_status='pool'`; the dialer requires
`'active'` AND membership in a STARTED campaign. Two independent reasons, so
forgetting one places no calls.

**`leads.timezone` is enforced as an IANA region/city name** by a trigger
(migration 003). Postgres accepts `-05:00` in `AT TIME ZONE` without
complaint, so nothing caught a hand-written offset before - and that failure
is silent for a few weeks after each DST change.

## Phase 1 — verified end to end on a real call

`dialed -> call_ended -> call_analyzed -> lead row` proven on 2026-09-07 with
`dm_name`, `dm_email` and **`dm_email_confirmed = TRUE`** captured off a real
spellback. Replay is idempotent, unsigned POSTs 401, break pass red on four
guards. `DIAL_ALLOWLIST` still contains one number.

## Measured latency — the model is the lever, not the prompt

Four real calls, one variable at a time. p50, ms:

| version | model | prompt | e2e | llm | tts |
|---|---|---|---|---|---|
| v1 | claude-5-sonnet | 5,338 ch | 2592 | 2201 | 165 |
| v2 | claude-5-sonnet | 3,368 ch | 2325 | 2040 | 132 |
| v3 | claude-5-sonnet | 8,047 ch | 3179 | 2836 | 212 |
| **v4** | **gpt-4.1** | **8,047 ch** | **792** | **508** | **139** |

**v3 vs v4 is a controlled test — identical prompt, identical voice and
endpointing settings, only the model differs: llm p50 2836 -> 508, a 82%
drop.** Prompt length is real but second order: within one model, trimming
5,338 -> 3,368 chars bought only 161 ms, and the cost per char roughly doubles
as the prompt grows (0.082 ms/char at 3-5k, 0.170 ms/char at 8k). The longest
prompt on the fast model still beat the shortest prompt on the slow one by
1,500 ms.

TTS (132-212 ms) and ASR (125-181 ms) never mattered. `responsiveness` was
already at ceiling, so endpointing was never the cause either.

Anything recorded in `prompt_versions` (phase 3) must carry the **model** as
well as the prompt text, or a score change after a swap like this is
unattributable.

## (superseded) Phase 1 build notes — still nothing can dial

Retell client, webhook inbox, drain and dialer are in and tested.
`DIAL_ALLOWLIST` is **empty**, so no number can be dialed.

Two steps remain to close phase 1 end to end:

1. **Expose the webhook.** Create a DNS A record for a hostname pointing at
   this box, **Cloudflare proxy OFF (grey)**, then:
   ```bash
   ./scripts/expose_webhook.sh caller.legaltoolsgpt.com
   ```
   It checks DNS, writes an nginx vhost that exposes **only**
   `/webhooks/retell` (everything else 404s), gets a cert, proves an unsigned
   POST gets 401 from the public internet, and sets `webhook_url` on the
   agent. The agent currently has **no** webhook URL, so Retell has nowhere
   to deliver events.
2. **Put a number on the allowlist**, then add a lead:
   ```bash
   # DIAL_ALLOWLIST=+1XXXXXXXXXX in .env, then:
   docker compose up -d
   ./scripts/add_lead.sh "My Cell" +1XXXXXXXXXX America/Los_Angeles
   ```
   The worker dials on a 2-minute tick.

## Run it

```bash
./scripts/up.sh          # migrations FIRST, then api + worker
docker compose ps
curl -s localhost:4100/health
```

Migrations run before the app containers come up. `scripts/up.sh` enforces
that ordering rather than relying on anyone remembering it.

## Test it

```bash
./scripts/test.sh -q             # the suite, inside the caller-api image
./scripts/break_pass.sh          # prove those tests are not decoration
```

Tests run **in the container** (host python is PEP 668 managed) against
`caller_test_db`, never the database the containers use. `api/` is **mounted**
into the test run — without that, a break-pass edit would land on the host and
change nothing in the container, reporting a landed edit and a green suite.

`break_pass.sh` removes each guard, **confirms the edit actually landed**, and
requires the suite to go red before restoring it. A break that did not land is
not a break, so the script reports whether the patch applied *before* it reads
any test result.

## The dial guard

`api/guards.py` is pure — no database, no network, no app imports. It takes a
number and a config and either returns or raises `DialRefused`. It fails
**closed**: an error, a missing config, or an unrecognised mode all result in
no call being placed.

- **Dev is `DIAL_MODE=allowlist`** with an explicit list of numbers you own.
  An empty `DIAL_ALLOWLIST` dials nothing. **That is correct, not a bug.**
- **Prod is `DIAL_MODE=unrestricted`.** Absent means `allowlist`, so a
  misconfigured box is inert rather than loose.
- Absent and empty are treated differently on purpose: absent → `allowlist`
  (safe), empty → refused as an unknown mode. Empty is garbage, not a signal
  to relax.

## Ports

Everything binds `127.0.0.1`. Nothing here is on the internet.

| port | service |
|---|---|
| 4100 | `caller-api` (SSH tunnel: `ssh -L 4100:localhost:4100 root@dev`) |
| 4102 | `caller-postgres` |

The one public route this service will ever expose is `POST /webhooks/retell`,
on its own hostname. It does not exist yet.

## Conventions

- **No default-fallback interpolation in compose.** Every variable is the
  question-mark form. A silent fallback to a stale value looks exactly like
  success.
  - The single exception in spirit is `DIAL_ALLOWLIST`, which uses the
    no-colon form so a deliberate *empty* value is allowed. It still has no
    default and still fails loudly when unset.
- **Migrations are forward-only.** One per phase. Never edit an applied one.
- **`db.get_conn()`**, `RealDictCursor`, so rows are `r['col']` never `r[0]`.
- **No tech debt.** Three similar lines beats a premature abstraction.

## Retell notes

**The signature is not a plain HMAC of the body.** Retell sends
`x-retell-signature: v=<unix_ms>,d=<64 hex>` and signs **body + timestamp**,
with a 5-minute replay window. `hmac(api_key, body)` rejects every genuine
webhook and reads as a credentials problem. `api/retell.py` imports Retell's
own verifier rather than transcribing it.

**Read `call_analyzed`, not `call_ended`.** The `call_ended` payload has no
`call_analysis` at all.

**Calls that never connect** (`dial_failed`, `dial_no_answer`, `dial_busy`)
skip `call_started` but still fire the other two.

**Custom analysis fields are absent when no conversation happened.**
`drain._field()` is the only reader; it returns `None` for absent, empty, and
the literal junk a JSON bridge produces, so `dm_email` can never become the
string `"undefined"`.
