# CALLER — HANDOFF

Outbound calling for CounselorAI. Dials personal-injury firms through Retell,
scores every call, drafts a follow-up email that a person sends by hand.

**Read this for state. Do not trust anyone's memory, including mine.**
Last updated 2026-09-09. Every number in the table below was read from
`caller_db`, not carried over from the previous edit.

---

## Where it stands right now

| | |
|---|---|
| Repo | `git@github.com:Cali1900/caller.git`, branch `main`, all work pushed |
| Last migration | `20260909_039_timezone_may_be_absent.sql` |
| Tests | 675 passed, 1 skipped |
| Break pass | **103 definitions** (97–104 added for the drip). Full pass GREEN across all 103 at 2026-09-09T17:23Z, suite 632. The FIRST run of that pass FAILED — break 100 reported GREEN because a drip test was passing with zero clicks; see masked-guard row 12 in README.md |
| Campaigns | `C1` only (type `call`), **stopped**. `C2` no longer exists |
| Data | 1,087 leads — **all 1,087 in the pool, 0 queued** — 2 calls, 3 suppressed, 0 archived |

**NOTHING IS DIALING, for THREE independent reasons.** Any one of them alone
would be enough; all three are deliberate:

1. `C1` is **stopped** (`is_running = false`). Nothing dials while nothing runs.
2. **No lead is queued.** All 1,087 sit at `pool_status='pool'`; the dialer
   requires `'active'` AND membership in a started campaign.
