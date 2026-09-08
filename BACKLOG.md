
## B-batch-review — batch review tool (queued 2026-09-08)

`./review.sh --last 100` | `--version v9` | `--from/--to`. ONE analysis over
the whole set, not per call and not per day. Markdown to a file, no UI.

Must report:
  - where calls die, RANKED, with real quotes
  - what the agent does wrong most, from agent_deductions
  - what receptionists actually say, CLUSTERED BY THEME (not counted by
    category - the category counts are what the daily digest already does)
  - which objections we handle and which we fold on
  - what to change in the prompt, WITH THE EVIDENCE FOR IT
  - if two prompt versions are in the range, compare them

Sean: "The daily digest tells me what happened yesterday; this tells me what
the pattern is."

LOW PRIORITY UNTIL REAL VOLUME - 5 calls to one phone is not a sample. Build
after the campaign model change lands.

## B-objection-scoring — objections in the scorer + digest (queued 2026-09-08)

Outcome and agent conduct are scored; the thing the prompt was REWRITTEN for
is not. New columns on `call_scores`:

  objections_raised   text[]  - not_interested, dont_give_that_out,
                                use_software, send_email, not_available,
                                whats_this_about, are_you_ai, how_much,
                                remove_me, other
  objection_handling  int 0-10 - acknowledge, ONE short answer, steer back.
                                Argue / over-explain / fold = low.
  angles_tried        int      - DIFFERENT approaches before giving up.
                                Repeating the same ask does not count.
  folded_too_early    bool     - accepted a reflex brush-off as final
  pushed_too_far      bool     - kept going after a REAL no. "Take me off
                                your list" ending the call is CORRECT;
                                working that one is not.

BOTH DIRECTIONS MATTER. Sean: "I rewrote persistence so it works 3 angles
instead of stopping at 2 - I need to know whether it actually does, and
whether it now overshoots."

Digest: objection breakdown - which objections come up most, and our handling
score against each, so it is obvious which one needs better copy.

NOTE: objection_handling is a THIRD score. It is never averaged with the
outcome or agent scores - same rule as those two.

## B-scorer-model-cost — downgrade the scorer, but measure first (queued 2026-09-08)

Scorer runs claude-opus-5 at ~1.6c/call. Reading a 45-second transcript and
filling structured fields probably does not need it. INTENT IS TO DOWNGRADE -
the measurement decides whether that is safe, it is not a formality.

MEASURE, DO NOT GUESS. Re-score the same 7 calls on a cheaper model, compare
FIELD BY FIELD against the Opus scores. Agreement -> switch. Keep the
comparison on file either way.

THE FIELD TO WATCH IS `agent_deductions`. That is where a weaker model is
likeliest to be lazy and simply not notice things. A missed deduction does not
look like an error - it looks like a clean call - so a single accuracy number
would hide exactly the failure that matters. Report deductions separately:
what Opus caught that the cheap model did not, quoted.

Sean: "I'd rather pay than have scores I can't trust."

`call_scores.model` already records which model ran, so both runs stay
attributable and the comparison is reproducible from the table.

SEQUENCING NOTE: B-objection-scoring adds five fields, and judging whether an
agent "folded too early" is a harder call than filling in an outcome score.
Run this comparison against the FINAL field set, or it measures a scorer that
no longer exists.

## B-demands-volume — volume question on L1 + column (queued 2026-09-08)

Sean writes the prompt edit. BUILD THE FIELD, THE COLUMN AND THE UI.

WHEN (prompt-side, his edit): only after we have BOTH the name and the email.
Never before, never instead. No email -> do not ask; the primary objective
does not change. If she does not know or will not say, DROP IT IMMEDIATELY
and end the call normally. It must not become a second thing the agent pushes.

Extraction field `demands_per_month`: TEXT, in her own words - "about 20",
"maybe 5 or 6", "no idea". Do not force a number.

Columns on `leads`:
  demands_per_month_raw  text - the verbatim, always
  demands_per_month      int  - parsed ONLY where a number is clearly stated
NULL MEANS SHE DID NOT SAY. It does not mean zero, and nothing may treat it
as zero - not a sort default, not an average, not a filter.

