# DRIP AND ARCHIVE — BUILD BRIEF

Supersedes the earlier drip spec (1:1 email campaign per call campaign), which
is retained commented-out in `BACKLOG.md` as history. That model could not
express many drips and tied a lead's sequence to whichever call campaign
sourced it.

**Status: PARKED 2026-09-09.** Sean reads every reply himself at this volume;
the drip is not worth building until that stops being true. Nothing below
exists, and reply ingest was reverted rather than left half-built - an applied
migration that is not in git makes dev and a fresh database disagree.

**Groundwork that IS being built now, because it is cheap today and a
migration against live campaigns later:** campaign type, and archive. Read the
archive section before touching it - the rest of this design assumes that
shape.

---

## The model

Every lead is in exactly one place, and everything terminates.

```
POOL
  │  select and add to a call campaign
  ▼
CALL CAMPAIGN
  ├─ email 1 sent          →  DRIP CAMPAIGN
  ├─ max attempts          →  ARCHIVE (max_attempts)
  ├─ refused               →  ARCHIVE (refused)
  └─ asked to be removed   →  ARCHIVE + suppression
DRIP CAMPAIGN
  ├─ replied or clicked    →  ENGAGED — Sean works it
  ├─ last step, silence    →  ARCHIVE (no_reply)
  ├─ bounced               →  bad_email, back to Sean
  └─ unsubscribed          →  ARCHIVE + email suppression
ARCHIVE
  └─ 6 months later        →  back to POOL
```

Nothing sits in limbo: a lead is worked by the machine, worked by Sean, or
resting.

## Campaign type

`campaign_configs` gains `type`: `call` or `drip`.

| | call | drip |
|---|---|---|
| prompt, cap, spacing, windows | yes | no |
| email copy, intervals, sender | email 1 only | all steps |
| how many run at once | **ONE** | **MANY** |

⚠️ `one_running_campaign` is currently `UNIQUE (is_running) WHERE is_running` -
it would block a second drip. **Scope it to `WHERE is_running AND type='call'`.**
Call stays one-at-a-time because there is one phone number and one worker, and
the stop-and-swap guard exists deliberately. Drip has no such constraint.

**Build ONE drip to start.** The flexibility is in the schema, not in what
exists on day one.

## Moving a lead

**The `campaign_id` moves. The row does not.** Same row, same calls, scores,
drafts, clicks and timeline. The call selector skips it because it filters on
campaign; the drip selector picks it up for the same reason.

NOT a separate table (would orphan history, and the six-month return would
mean moving it all back). NOT status alone (with several drips, status cannot
say which one).

**Sending email 1 is the only entry to a drip.** Auto-assign when exactly one
drip exists; ask only when there is more than one. No picker for a list of one.

## Archive is a status, not a table

`status='archived'`, plus `archived_at`, `archive_reason`
(`max_attempts | refused | no_reply | bad_email | unsubscribed | manual`) and
`returns_at = archived_at + 6 months`.

**The reason is the point.** Six months on, a firm that maxed out on no-answers
is a completely different prospect from one that said no.

One nightly sweep returns anything past `returns_at` to the pool: `campaign_id
= NULL`, status `new`, attempts reset.

⚠️ **THE RETURN MUST NOT CLEAR SUPPRESSION OR THE EMAIL DO-NOT-SEND LIST.**
They are keyed on the phone and the address, not the lead, and they outlive
everything. **This gets a test.** A suppressed number coming back out of
archive and being dialed is the failure this whole system exists to prevent.

## The drip

### THE SEQUENCE IS SEAN'S, NOT THE SCHEMA'S

Steps are ROWS, not columns. Three steps or seven; four days or thirty.

```
drip_steps: step_id, campaign_id, position, delay_days, subject, body,
            created_at, updated_at
```

`campaign_configs` keeps only what belongs to the campaign as a whole: email
1's mode and delay, the sender, and what happens after the last step.

**Refuse to save a sequence with no steps**, or with duplicate/out-of-order
delays - step 3 must not fall due before step 2.

### Delays anchor to step 1, ALWAYS

Every `delay_days` is measured from `emailed_at`, never from the previous send.
Chaining lets the schedule drift; anchoring does not.

This is why `emailed_at` is write-once and `mark_emailed()` is a no-op on a
second call - a restamp would move EVERY scheduled send and invalidate every
click timing already recorded. **Break 18 guards this. Leave it alone.**

Starting values: 0, 4, 10, 21 days.