3. `DIAL_MODE=allowlist` with one number on it (Sean's cell), so even a queued
   lead on a running campaign refuses at `assert_dialable`.

Starting a campaign is a deliberate act on `/campaigns`, and queueing is a
separate deliberate act on `/leads`. **Do not assume `C1` is running because
someone said so — read `is_running`.** It was believed to be running on
2026-09-09 and was not.

### ⚠️ Open before this is "done"

1. **Rename `leads.stage`** — see "Naming" below. Recorded, not done.
2. Auto-send is BUILT and deliberately not wired — see the standing decision
   on it below, and `tests/test_no_dead_config.py`.
3. `archived_contacts` has a reader (`archive.contacts()`) and tests but **no
   template** — the lead detail screen does not yet show "emailed Sep 2026, 2
   clicks, said no on Sep 8".

---

## The model — read this before changing anything

**A campaign is a NAMED CONFIGURATION.** Not a day, not a prompt version. It
owns the prompt version, sender identity, cap, spacing, calling windows, email
copy and notes.

* **Exactly one runs at a time**, enforced by a unique partial index
  (`one_running_campaign`), not by code being careful.
* **The switch is never silent.** `campaigns.start()` refuses while another
  runs and raises `CampaignConflict` carrying what is running, so the caller
  must come back with an explicit "stop that one and start this one".
* **Leads are ASSIGNED** (`leads.campaign_id`) and **QUEUED** separately
  (`pool_status='active'`). Switching which campaign runs reassigns nothing, so
  a campaign resumes where it left off.
* Two campaigns can run different prompts and different email copy. That is
  most of the reason to have a second campaign.

Config that must change **without a deploy** lives in the database and is
edited in the UI: cap, spacing, windows, sender, email copy. Env requires a
container recreate, and a recreate during calling hours is how you drop a call.

---

## Safety — the things that stop this hurting someone

| Guard | Where | What it stops |
|---|---|---|
| `DIAL_ALLOWLIST` | `guards.assert_dialable` | dev dialing strangers. **Fails closed**: any DIAL_MODE that is not exactly `unrestricted` or `allowlist` refuses |
| suppression | selection join **and** re-checked in the dial transaction | calling someone who said remove me |
| TCPA legal window | `windows.LEGAL_WINDOW` | 08:00–20:30 in the **called party's** local time, IANA names, never offsets |
| campaign window | `campaign_windows` | can only ever narrow the legal window |
| campaign running | selection **and** dial | a stopped campaign dialing |
| daily cap | `guards.assert_under_daily_cap` | per campaign; carry-overs are exempt |
| `max_concurrent` | `dialer.run_once` | one tick placing the whole queue back to back |

`scripts/deploy.sh` pauses dialing, rebuilds, waits for health, resumes, with
`trap restore EXIT`. **Nothing restarts a container during calling hours
without pausing first.**

---

## ⚠️ Operating rules learned the hard way

**Never run the break pass under a timeout that can kill it, and never stage or
commit while it is running.** It mutates real source files. It has been killed
mid-run and left a break LIVE in `api/` — once with `raise` in the scorer's
`except`, once with **the allowlist check deleted**.

It now survives that: originals + md5 manifest + an `ACTIVE` journal in a fixed
`.break_pass_state/`, traps on `INT TERM HUP` as well as `EXIT`, and because
SIGKILL cannot be trapped the state directory **survives on purpose** so the
next run recovers and refuses to proceed until clean.

```bash
./scripts/break_pass.sh --check     # is a break live in api/ right now?
./scripts/break_pass.sh --recover   # restore from a killed run
./scripts/break_pass.sh --only=19   # one break
```

**⚠️ NEVER RUN TWO BREAK PASSES, AND THE LOCK NOW ACTUALLY PREVENTS IT.**
Until 2026-09-09 `preflight` ran BEFORE the lock, so a second invocation
"recovered" from the RUNNING pass's state directory and deleted it. The running
pass then could not restore, stopped correctly, and left a break live in
`api/drip.py` — and every subsequent run snapshotted the broken file as its
"original" and reported `RESTORE VERIFIED ✓` against it. `--check` said clean
and a full suite passed. Only `breaks_anchor_check.py` caught it.

Three fixes: the lock comes first, `--check` runs the anchor check (a state
directory only detects a KILLED run, not a completed one that failed to
restore), and `break_pass.sh` refuses to start when a break is already live.

**`./scripts/break_pass.sh --check` is the answer to "is a break live?"** — it
reads `api/` against every definition and does not depend on any state
surviving. `git diff -- api/` is the other. A "RESTORE VERIFIED" is only as good
as the snapshot it verifies against.

**⚠️ Break numbers ≥100 sort BEFORE two-digit ones.** `100_…` sorts right after
`09_…`, so `--from=N` and the chunk banners are POSITIONS, not break numbers.
`--only=N` matches by name and is reliable. Zero-padding the filenames would fix
it and has not been done.

**One test run at a time.** Three concurrent runs against one test database
produced hours of results that looked like code regressions — breaks
reappearing after restore, deadlocked TRUNCATEs, failures moving between runs.

⚠️ **This file claimed that guard existed from 2026-09-08. It did not** — the
`flock` was in `break_pass.sh` and only ever guarded passes against each other;
`scripts/test.sh` was seventeen lines with no guard at all. Added 2026-09-09,
and it is two checks because there are two different collisions:

* **test vs test** — `test.sh` takes an exclusive lock of its OWN
  (`.test_run.lock`), deliberately *not* `break_pass`'s lock: the pass invokes
  `test.sh`, so sharing one would make the pass block on itself. That is the
  same shape as the `pgrep` guard that matched its own command line.
* **test vs pass** — `test.sh` refuses while `.break_pass_state/` exists. This
  is the direction nothing covered: `break_pass.sh` checks for test containers
  at STARTUP only, so a run started at minute five of a pass read a
  deliberately broken tree and reported failures that were not regressions.

`BREAK_PASS=1` (exported by `break_pass.sh`) skips both — the pass already
holds its own lock and already checked. Verified in all three states.

If results look impossible, check for stray processes before you debug the code.

**A guard is only tested if the test ISOLATES it** — see the table in
`README.md`, which is the ONE place the instances are counted. It has been
stated as six, nine and eleven in three different places; do not add a fourth
count here, add a row to the table.

**A test may not share state with the next test.** `tests/conftest.py`
truncates between tests, and `TRUNCATE ... CASCADE` only reaches tables with a
foreign key to the ones named. **`email_do_not_send` has no FK to `leads` on
purpose** — it is keyed on the ADDRESS so it outlives the lead — which meant it
was outliving test isolation too: an address blocked by one test silently
excluded leads in every test that ran after it. `email_audit` had the same gap.
Found on 2026-09-09 when three drip tests failed for a reason that had nothing to
do with the drip. The non-cascading tables are now named EXPLICITLY rather than
left to a cascade path a future table can quietly fall outside of.

**A test may not configure what production ignores.**
`tests/test_no_dead_config.py` fails when a test writes a settings key or a
table nothing consumes. Most of the masked guards in that table were exactly
that: the test configured one place, the dialer read another, and matching
seeds hid it. Its allowlist is a review decision, not a convenience.

**Migrations are forward-only.** `git stash -u` is banned in the sibling
legalflow repo (it eats `models/`); no equivalent trap here, but the habit
stands.

---

## Layout

```
api/
  dialer.py      selection + claim + dial. Every guard re-checked in the dial txn
  guards.py      the refusals. Each one has a break definition
  campaigns.py   named configurations, windows, one-running enforcement
  drafts.py      generates + retargets follow-up drafts. NEVER SENDS
  scorer.py      per-call scoring. Two scores, never averaged together
  digest.py      end-of-day. Day boundary is the OPERATOR's, not UTC's
  stages.py      the confirmed-email transition. mark_emailed() is the seam,
                 stage_label() the ONE place L1/L2 is still spoken
  web.py         the CRM. SSH tunnel only
  worker.py      the loop. next_gap() is module level so tests call the real one
  sender.py      the ONLY thing that puts campaign mail on the wire
  autosend.py    the email-1 gate. Decides, never sends. Fails closed
  archive.py     a lead rests, then returns. Writes `leads` and NOTHING else
  clicks.py      click tracking. Public endpoint, token only
  retry_ladder.py per-outcome retry gaps, per campaign. A rung is a DURATION
  drip.py        the email sequence. Steps are ROWS; delays anchor to emailed_at
  why.py         "why is it here", assembled from existing state
  funnel.py      / forecast.py / volume.py  the numbers screens
migrations/      forward-only, applied by scripts/migrate.sh
scripts/breaks/  111 break definitions, one per guard
```

## Running it

```bash
./scripts/up.sh                     # bring it up
./scripts/test.sh                   # the suite (ONE at a time)
./scripts/break_pass.sh             # ~20 min, run it in the background
./scripts/deploy.sh                 # pause -> rebuild -> health -> resume
./scripts/why_not_dialed.sh <id>    # why a lead is not a candidate
```

The CRM is loopback only (`127.0.0.1:4100`). Reach it through an SSH tunnel;
never publish it.

---

## Two live bugs fixed on 2026-09-08, worth knowing about

**The digest was dropping evening calls.** It computed the day boundary in the
operator's timezone but applied it in UTC, so between 17:00 Pacific and
midnight UTC the digest for "today" silently omitted calls that had already
happened. Comparing a `timestamptz` against a bare `::date` applies the SESSION
timezone. Pinned by `test_the_digest_day_is_the_operators_day_not_utcs`.

**`dial_one` trusted a stale campaign snapshot.** It used the campaign row
captured at SELECTION time, so a pause mid-batch was invisible to the
in-transaction re-check that exists for exactly that case — and stopping A to
start B let A's claimed lead dial under **B's** cap, spacing and prompt
version. It now reads the lead's own `campaign_id` from the database inside the
transaction, with no fallback to `running()`.

---

## Email-only leads: a list of firms, no calls involved

`upload_emails()` takes a CSV of **company + email**. `website` and
`demands_per_month` are optional and both earn their place: the website is what
autosend's DOMAIN check compares the address against — the one exclusion NOT
relaxed for an import — and the volume figure is what the forecast reads.

The operator says which KIND of list it is on `/leads`. **Sniffing the headers
would mean a file missing its phone column by mistake silently imports as
email-only**, and 500 leads that should be dialed sit undialable.

### ⚠️ THIS REMOVED A STRUCTURAL GUARANTEE

`leads.phone_e164` was NOT NULL, so a lead without a number **could not exist**
and therefore could not be dialed. Migration 038 made it nullable. From that
point only CODE stands between a phoneless lead and a call attempt:

| | |
|---|---|
| `dialer.PHONE_REQUIRED` | `AND l.phone_e164 IS NOT NULL` — never a CANDIDATE. Silent, which is right for a filter. Break 106 |
| `guards.assert_has_phone` | refuses **loudly** with a `dial_audit` row if a phoneless lead reaches the dial path anyway. Break 107 |

Belt and braces, the same shape as suppression being in the join *and* re-checked
in the dial transaction. In `unrestricted` mode a NULL would otherwise reach
`retell.create_phone_call(to_number=None)` and read as a Retell problem rather
than a bad lead.

**⚠️ NEITHER IS KEYED ON `lead_source`. PHONE PRESENCE IS THE GATE; PROVENANCE IS
ONLY A RECORD.** An imported lead that turns out to be worth calling gets a
number by hand and becomes dialable, staying `lead_source='import'`. Filtering on
the source instead would make that case impossible and would *look* like a
tightening — `test_an_imported_lead_with_a_phone_added_by_hand_dials` exists to
stop exactly that.

### A phone and a timezone are saved together or not at all

`PREFERENCE_WINDOW` joins on `l.timezone`, so a NULL there matches **no window**:
the lead would look dialable — a number, on a campaign, queued — and never be
dialed, with nothing saying why. **That is the archive bug's exact shape**, so the
contact save refuses the pair half-filled. Break 112.

Migration 039 relaxed the IANA trigger to allow **absent** while still refusing
**wrong**: an offset like `-05:00` is still rejected, because Postgres accepts it
in `AT TIME ZONE` and the resulting error is silent for weeks after each DST
change.

### `lead_source`, and why not `dm_email_confirmed`

autosend's `UNCONFIRMED` ("not confirmed by the agent") and `NO_NAME` exclusions
substitute for a human having verified the contact. For a call-sourced lead the
evidence is a spellback on a recorded call; for an import it is a person choosing
to upload the file — **different in kind**, asserted once for a batch.

Setting `dm_email_confirmed = true` on import would fake the call-sourced
evidence and make that flag mean two different things depending on origin, with
the gate unable to tell them apart. One fact, two homes. So the exclusions are
**source-aware** and the call path is byte-for-byte as strict as it was — break
111 asserts that, by treating every lead as imported and watching the call-path
test go red.

**NOT relaxed for an import:** the domain check, the do-not-send list,
`replied_at`, `needs_human`, archived, and the dev allowlist.

`lead_source` is also a funnel filter, so call-sourced and imported can be
compared — whether the confirmed email and the referral line earn what they cost.
It never changes: a lead that arrived by import and later gets a phone stays
`import`, because the record is where it came from, not what has happened since.

### Step 1 of an imported lead IS email 1

A call-sourced lead reaches a drip by having email 1 sent, so `emailed_at` is
already stamped. An imported lead is added **directly** — there is no email 1
before the sequence.

So a lead on a drip with **no `emailed_at`** has step 1 due immediately, and
sending it stamps `emailed_at` through `stages.mark_emailed()` — the same
write-once transition the button and the sender use, not a second implementation.
From that instant it is indistinguishable from a call-sourced lead.

**ONE ANCHOR, NOT TWO.** The alternative was a `sequence_started_at` column, i.e.
two columns that must agree forever; `emailed_at` already means "when the
sequence started".

⚠️ **THE MIRROR CASE WAS A REAL BUG.** A call-sourced lead's email 1 is recorded
with `step_id NULL` (no drip existed when it went), and `ALREADY_SENT_STOP`
matches on `step_id` — so the drip's step 1 at delay 0 was unsent and due
**immediately after email 1**. The firm would get the same opener twice, minutes
apart. `enter()` now links email 1 to step 1, so both paths agree that step 1
means "the first email". Break 108. It only surfaced because the import forced
the question of what step 1 means when there is no call.

### `default_drip_id`: the call campaign chooses its sequence

⚠️ **WITHOUT IT, A SECOND DRIP SILENTLY STOPS THE FIRST'S FOLLOW-UPS.**
`only_drip()` assigns only when exactly ONE drip runs, so with two — a
call-sourced sequence and an imported one, for any reason — every call-sourced
lead that got email 1 joined **no drip at all**. Email 1 out, lead at `emailed`,
nothing following up, and nothing saying so until somebody opened that lead.

A campaign already owns email 1's copy, so owning what **follows** it is the same
shape rather than a new concept. `only_drip()` is now a fallback for the
single-drip case. A default pointing at a *stopped* drip routes **nowhere** rather
than falling back — falling back is how a lead lands on the wrong copy, and the
call-sourced opener ("your front desk pointed me your way") is false for an
imported lead. Break 109.

**The safety net for whatever that does not cover:** `/today`'s needs-you queue
surfaces **"emailed, on no drip — the sequence stalled"**. Break 110. Replied,
archived and terminal leads are excluded: those stopped on purpose.

### One more thing the partial index broke

Making `leads_phone_uniq` partial (`WHERE phone_e164 IS NOT NULL`) meant
`ON CONFLICT (phone_e164)` no longer had an arbiter — Postgres will not use a
partial unique index unless the statement repeats its predicate. **Twelve tests
went red immediately, all on the CALL path**, not the new one. Fixed in
`upload.py` and `scripts/add_lead.sh`; the other three `ON CONFLICT (phone_e164)`
sites target `suppression`, whose constraint was untouched.

## The drip

A lead's email sequence, owned by a **drip** campaign. `api/drip.py`.

**Entry is email 1 and nothing else.** `mark_emailed()` sets
`leads.drip_campaign_id` in the same transaction as the `emailed_at` stamp, and
auto-assigns when exactly ONE drip is running — no picker for a list of one. With
several, a person chooses on the lead.

⚠️ **`leads.campaign_id` IS NEVER MOVED.** The brief said the campaign_id should
move from the call campaign to the drip. It should not, and this is the one place
the design deviates from `DRIP_ARCHIVE_BRIEF.md`:

`leads.campaign_id` is doing a **second job** — neither `dial_audit` nor `calls`
records a campaign, so it is the only record of which campaign dialed a lead.
Moving it would (a) **leak the daily cap**, because
`guards.assert_under_daily_cap` counts leads currently assigned to the campaign,
so every send would let one extra fresh lead dial; and (b) **rewrite the funnel**,
because `funnel._where` filters on it, so a call campaign would lose exactly its
successes as they migrated out — "40 humans, 12 emails" decaying to "40 humans,
0 emails", which breaks the prompt-version comparisons the agent version is
pinned for. Same fault `archive.return_due()` refuses to commit.

The brief's stated goal is met anyway, at zero cost: entering a drip requires
`has_confirmed_email`, and `dialer.STAGE_DIALABLE` is
`AND NOT l.has_confirmed_email`, so **the call selector already skips every lead
in a drip.** No dialer change at all. `drip_campaign_id` joins `_CLEAR_GATES` —
a resting lead must not be in a drip.

**The sequence is Sean's.** `drip_steps` are ROWS: any number of steps, any
delays. `save_steps()` replaces the whole sequence in one act and **refuses it
whole** on an empty sequence, a repeated delay, a backwards delay, or a blank
subject or body — a half-saved sequence would schedule from steps nobody
approved.

⚠️ **DELAYS ANCHOR TO `emailed_at`, NEVER TO THE PREVIOUS STEP.** Chaining lets
the schedule drift by however long each send was late, and the drift compounds.
Break 97. This is also why `emailed_at` is write-once (break 18): a restamp would
move every scheduled send at once.

⚠️ **TWO DIFFERENT ANCHORS, and anyone reading one will assume the other:**

| | anchored to |
|---|---|
| the SCHEDULE (when a step is due) | `leads.emailed_at` — the FIRST send |
| CLICK TIMING (`minutes_since_sent`) | **that step's own send** (`email_sends.sent_at`) |

"clicked 47m after send" on step 3 must mean 47 minutes after **step 3** went
out; from `emailed_at` it would report every later click as "11 days after send"
— true of the sequence, useless about the email.

**One token per SEND, not per lead** (`email_sends.click_token`), which is what
makes a click attributable to the step that produced it: if step 1 pulls every
click the follow-ups are noise, if step 3 does the opener needs rewriting.
`leads.click_token` is gone.

`email_sends.sent_at` is **nullable** and that is load-bearing: a row means
*prepared*. Email 1's draft is rendered and STORED at capture time and break 60
pins that Copy and Send produce identical bytes, so the token must exist before
the send. `sent_at IS NULL` → the token is live and a click records
`minutes_since_sent = NULL`, which is correct — there is no send to measure from.

**A step already sent is never re-sent and never re-dated** (break 98). Every
mid-flight edit rule falls out of that one fragment rather than being coded case
by case: editing copy or a delay touches only leads who have not reached the
step, deleting one **soft-deletes** so those who got it keep the record, and
inserting one sends nobody backwards.

**One step per lead per tick** (break 104). After a pause several steps can be
due at once; sending them all puts three emails in front of one firm in a minute.

**What stops it:** a recorded reply (99), a stopped drip campaign (101 —
`is_running` is the switch, and it is unscoped from `one_running_campaign` so
many drips run at once), an archived lead, the email do-not-send list (102), a
terminal status, or a person on the lead page. **A CLICK DOES NOT** (100) — a
click is interest, not an answer, and stopping on one would silence the sequence
exactly when it is working. After the last step: `archive('no_reply')`, or
`hold` if the campaign says so.

**An email unsubscribe suppresses EMAIL ONLY** (103). Someone who does not want
our emails has not given up the right to be phoned about a case they asked about,
so `drip.stop()` never writes to `suppression`. A prose "take me off your list"
is broader — that is a person's call at the DNC button.

### Reaching the drip from the UI

`/campaigns` **creates both kinds.** The New Campaign form has a TYPE selector
(call | drip), defaulting to `call` so anything posting without the field behaves
as it always did, and a bad value is refused rather than silently becoming a call
campaign. Type is set at creation and `campaigns.update()` refuses it afterwards.

The list is **grouped and labelled by type**, with a drip's own columns (sequence
length, after-the-last-step, leads on it) rather than blank cap and spacing boxes
— a blank reads as "not configured yet" when it means "does not apply". A drip
with **no steps** says so on the list, because a drip with no sequence sends
nothing.