UI: show on the leads list and lead detail; filterable and sortable, because
the point is to work the L2 email queue by firm volume.

Same absence rule as the other extraction fields: a call where no conversation
happened has the field ABSENT, not null-with-meaning.

# ═══════════════════════════════════════════════════════════════════════
# EMAIL DRIP  (specified 2026-09-08)
# ═══════════════════════════════════════════════════════════════════════

**L3 CALLING IS DESCOPED ENTIRELY. A follow-up is email, not a call.**

This resolves the open question from the L3 unwiring: a campaign does NOT need
to point at a Retell agent, because there is no follow-up call. `agent_l3_version`
and `AGENT_L3` can come out of the app once the drip lands.

## The shape

Every call campaign has ONE email campaign attached, **1:1**. C1's email
campaign is C1's — its own copy, its own intervals.

Sending email 1 by hand puts the lead into the drip; it runs automatically
from there.

| | | |
|---|---|---|
| email 1 | **MANUAL** — Sean writes, reviews, sends | sets `emailed_at` |
| email 2 | auto | +4 days |
| email 3 | auto | +10 days |
| email 4 | auto | +21 days |
| | then stop | |

**Intervals are measured FROM EMAIL 1, never from the previous email**, so the
schedule cannot drift. 4 / 10 / 21 are the starting values and are adjustable
per campaign. Each email has its own editable copy, in the same editor as
email 1 (subject + body, both gatekeeper variants, live preview).

`emailed_at` is therefore load-bearing twice over: it anchors the click timings
AND the entire drip schedule. It is already write-once by design — marking
twice is a no-op — and that must not change.

## What stops the drip

* **ANY reply** — including "not interested" and an out-of-office
* **bounce** — dead address: stop and flag
* **DNC / unsubscribe**
* **demo booked**
* **max emails reached**
* **Sean stops it by hand**

Every stop ALERTS and records WHY.

⚠️ **REPLY DETECTION IS A HARD GATE. Nothing auto-sends until it works.**
Four emails to someone who already answered is the worst thing this system can
do, and it is the most common way these systems fail. This is the dial-guard of
the email side: it gets a break definition, and the auto-sender refuses to run
if detection is unavailable — fail closed, like `assert_dialable`.

## Statuses

Add: `emailed` (email 1 sent, drip running), `engaged` (clicked or replied —
HOT), `demo_booked`, `won`, `lost` (**explicit no**), and `lost_no_response`
(**the drip finished with nothing**).

⚠️ **REFINED 2026-09-08: `lost` and `lost_no_response` are separate.** The
first spec folded "drip finished with nothing" into `lost`. They are not the
same event and must not share a status: an explicit no is a decision someone
made, and silence is an absence of one. Only one of those is worth revisiting
in ninety days, and a single `lost` bucket cannot tell them apart afterwards.

**EVERY STATUS IS MANUALLY CHANGEABLE.** A dropdown on lead detail sets any of
them at any time; the change goes to the timeline with WHO and WHEN. The system
sets statuses automatically and Sean overrules it.

**The qualified line is ENGAGED.** That is where he acts.

## A bounce — `bad_email`, which is RECOVERABLE

    bad_email   email 1 bounced, drip stopped, needs a person

**Not `lost`.** A bounce means we probably have the right firm and the wrong
address. That is recoverable; an explicit no is not.

When it fires:

* the drip stops immediately (already in the stop list)
* the lead goes to `bad_email` and appears in **"needs you"**
* **the bounced address is recorded** so it is visible what was tried

### The restart is a BUTTON, never a side effect

    1. bounce -> status bad_email, drip stops, shows in "need you"
    2. Sean opens the lead, sees the bounced address and why
    3. Sean corrects the email
    4. Sean clicks RESTART DRIP  <- an explicit button

⚠️ **CORRECTED 2026-09-08.** This section previously read "correcting the
address restarts the drip", which is edit-triggered — **editing a field would
have caused an email to send.** That is the same failure as adding leads
starting a dial, and it was written without noticing. Sean caught it.

**Step 4 must NOT be triggered by the status change or by saving the email.
Changing a dropdown must never cause an email to send.**

