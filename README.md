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

Both live in the `settings` table and the `dialing_windows` table, edited on
`/campaign`. **Env is only the first-boot seed** — changing an env var needs a
container recreate, which is a deploy step.

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

## ⚠️ A guard is only tested if the test ISOLATES it

Three times now a guard has been removed and the suite stayed green, because
some *other* filter already excluded the same row. A test that passes for the
wrong reason is worse than no test: it reports coverage that does not exist.

| phase | guard | what masked it |
|---|---|---|
| 2 | TCPA legal window (08:00–20:30) | the operator preference window (09:00–17:00) is strictly **narrower**, so the legal window never bound. Only observable once the preference is widened. |
| 2 | campaign started/not-paused SQL gate | `assert_campaign_running()` already refused at dial time, so `run_once()` returned 0 either way. Only observable at **selection**, where the SQL gate stops the lead being *claimed*. |
| 5 | `REPLIED_GUARD` (`replied_at IS NULL`) | the test used `record_reply()`, which also sets `status='completed'` — and the status filter already excluded it. Only observable when `replied_at` is set while status stays **dialable**. |

**The rule:** to test a guard, construct a row that every *other* filter would
let through, so the guard under test is the only thing that can exclude it.
Then remove the guard and watch that specific test go red.

`scripts/break_pass.sh` enforces the second half — it requires the **named
expected test** to fail, not merely that something did. All three cases above
were found by that check, not by review.

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
| `/campaign` | today's run — enrol / start / pause / resume / rollover / cap, CSV upload |
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