### Clicks are tracked PER STEP

⚠️ **TODAY'S CODE IS PER-LEAD, AND THIS SECTION IS THE CHANGE REQUIRED.**
`leads.click_token` is a single column and `clicks.token_for()` generates ONE
token per lead, reused for every send. That was the right call for a single
manual email and it is not settled — building the drip means moving the token
onto the send, which is a schema change, not a tweak. Decided 2026-09-09: the
per-step design below wins, because step attribution answers a question
time-since-send cannot. Parked with the rest of the drip.

Each step's sample link carries its own token, so a click attributes to the
step that produced it. If step 1 pulls every click, the follow-ups are noise;
if step 3 does, the opener needs rewriting. A per-lead count cannot tell those
apart. Surface clicks-by-step in the funnel.

**A click makes a lead `engaged` and flags it. It does NOT stop the drip** - a
click is interest, not an answer, and stopping on one would silence the
sequence exactly when it is working. **Only a reply stops it.**

### Editing a sequence with leads mid-flight

**RULE: a step already sent is never re-sent and never re-dated.**

* editing copy → only leads who have not reached that step
* changing a delay → reschedules only leads who have not reached it
* deleting a step → those who got it keep the record; others skip it
* inserting a step → leads already past that position do NOT go backwards

Sends are recorded **per step per lead**, so "has this lead had step 3" is a
fact in the database, not inferred from a count. Without that, deleting a step
silently renumbers what everyone received.

### What stops the drip

Any reply (including "not interested" and out-of-office), bounce → `bad_email`,
unsubscribe → archive + email do-not-send, demo booked, last step sent →
archive (`no_reply`), or Sean stopping it by hand. **Every stop records WHY and
alerts.**

⚠️ **REPLY DETECTION IS A HARD GATE.** Nothing auto-sends if detection is
unavailable - fail closed, exactly like `assert_dialable`. **Build reply ingest
first; the drip does not send without it.**

### Auto-send exclusions apply to EVERY step

The five that already exist (unconfirmed email, no contact name, domain neither
matching nor free-mail, `needs_human`, `replied_at` set) apply to every step,
not just email 1. Each keeps its own break definition.

## Pausing

**Pausing a call campaign pauses DIALING ONLY.** A lead already in a drip is in
a different campaign and keeps sending. Stopping a drip is its own control on
the drip campaign - never a side effect of switching which call campaign dials.

## Per-campaign retry gaps

Move the hardcoded ladders onto the call campaign row, editable like cap and
spacing, per outcome, with the current values as defaults:

```
busy       15m → 1h → 4h → next day     (shortest: a busy signal means a human
no answer  2h → 8h → 1d → 3d             is there - the best signal in the list)
voicemail  next day
```

## "Why is it here", and tags

One line at the top of lead detail, assembled from existing state - no new data:

> Called 3 times, reached a human once. Bob Smith gave his email Sep 8.
> Emailed Sep 8, clicked the sample twice. Waiting on a reply — email 2 due
> Sep 12.

Archived leads say so and why, including when they return.

**Tags**: free text, many per lead, applied by hand, filterable. Not a fixed
vocabulary.

## Statuses — the full set

```
CALL      new · queued · dialing · callback · no_answer · completed
          human_review · paused · max_attempts · failed
EMAIL     emailed · engaged · demo_booked · bad_email
TERMINAL  won · lost · archived · dnc
```

All manually settable except `dnc` and `dialing`, for the reasons already
built in. Every hand change lands on the timeline.

## BUILD ORDER

```
1. reply ingest              the gate on everything below
2. statuses                  ✅ DONE - the seven, manual dropdown, filterable
3. campaign type             call | drip, scope the running index
4. archive                   status, reason, returns_at, nightly sweep
5. the drip                  drip_steps, the editor, per-step send record,
                             the sender loop
6. per-campaign retry gaps
7. "why is it here" + tags
```

**Nothing in 5 ships before 1 is green with its own break definitions.**

## Standing rules

* **Fail closed** on every guard. An error means do not send.
* **Every exclusion gets its own break definition**, verified red on its own
  named test.
* **The app's suppression state is what the sender checks**, never Brevo's.
* **An email unsubscribe suppresses EMAIL ONLY.** Someone who does not want our
  emails has not given up the right to be phoned about a case they asked about.
  A prose "take me off your list" in a reply is broader - both lists, plus a
  person looking at it.
* **No open tracking.** Clicks and replies only.