The button:

* appears ONLY on a `bad_email` lead whose address has actually been corrected
* fires the audited `emailed_at` reset (see the collision note below)
* re-drafts email 1 and returns the lead to the NORMAL flow — manual or auto
  per the campaign switch, not a special path that bypasses it
* **starts the drip from zero, not email 2** — it never got a first touch
* writes to the timeline: who, when, old address, new address

**If the corrected address also fails validation, REFUSE and say why** rather
than restarting into a second bounce. The restart runs the same
`email_validation.check()` the auto-send exclusions use — one implementation,
so a rule can never hold on one path and pass on the other.

⚠️ **This collides with the write-once `emailed_at` rule, and the collision is
the interesting part.** `mark_emailed()` is a deliberate no-op when
`emailed_at` is already set, because restamping would silently change every
"N minutes after send" already recorded against that lead.

On a bounce that reasoning does not apply: **nothing was delivered, so there
are no click timings to invalidate.** So the restart CLEARS `emailed_at` as an
explicit, audited reset — a named operation that writes to the timeline — and
`mark_emailed()` keeps its write-once guard untouched. Loosening the guard to
allow the restart would trade a rare recoverable case against the property that
protects every normal one.

### A hard bounce goes on the DO-NOT-SEND list, not suppression

**Three separate exclusion lists now, and they must stay separate:**

| list | keyed on | why | who can lift it |
|---|---|---|---|
| suppression | phone / firm | **compliance**, damages behind it | nobody |
| cooled (`lost_no_response`) | lead | business rule | Sean, by hand |
| do-not-send | **the EMAIL ADDRESS** | the mailbox is dead | a new address just works |

The do-not-send list is keyed on the ADDRESS, not the lead and not the firm. A
dead mailbox is just dead: **if a good address for the same firm turns up
later, it must still send.** Equally, if the same dead address appears on
another lead, that must not send either.

Sean: *"Suppression is compliance. A dead mailbox is just dead."*

## After email 4 with no response — COOLED, not deleted

`status = lost_no_response`, the drip stops, and **nothing automated ever
contacts them again** — not the dialer, not a second drip, not a future
sequencer.

**Do not delete them.** We hold the name, a verified email, the firm and its
demand volume. That is worth keeping even when the attempt failed — and it is
exactly the data a cold re-approach would otherwise have to re-earn by calling
the front desk again.

### ⚠️ Zero opens AND zero bounces → flag "possibly bad address"

A lead reaching `lost_no_response` having never bounced and never registered a
single open is **different from one that was opened and ignored**. The first
might not exist; the second is a real person who is not interested. Flag it as
*possibly bad address* rather than filing it as plain silence.

Brevo reports opens on emails 2-4 for free. **Weak, not conclusive** - but for
the case where nothing else tells us anything, it is the only signal available.

⚠️ **This LOOKS like it contradicts "never trigger anything from an open", and
whoever builds it will hit that head-on. It does not, and the distinction is
the whole point:**

* an open **never** creates `engaged`, never scores, never stops the drip,
  never alerts — a pre-loaded pixel must not be able to mean interest
* the ABSENCE of every open across four sends, combined with zero bounces, is
  a diagnostic label applied ONCE at a terminal state

Apple pre-loading inflates opens; it never invents zero. So a zero is the one
reading of that data Apple cannot manufacture — which is exactly why the
absence is usable when the presence is not.

⚠️ **`test_no_open_tracking_anywhere` will fail when this lands, correctly.**
It currently forbids any open tracking in `api/`. The rule it should encode is
narrower: **we never embed our own tracking pixel.** Consuming an open figure
Brevo reports from its own pixel is a different thing. Narrow that test when
the drip lands — deliberately, with this note as the reason — rather than
deleting it.

Add a **"cooled" filter** on the leads list to pull them up later. After ~90
days Sean may reach out BY HAND with an actual reason — a new feature, a case
study. **That is him writing one email, not a second drip.**

⚠️ This permanent-exclusion property gets a break definition, and it is a
DIFFERENT guard from suppression. Suppression is a compliance stop (they said
remove me). This is a business stop: still contactable by a person, never by
the machine. Two rules that happen to both mean "do not auto-contact" will
drift apart the moment one of them is edited, so they get separate guards and
separate tests.