The page header no longer says "One runs at a time" globally. **That is true of
call campaigns and false of drips**, and the index enforcing it is scoped to
`type='call'`. It is now stated per type, and a test asserts that exact string is
gone.

A **drip's** page shows the sequence editor and hides prompt version, cap,
spacing, retry gaps and calling windows, plus a short card saying what a drip does
not have and why. A **call** campaign's page is unchanged — asserted by its own
test listing all seven sections, because taking something off the call page would
be the easy mistake in that change.

⚠️ **This was built and UNREACHABLE for one commit**: the drip shipped complete
with no way to create a drip campaign except SQL. Same fault as a control that
exists and cannot be found — the archive Restore button, inverted. When a feature
lands, check the path a person takes to reach it, not only that its tests pass.

## ⚠️ THE REPLY GATE IS NOT FAIL-CLOSED. SEAN READING HIS INBOX IS THE GUARD.

**Read this before touching the drip, and do not let it get softened.**

`DRIP_ARCHIVE_BRIEF.md` says *"nothing auto-sends if detection is unavailable —
fail closed, exactly like `assert_dialable`"*. **That property does not hold, and
cannot, while reply detection is manual.**

Reply detection IS manual and staying that way: Sean ticks a box, and
`stages.record_reply()` writes `replied_at`. There is nothing that can *be*
unavailable, so there is nothing to fail closed about.

