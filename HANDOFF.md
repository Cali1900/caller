# CALLER — HANDOFF

Outbound calling for CounselorAI. Dials personal-injury firms through Retell,
scores every call, drafts a follow-up email that a person sends by hand.

**Read this for state. Do not trust anyone's memory, including mine.**
Last updated 2026-09-08.

---

## Where it stands right now

| | |
|---|---|
| Repo | `git@github.com:Cali1900/caller.git`, branch `main`, all work pushed |
| Last migration | `20260908_013_drop_legacy_campaign_tables.sql` |
| Tests | 253 passed, 1 skipped |
| Break pass | 29 definitions. Last **full** clean run: `BREAK PASS: OK`, 29/29 red on their own named test — **before** the draft/follow-up work below |
| Campaigns | `C1` and `C2`, both **stopped**. Nothing dials while nothing runs |
| Data | 2 leads (2 queued), 7 calls, 6 scores, 1 draft, 3 suppressed |

**NOTHING IS DIALING.** The pause defaults to stopped and both campaigns are
stopped. Starting one is a deliberate act on `/campaigns`.

### ⚠️ Open before this is "done"

1. **The break pass has not been re-run since the follow-up work.** Two new
   guards have no break definition: `retarget()` refusing to touch a SENT
   draft, and the `_EMAIL_STATE` precedence. Until a break proves each turns
   its own test red, treat them as untested — that is this project's standing
   rule and it has caught eleven masked guards so far.
2. Items 5, 6 and 7 below are specified and not built.

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

**One test run at a time.** Three concurrent runs against one test database
produced hours of results that looked like code regressions — breaks
reappearing after restore, deadlocked TRUNCATEs, failures moving between runs.
There is now an exclusive `flock` and a refusal to start while another
`test.sh` is in flight. If results look impossible, check for stray processes
before you debug the code.

**A guard is only tested if the test ISOLATES it** — see the table in
`README.md`. Eleven instances so far.

**A test may not configure what production ignores.**
`tests/test_no_dead_config.py` fails when a test writes a settings key or a
table nothing consumes. Nine of the eleven masked guards were exactly that:
the test configured one place, the dialer read another, and matching seeds hid
it. Its allowlist is a review decision, not a convenience.

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
  stages.py      L1 -> L2 -> L3. mark_emailed() is the seam for automation
  web.py         the CRM. SSH tunnel only
  worker.py      the loop. next_gap() is module level so tests call the real one
migrations/      forward-only, applied by scripts/migrate.sh
scripts/breaks/  29 break definitions, one per guard
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

## Next, in order

These are specified and **not built**.

**5 — Unwire L3 from the app.** A campaign already is a named config with its
own prompt and leads, so a follow-up is just another campaign that Sean assigns
leads to. Campaign config shows ONE prompt version, not L1 and L3. Leads that
capture name + email stop and wait. **Keep the L3 agent in Retell**, just
unwire it. `stages.mark_emailed()` should stop scheduling an L3 dial, and
`STAGE_DIALABLE` should drop `'L3'`.

**6 — Prompt picker auto-sync.** It shows v9 while Retell is at v15.
`listing()` already orders newest-first — the list is not wrong, it is never
refreshed, because `sync_versions()` only runs from a manual button. Sync
periodically (worker) and on page load when stale, degrading to the stale list
with a notice if Retell is slow. **Choosing which version is live stays
manual** — that is the property that stopped v8 going live as a side effect of
a dashboard edit.

**7 — Click tracking.** Sean sends by hand; the app tracks what happens after.
Draft's sample link is rewritten to `https://caller-dev.counselorai.io/c/{token}`;
the endpoint logs and 302s to `counselorai.io/#letter` so the recipient sees no
difference. Record `clicked_at`, **`minutes_since_sent`** (the number he wants),
`user_agent`, `ip`, and a click count. Show `clicked · 47m after send` on the
list, put it in the lead timeline beside the calls, and add `clicked` to the
filter. **Public endpoint on the webhook vhost: token only, no lead id, nothing
enumerable, and it must expose nothing but a redirect.**

Then the four in `BACKLOG.md`: `B-batch-review`, `B-objection-scoring`,
`B-scorer-model-cost`, `B-demands-volume`.

---

## Standing decisions

* **Nothing auto-sends.** Drafts are generated on capture and sent by hand.
  Anything after a reply, Sean sends from his own inbox.
* **Links, never attachments.** The sample demand is a link; there is a guard
  test pinning that it is never attached. No media library.
* **No open tracking** — Apple pre-loads pixels, the number is noise. Replies
  and clicks only.
* **Recordings stay in Retell.** `opt_in_signed_url` is true; public recording
  URLs are not defensible.
* Sean writes the prompts and the email copy himself.
* Suppression is backed up weekly to DO Spaces (`caller-backups-sfo3`), keep
  12. **Restore tested, not assumed** — see `BACKUP.md`.