## Engagement scoring

Per lead, visible in the list and **sortable**:

| signal | weight |
|---|---|
| replied | strongest |
| clicked | strong |
| opened | **ZERO** |

**Opens are recorded and never scored.** Apple Mail Privacy Protection
pre-loads pixels, so an open fires whether or not a human looked. Store it,
show it greyed as context, never score on it, never trigger anything from it.
An open must not create `engaged`, must not stop the drip, must not alert.

## Build order

1. **click tracking** (item 7, in progress)
2. **reply detection** — the guard, before ANY auto-send
3. **bounce handling**
4. **the drip itself**
5. **statuses + manual override**
6. **scoring**

## B-page-visits — page-visit tracking on counselorai.io (queued 2026-09-08)

FOLLOW-ON TO CLICK TRACKING, not part of it. **The click is what identifies
him**, so page tracking without it is meaningless - build it after clicks work.

Once Bob clicks a tracked link and lands on counselorai.io, set a cookie and
record what else he looks at: pages, time on page, return visits, all
attributed to him.

Sean: *"read the sample for four minutes, came back Thursday, looked at
pricing"* tells him to call. That is a real buying signal in a way opens never
were - because a human chose each of those actions.

Someone who types counselorai.io directly **stays anonymous**, and that is
fine. Only a click from an email we sent identifies anyone.

### ⚠️ This is NOT contained in this repo

The redirect can carry a visitor token (`.../#letter?v=<token>`), but the
COOKIE MUST BE SET BY counselorai.io ITSELF and the page events beaconed back.
That means a change to the marketing site, not just to caller. A third-party
cookie set by this app on that domain is blocked by Safari ITP and by Chrome's
third-party cookie restrictions; a FIRST-PARTY cookie set by counselorai.io
works everywhere. Scope the marketing-site work before estimating this.

### ⚠️ Legal, and it is a real requirement

We would be tracking NAMED INDIVIDUALS across pages, not anonymous traffic.

* **A privacy policy line is required** and must be live BEFORE the first
  tracked visit, not after.
* **CCPA/CPRA applies to any California resident** among them - and these are
  California PI firms, so assume most of them. That brings notice-at-collection,
  the right to know, the right to delete, and an opt-out path.
* Deletion has to actually work: a "delete my data" request must reach
  `email_clicks`, the visit log, and the cookie - so build the deletion path
  WITH the feature, not after someone asks.

Sean raised the privacy-policy requirement himself; this records it so it
cannot be forgotten between now and the build.

## B-funnel — funnel view (queued 2026-09-08)

The numbers exist across Today and the leads list; the SHAPE does not.

    in queue         1,000
    dialed             340   34%
    reached a human     85   25% of dialed
    gave a name         42   49% of reached
    gave an email       28   67% of names
    emailed             28
    clicked              6   21% of emailed
    replied              3   11% of emailed
    demo booked          1

Count, percentage, and THE DROP at each step. **Highlight the worst step** -
that is where the problem is.

Two views: all time, and a date range (compare weeks). Filterable by campaign,
and by prompt version if cheap - the question being "did a script change move a
specific step". Today page or its own tab.

Sean: *"the funnel is how I'll know whether the problem is the list, the script,
or the email."* **Wanted BEFORE scaling past 50 real calls.**

## B-pipeline-forecast — weighted pipeline (queued 2026-09-08)

Uses `demands_per_month` (B-demands-volume).

    monthly value  = demands_per_month x price_per_demand
    weighted value = monthly value x P(stage)

Stage probabilities configurable per campaign; starting guesses:
`demo_booked` 40%, `engaged` 15%, `emailed` 3%, everything else 0%.

Show total unweighted, total weighted, both broken down by stage, and the lead
count at each stage. On the leads list: **monthly value as a sortable column**,
so the big firms can be worked first.

* `price_per_demand` is a CAMPAIGN setting, default **$150**
* **`demands_per_month` is a receptionist's ESTIMATE - label it as such on
  every screen it appears.** Directional, not a contract. A forecast built on
  it must never be presented as a number anyone can bank.