What is true, precisely:

* `replied_at` is a sound gate for **"has a reply been RECORDED"**. Once it is
  set, `drip.REPLIED_STOP` ends the sequence. Break 99.
* It is **not** a gate for *"has the firm replied"*. Nothing in the system has
  any inbound signal at all.
* **THE WINDOW IS REAL AND UNCLOSEABLE WITHOUT INBOUND INGEST.** A firm that
  answers on Tuesday and is ticked on Wednesday **will receive** a step that fell
  due Tuesday night. No code check can see it.

That is accepted deliberately, at this volume, because Sean reads every reply —
which is exactly why the drip was safe to unpark. **The guard is a person, not a
code path.** It is written here rather than implied because "Sean is diligent"
being an unstated assumption is the actual danger: the next person to read
`REPLIED_STOP` will assume it means more than it does.

**The mitigation is a checkpoint, not a fix.** The drip never sends silently into
the future. `drip.upcoming()` feeds a **GOING OUT IN THE NEXT 24 HOURS** block in
the daily digest, listing every send **by step and by firm name** — not a count,
because "6 sends tomorrow" is not actionable and "step 2 to Whitfield Law" is.
One lead can then be stopped on its own lead page
(`POST /leads/{id}/drip/stop`) **without pausing the whole drip**.

