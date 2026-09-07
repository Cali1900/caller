# caller

Outbound calling system that dials personal-injury law firms, talks to the
front desk, and comes back with **who handles intake, their email, and when to
reach them.** It does not pitch and it does not book demos.

Voice is Retell AI. We own the dialer, the state machine, the webhook handler
and the data.

**Separate repo, separate compose project, eventually a separate droplet.** It
is not part of CounselorAI and does not share `lf-postgres` — see
BUILD_BRIEF.md for the three load-bearing reasons.

## Status: PHASE 3 COMPLETE — scoring and the digest

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