After the funnel, same reason: wanted before scaling past 50 calls.

## B-lead-edit-all — edit any lead field by hand (queued 2026-09-08)

Editable on lead detail: company, phone, timezone, city, state, segment,
contact name, title, email, confirmed, `demands_per_month`, **status
(dropdown, any value - the operator overrules the system)**, stage,
`next_attempt_at` (reschedule a call by hand), callback person, and
`emailed_at` (for mail sent outside the app).

**Every edit writes to the timeline: what changed, from what to what, and that
a PERSON did it, not the system.** A hand correction must stay distinguishable
from an agent capture - that is already the rule for the email; keep it for
everything.

Validation STAYS. Phone must be E.164, timezone must pass the trigger, status
must be a real value. **Refuse bad input rather than accepting it quietly.**

⚠️ **NOTHING ON THE `calls` TABLE IS EDITABLE.** Transcripts, scores and
timestamps are the record of what happened. Sean: *"If I could edit those I
couldn't trust them."* This deserves a break definition, not just a habit.

## B-csv-website — website column on CSV upload (queued 2026-09-08)

Optional column `website` on the upload. Small on its own - but it is what
B-auto-send-email-1 needs to compare an email domain against the firm's site,
so build it first or that check cannot exist.

## B-auto-send-email-1 — manual/auto switch for email 1 (queued 2026-09-08)

Per campaign, **same pattern as the dial switch: defaults to the safe side and
is flipped deliberately.**

* **MANUAL (default)** - draft generated, waits for Sean
* **AUTO** - sends N minutes after the call, N configurable, default **15**

⚠️ **EVEN WITH AUTO ON, these go to manual review instead of sending:**

* `dm_email_confirmed` is false
* no contact name captured
* **the email domain does not match the firm's website** (needs B-csv-website)
* anything flagged `needs_human`

Sean: *"auto handles the clean ones and I only look at the questionable ones -
my attention goes where it's actually worth something."*

Everything in the drip spec still stands: stops on any reply, bounce, DNC or
demo booked.

⚠️ **THIS IS THE FIRST THING THAT SENDS MAIL WITHOUT A HUMAN.**

**FAIL CLOSED, like `assert_dialable`: if the exclusion check cannot run, do
not send.** Not "log and continue" - refuse.

**EVERY exclusion gets its own break definition.** A silently-stopped exclusion
is an email to the wrong person, not a red test. The full set:

| # | exclusion | break |
|---|---|---|
| 1 | `dm_email_confirmed` is false | required |
| 2 | no contact name captured | required |
| 3 | domain neither matches the site nor is known free-mail | required |
| 4 | `needs_human` flagged | required |
| 5 | `replied_at` already set (a prior campaign) | required |

Plus the drip stops, each of which also gets one: any reply, bounce, DNC,
demo booked.

⚠️ **Exclusion 5 is mine, not Sean's list** - a lead can carry `replied_at`
from earlier work, and "they already answered us once" is exactly the case
auto-send must not walk into.

**Reply detection and email 1:** the drip's hard gate is about emails 2-4,
where a reply must stop the sequence. Email 1 is first contact - there is
nothing yet to reply TO - so auto-sending it does not strictly require
detection to be live. It does require exclusion 5. **Emails 2-4 remain blocked
on working reply detection**, no exceptions.

It also needs the verified sender, which is now in place (both addresses
verified in Brevo).

## B-email-validation — validate before sending (queued 2026-09-08)

Depends on **B-csv-website** (the website field) and gates
**B-auto-send-email-1**.

### Free checks, on every capture

* format
* domain resolves and has **MX records**
* **domain matches the firm's website** ← the one that matters
* not disposable, not a role address (`info@`, `admin@`, `office@`, ...)

Any failure → **manual review, never auto-send**, with THE REASON shown on the
lead. Store the result so it is visible why something was held.

Sean: *"An agent confirming bob@gmial.com because the receptionist said yes is
exactly what I can't catch by reading fast."* A one-character typo in a
plausible domain is invisible at reading speed and obvious to an MX lookup.