Everything else in the send path still fails closed properly — see
`autosend.eligibility()`, which the drip reuses verbatim.

**If inbound ingest is ever built**, it becomes a SECOND WRITER to `replied_at`
rather than a rewrite: the gate, the dialer's `REPLIED_GUARD` and the drip all
already read that one field from that one place. At that point this section can
be deleted and the brief's fail-closed rule becomes true as written.

## The archive return clears GATES and keeps FACTS (2026-09-09)

`archive.return_due()` put a lead back in the pool, called it "a FRESH
prospect", and never reset `stage`. Nothing in this codebase writes
`stage='L1'` — `api/stages.py` only ever writes `'L2'` — and
`dialer.STAGE_DIALABLE` is `AND l.stage = 'L1'`.

So the three EMAIL-derived archive reasons (`no_reply`, `bad_email`,
`unsubscribed`), which can **only** be reached from L2 because they all require
email 1 to have gone out, produced leads that came back to the pool looking
fresh and **could never be dialed**. They could not be emailed either:
`emailed_at` survived and `mark_emailed()` is write-once. Unreachable down both
wires — the "nothing sits in limbo" property, inverted.

Every test in `tests/test_archive.py` builds its lead at `stage='L1'`, which is
what makes the suppression test properly isolated **and** what hid this. See
rows 10–11 of the masked-guard table in `README.md`.

**The rule now, and it is the thing to reason with:**

> **A GATE is cleared. A FACT is kept.**

| | | why |
|---|---|---|
| `stage` | **cleared** | blocks the dialer forever |
| `emailed_at` / `emailed_by` | **cleared** | `mark_emailed()` is write-once |
| `replied_at` / `reply_note` / `replied_by` | **cleared** | `dialer.REPLIED_GUARD` and auto-send exclusion 5. Six months on, a firm that said "not interested" is a legitimate prospect again — that is the whole premise of `refused` having a return date. Break 95, plus a companion test proving a **suppressed** number that also replied stays unreachable |
| `dm_email_confirmed` | **cleared** | not a fact about the firm — it records that WE verified the address, and that verification is stale at six months even when the address is not. Read by `autosend.eligibility()`, `drafts.generate_for()` and `advance_to_l2()` |
| `dm_email`, `dm_name`, `dm_title`, `website`, `gatekeeper_name`, `demands_per_month`, `notes`, `tags` | **kept** | we paid a call to learn them; six months does not make them untrue |
| `first_dialed_at` | **kept** | the cohort key the funnel measures against |
| suppression, `email_do_not_send` | **never touched** | keyed on the phone and the address, not the lead |

The lead comes back holding the address and needing it **re-confirmed**, which
is what the next call is for: it either re-confirms the contact or updates it.
**Nothing auto-advances a returned lead to L2** — `advance_to_l2()` is only
called on a fresh capture (`drain.py`) or a person ticking confirmed
(`web.py`), never on a tick or at selection.

The send record is snapshotted into **`archived_contacts`** immediately before
the clear (`archive.contacts(lead_id)` reads it), so "we emailed this firm in
September and heard nothing" survives. It is HISTORY, never a gate — nothing
reads it to decide whether to dial or send. `email_clicks` rows are NOT
deleted; `minutes_since_sent` was computed and stored at click time, so the
timings stay true after `emailed_at` is cleared.