### ✅ SETTLED 2026-09-08 — the domain check has THREE outcomes

| outcome | what happens |
|---|---|
| matches the firm's website | **auto-send** |
| known free-mail provider | **auto-send**, flagged quietly on the lead |
| neither | **HOLD for review, loud** |

A strict match is wrong: plenty of small PI firms genuinely use gmail, and
holding all of them would put most of the queue in review, which defeats
auto-send. `bob@gmial.com` fails the MX check outright as well, so the
gmial.com shape is caught twice.

### Paid verification - if cheap to wire in

ZeroBounce or NeverBounce, ~$0.005/email, ~$5/month at this volume. SMTP-probes
the mailbox without sending.

⚠️ **It is a filter, not a guarantee.** Many corporate servers accept-all and
bounce later. **Bounce handling in the drip is required either way** and must
not be treated as optional because verification is in place - a "valid" verdict
on an accept-all domain means nothing.


# ═══════════════════════════════════════════════════════════════════════
# BUILD ORDER — settled 2026-09-08
# ═══════════════════════════════════════════════════════════════════════

1. **auto-send switch + email validation** (one piece of work — the validation
   IS the exclusion set)
2. **full lead editing + CSV website column**
3. **funnel**
4. **pipeline forecast**

**NOTHING AUTO-SENDS UNTIL THE BREAKS ARE GREEN.**

# ═══════════════════════════════════════════════════════════════════════
# B-ingest — SIGNALS ARRIVING WHERE THE APP CANNOT SEE THEM
# (written by the website coder, 2026-09-08)
# ═══════════════════════════════════════════════════════════════════════

Both items are ONE problem: a recipient acts, a third-party system records it,
and the app's view of that person goes silently stale. Same fix shape — an
ingest path back into the app — so scope them together.

## 1. List-Unsubscribe unsubscribes are invisible

A recipient clicks Unsubscribe in Gmail. Gmail calls the List-Unsubscribe
endpoint, which is **Brevo's**. Brevo records it. The app is never told, so it
still believes that person is contactable.

Two consequences: we keep mailing someone who opted out through the mechanism
their own mail client offers, and repeatedly mailing unsubscribers is exactly
what costs sending reputation — quietly.

**Fix:** consume Brevo's unsubscribe webhook, or reconcile against its list on
a schedule, and write to the app's own suppression state.

⚠️ **THE APP'S LIST MUST BE WHAT THE SENDER CHECKS, not Brevo's.** Otherwise
the gap returns the next time the ESP changes.

## 2. Replies are invisible

A reply lands in the mailbox. The app does not read it. That is both the
strongest positive signal the channel produces, and the place a prose opt-out
arrives — *"take me off your list"* carries the same obligation as the button.

**Fix:** ingest the reply mailbox (IMAP poll, or ESP inbound parse), attach to
the lead, halt sends pending review. **Safe default is halt on ANY reply** and
let a person decide.

## How this joins up with what already exists

* `stages.record_reply()` is ALREADY the writer, deliberately built as a seam
  and deliberately unwired. This is the missing *caller*, not a new concept —
  and `test_reply_detection_is_not_wired_up_yet` fails the day it lands, which
  is the signal to update the "replied is inert" copy.
* Reply detection is the **hard gate on the drip** (see the drip spec): emails
  2–4 stay blocked until it works. Auto-send of email 1 does not depend on it,
  because there is nothing yet to reply to — but exclusion 5 (`replied_at`
  already set) does.
* The **do-not-send list is keyed on the EMAIL ADDRESS** and is separate from
  phone suppression by design — see the three-list table in `HANDOFF.md`.
  A List-Unsubscribe opt-out belongs on the email list, not the phone one:
  they gave up email contact, not the right to be phoned about a case they
  asked about. Keeping them separate is the whole point.
* A **prose opt-out in a reply is different again** — "take me off your list"
  in an email body is a request to stop contacting them, and the safe reading
  is BOTH lists plus a person looking at it.

Confirmed on this box: suppression already exists app-side and survived the
wipe (3 rows, md5 020b7227a88e77476da537cdb9f7f447). So this is an ingest into
an existing concept.