Breaks **91–93**. The hand pull (`unarchive()`) and the nightly sweep leave a
lead in the *same* state, asserted by a test — two paths that disagree is "why
is this one different" with no answer.

### Getting a lead back out of archive

**One control: "Return to the pool now"** in the Archive section of lead detail
(`POST /leads/{id}/unarchive` → `archive.unarchive()`). It is the SAME code the
nightly sweep runs — they share `_CLEAR_GATES` and `_snapshot_contacts`, and a
test asserts the hand pull and the sweep leave a lead in *identical* state. It
clears the campaign, the attempts and the four gates, snapshots the send record,
keeps every contact fact, and writes the return to the timeline.

⚠️ **THE STATUS DROPDOWN REFUSES TO DO IT**, and that refusal is the exact
mirror of the one that already stops a lead being moved *into* `archived`.

A bare `UPDATE leads SET status` on an archived lead leaves `pool_status='done'`,
`campaign_id` NULL, `archived_at`/`returns_at` set, and every gate still set —
un-archived in name only, undialable and un-emailable. And **permanently**, two
ways: `return_due()` selects `WHERE status='archived'`, so the nightly sweep can
never see it again; and `lead.html` renders the Restore button only for an
archived lead, so the one control that would repair it disappears from the page.
No route back but hand SQL.

It refuses rather than quietly performing the restore instead. `unarchive()`
snapshots the send record and clears four gates — a great deal more than "set
the status" — and a dropdown that silently did all that would make the timeline
lie about what a person did. **Same discipline as `/dnc` being the only route to
suppression.** Break 96.

⚠️ Not yet surfaced in the UI: `archived_contacts` has a reader and a test but
no template. The lead detail screen does not yet show "emailed Sep 2026, 2
clicks, no reply".

## The retry ladder has no `next_day` rung (2026-09-09)

A rung is a **duration** — `15m`, `4h`, `3d` — and nothing else.

**THE CALLING WINDOW IS THE CLAMP, NOT THE LADDER.** `next_attempt_at` is a
NOT-BEFORE gate, never a scheduled dial time: `windows.LEGAL_WINDOW` and
`windows.PREFERENCE_WINDOW` are ANDed into the selection query in the called
party's local time, so **no rung value can place a call outside the allowed
hours**, and none of them needs to try. A gap landing at 03:00 simply waits for
the window to open.

`next_day` computed 09:00 tomorrow in the lead's timezone. The case for it was
that a plain `1d` after a 19:50 dial lands at 19:50, which the window pushes to
the following morning — a day later than intended. **That is only reachable if
the preference window is wide enough to have dialed at 19:50 in the first
place.** Under the default 09:00–17:00 it cannot happen: the previous attempt
was inside the window, so the same local time tomorrow is inside it too.

So it bought nothing at the configured hours, and it cost the only raw-SQL
fragment in `api/retry_ladder.py`, a special case in three functions, and its
own break definition. Migration 033 rewrote the defaults and the one live row
(`C1` carried `next_day` in `retry_busy` and `retry_voicemail`).

| | now |
|---|---|
| `busy` | `15m → 1h → 4h → 1d` |
| `no_answer` | `2h → 8h → 1d → 3d` (unchanged) |
| `voicemail` | `1d` |
| unreadable rung | `retry_ladder.FALLBACK_RUNG = '1d'` — the WIDEST default rung. Unknown must never dial faster than configured, the same property `worker.next_gap()` holds for spacing |

Break 84 is **deleted**. `test_next_day_lands_in_the_called_partys_morning_not_ours`
is replaced by `test_the_ladder_is_timezone_free`, which asserts the exact
inverse — two leads three zones apart get the SAME instant for the same rung —
so it goes red if anyone reintroduces timezone arithmetic into a rung.

**If the evening window is ever widened, reconsider this.** And bring it back
with a test that widens the window, because that is the only condition under
which it is observable at all — the same trap as masked guard #1.

## settings.py is DELETED — and why that mattered

`api/settings.py` and the `settings` table are gone (migration 015). Every key
had moved onto the campaign; what remained was ten inert rows that still LOOKED
authoritative.

That was not cosmetic. `scripts/deploy.sh` paused before every restart by
writing `settings['dialing_enabled']`, which nothing had read for days — so its
pause was a **no-op**, and a deploy during calling hours would have rebuilt
straight through a live call while reporting that it had paused.

Three properties that lived in that module were ported, not dropped:

* **range refusal** → DB `CHECK` constraints on `campaign_configs`, which is
  stronger: a script or a stray `UPDATE` cannot get round them either
* **agent version range** → the same, newly added (it had no constraint)
* **"an outage must not speed up dialing"** → `worker.next_gap()` caught
  nothing and would have *raised* on a DB outage; it now falls back to the wide
  210–300 default. Unknown must never dial faster than configured.

`tests/test_no_dead_config.py` asserts the module stays deleted. A global
settings store returning is a design decision that should break that test and
be argued for — not something that reappears because one value had nowhere
obvious to live.

Archived: `/root/caller-archive/settings.py.deleted-20260908`,
`/root/caller-archive/settings_table_20260908.sql`.

## ⚠️ THREE EXCLUSION LISTS — they must never merge

**All three now exist.** They all currently mean "do not auto-contact",
which is precisely how they would merge the first time someone edited one.

| list | keyed on | why | who can lift it |
|---|---|---|---|
| **suppression** | phone / firm | **compliance** — statutory damages behind it | nobody |
| **cooled** (`lost_no_response`) | lead | business rule, revisitable | Sean, by hand |
| **do-not-send** (`email_do_not_send`) | **the email ADDRESS** | the mailbox is dead | a new address just works |

Sean: *"Suppression is compliance with damages behind it; cooled is a business
rule I might change my mind about."*

Keying do-not-send on the ADDRESS rather than the lead or the firm is what
makes a bounce recoverable: a good address for the same firm still sends, and
the same dead address on a different lead still does not.

Each has its OWN guard and its OWN break definition. A single shared
"should_contact()" would be the merge.

do-not-send is `email_do_not_send` (migration 029), read by
`archive.is_do_not_send()` and checked in `autosend.eligibility()` AND again in
both `sender.send_one()` and `sender.send_manual()`. Breaks 80–81.

## Three defects found in the SAFETY TOOLING itself

All three shared one shape: **the tool reported success without doing the
thing.** Worth knowing about, because that shape is not caught by tests passing.

1. `deploy.sh` "paused" by writing a key nothing read (above).
2. The break pass leaked live breaks into `api/` when killed — once with the
   allowlist check deleted. Fixed with a fixed-path state dir, a journal, signal
   traps, and `--check`/`--recover`.

   **A second, different way the same guard was lost: `git add -A` DURING a
   pass.** Commit `aa0f9c8` captured `api/guards.py` with `assert_dialable`'s
   allowlist check replaced by a bare `return`, and pushed it; the pass restored
   the working tree seconds later, so the commit was wrong while the tree was
   right — and a later `git diff` looked innocent. Restored in `cc19c9c`.

   ⚠️ **`cc19c9c`'s own message names the wrong break.** It says "break 12".
   Break 12 is `12_score_clamp.py`, which targets `api/scorer.py`. The allowlist
   break is **break 01** (`01_allowlist.py`, `TARGET = api/guards.py`,
   `LABEL = 'delete the allowlist check'`). The commit message is pushed and not
   being rewritten; this is the correction.

   **No exposure, and the reason is structural, not luck:** `api/` is NOT
   bind-mounted into `caller-api` or `caller-worker` (`docker inspect` shows
   zero mounts — the code is baked into the image, which is exactly why
   `scripts/test.sh` has to mount `api/` explicitly). The broken `guards.py` was
   never executable by the running system; it would only have taken effect on
   the next `deploy.sh` rebuild. `dial_audit` and `calls` were also empty for
   the whole window.

   Prevented now by `.githooks/pre-commit`, which refuses to commit while a pass
   holds `.break_pass_state.lock` OR while `.break_pass_state/` exists.
   `core.hooksPath` is bootstrapped by `scripts/up.sh` — a fresh clone had zero
   protection until that was added.
3. The break pass's own concurrency guard used `pgrep -f 'scripts/test\.sh'`,
   which matched the *invoking shell's* command line and refused to let the pass
   start at all. It now looks for the `caller-caller-api-run` container — the
   thing that would actually deadlock the database. **A safety check that fires
   on itself is worse than none: it trains you to bypass it.**

## The ladder ENDS at L2 (2026-09-08)

  L1  cold call the front desk. Goal: a name and a confirmed email.
  L2  we have the email. Nothing dials from here — the lead waits.

**L3's automatic follow-up call is unwired.** A campaign is already a named
configuration with its own prompt and its own leads, so a follow-up IS just
another campaign: assign the leads you want called back and start it when you
choose. Cleaner than a hardcoded ladder, and the operator controls when it
runs. The L3 agent still exists in Retell; only the app's scheduling is gone.

**THE SEAM IS KEPT.** `stages.mark_emailed()` still records `emailed_at` and
`emailed_by` — it just schedules nothing. That timestamp is load-bearing:

* the follow-up column on the leads list reads it
* click tracking computes "47m after send" from it
* every status change after a send is anchored to it

Marking twice is a no-op **by design**: the FIRST send is what timings are
measured from, and overwriting it would silently change every "N minutes after
send" already recorded. It does not touch `status` either — the email state is
derived from `emailed_at` in one place (`_EMAIL_STATE`), and a second source
for one fact is a source that drifts.

**Open design question for a follow-up campaign:** a campaign owns
`agent_l1_version`, so "a different prompt" today means a different *version of
the L1 agent*. The L3 agent is a different `agent_id` entirely. To dial a
follow-up with the L3 prompt, a campaign would need to point at an AGENT, not
just a version. Not built — flagging it because it is the obvious next question.

## Naming — what was fixed, and the one rename still owed

Done 2026-09-09:

* `campaigns.windows_for(campaign_id, cur, source)` → **`seed_windows_from(source, cur)`**.
  It never used the `campaign_id` it took, and it read as "the windows for this
  campaign" — which is `windows()`, a different function returning a different
  thing.
* `campaigns.create(..., type=...)` → **`campaign_type=`**. It shadowed the
  builtin inside the one function that decides a new campaign's whole shape.
  The COLUMN is still `type`. Break 78's anchor moved with it.
* `api/dialer.py`'s module docstring — it listed ten guards, **omitted
  `STAGE_DIALABLE` and `REPLIED_GUARD`** (both in the query twenty lines
  below), and named a `CAMPAIGN_JOIN` that exists nowhere in the repo. It is
  now split into SELECTION and BEFORE-THE-DIAL and matches the code. That
  omission is part of why the archive bug stayed invisible: the module's own
  index of what stops a call did not mention the filter that was stopping them.

### `stage` is renamed (2026-09-09)

`leads.stage` held two values and therefore one bit, named after a four-rung
ladder (L1→L2→L3→L4) that no longer existed. **The name is what hid the
archive bug**: nobody reading `return_due()` thinks "and reset the stage",
because "stage" does not sound like state a return should clear.

There were **three** stage-named columns and they meant three different things:

| was | now | why |
|---|---|---|
| `leads.stage` (`'L1'`/`'L2'`) | **`leads.has_confirmed_email`** (boolean) | one bit, now says so. `STAGE_DIALABLE` is `AND NOT l.has_confirmed_email` |
| `leads.stage_changed_at` | **`leads.email_confirmed_at`** | it only ever recorded that one transition |
| `leads.stage_attempts` | **dropped** | declared in migration 001, never read or written by anything since; all 1,087 rows held its `NOT NULL DEFAULT` of 0, so nothing was lost |

Boolean rather than a renamed text column on purpose: `boolean NOT NULL` is
**stronger than the old CHECK**, because an unrepresentable state cannot be
written by anything and there is no allowed-values list to fall out of step with
the code. That is migration 027's reasoning carried to its end. `NOT NULL`
matters as much as the type — a NULL would be neither true nor false and
`AND NOT l.has_confirmed_email` would silently drop the row.

### ⚠️ 'stage' STILL MEANS OTHER THINGS, and those are NOT renamed

The collision was half the confusion, so be precise about what was left alone:

| where | what it means |
|---|---|
| `calls.stage`, `scores.stage`, `activity.stage` | HISTORY about a past record — which agent ran that call. Not the lead's state now |
| `retell.agent_for()`, `STAGE_AGENTS` | which PROMPT to dial with |
| `forecast.py`, `pipeline.py` | funnel stage (`emailed → engaged → demo_booked`) |
| `prompt_versions.stage` | which agent a recorded prompt belongs to |
| the `/leads` filter on screen | still reads **L1 / L2**, because that is the vocabulary on every call record and timeline entry. `_lead_query` translates it to the boolean |

Because those boundaries legitimately still speak L1/L2, there is **exactly one
translation point**: `stages.stage_label()`. A test asserts no module outside
`api/stages.py` builds the label inline — scattering `'L2' if x else 'L1'` is
how two vocabularies drift and how somebody eventually writes a third.

**Break 35 has now been rewritten twice for the same reason.** Its "widening"
kept losing the ability to fail: first `('L1','L3')` after migration 027 made L3
unrepresentable, then `('L1','L2')` which under a boolean is *identical to
removing the filter* — already break 15. Two breaks doing one thing means one
guard nothing independently covers. It is now an **inversion** (a dropped
`NOT`), the realistic bug a boolean invites, and a genuinely different failure:
removal still dials the right leads among the wrong ones, inversion dials
exclusively the wrong ones.

Break 18's anchor also went stale in this change and `breaks_anchor_check.py`
caught it in one second — which is what `ee21fa3` built it for.

**Cost, recorded honestly:** the rename broke **58 tests** on the first run.
All mechanical (test helpers whose `INSERT INTO leads` formatting the pass did
not match), except one that was a mistake in the rename itself: a
find-and-replace renamed retell's `metadata['stage']` key, which is one of the
labels that was supposed to stay. That is why this did not ride along with the
archive fix — in one commit those failures would have had two candidate causes.

## Next, in order

**Click tracking (was "item 7") is BUILT** — `api/clicks.py`, migrations 016 and
017, breaks 37–40, `tests/test_clicks.py`. The sample link is rewritten to
`/c/{token}`; the endpoint logs and 302s to `counselorai.io/#letter`. It records
`clicked_at`, `minutes_since_sent`, `user_agent`, `ip` and a count, and an
UNKNOWN TOKEN STILL REDIRECTS so a scanner learns nothing from a 404.

**THE DRIP IS BUILT** (2026-09-09) — it was parked in error. See the drip
section below. **Reply ingest stays parked** and reply detection stays MANUAL;
read the fail-closed warning above before assuming otherwise.

Still specified and **not built**: the four in `BACKLOG.md` —
`B-batch-review`, `B-objection-scoring`, `B-scorer-model-cost`,
`B-demands-volume`.

---

## Standing decisions

* **Nothing auto-sends TODAY — and that is a decision, not a gap.**
  Auto-send is fully BUILT: `api/autosend.py` is the gate (seven exclusions,
  breaks 41–47) and `api/sender.py` is the only thing that puts campaign mail
  on the wire. It is off in two independent ways:
    1. `campaign_configs.email_1_mode` defaults to `'manual'`, and
       `autosend.eligibility()` returns NOT-OK for anything but `'auto'`.
    2. **`sender.run_once()` is deliberately NOT wired into `api/worker.py`**
       while the drip is parked. `web.py` calls only `send_manual()` — the
       "Send now" button a person presses.
  So setting a campaign to `'auto'` today sends nothing. That is intentional,
  and `tests/test_no_dead_config.py` asserts the worker does not call the
  sender loop so the next person reads a decision instead of a missing line.
  **Wiring one line into the worker turns on automated outbound email to law
  firms.** Do it deliberately, with the drip's reply gate in place, or not at
  all. Drafts are generated on capture and sent by hand; anything after a
  reply, Sean sends from his own inbox.
* **Links, never attachments.** The sample demand is a link; there is a guard
  test pinning that it is never attached. No media library.
* **No open tracking** — Apple pre-loads pixels, the number is noise. Replies
  and clicks only.
* **Recordings stay in Retell.** `opt_in_signed_url` is true; public recording
  URLs are not defensible.
* **The from-address is a list, not a text field.** Brevo only delivers from a
  verified sender. `api/senders.py` pulls the live list, unions it with the two
  offered addresses, and labels anything unverified. `info@counselorai.io` is
  the default. **Brevo's API needs a real User-Agent** — it answers curl with
  200 and Python's default urllib UA with 403 on `/v3/senders`.
* The from-NAME stays free text; Brevo does not verify display names.
* Sean writes the prompts and the email copy himself.
* Suppression is backed up weekly to DO Spaces (`caller-backups-sfo3`), keep
  12. **Restore tested, not assumed** — see `BACKUP.md`.
