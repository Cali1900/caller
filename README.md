# caller

Outbound calling system that dials personal-injury law firms, talks to the
front desk, and comes back with **who handles intake, their email, and when to
reach them.** It does not pitch and it does not book demos.

Voice is Retell AI. We own the dialer, the state machine, the webhook handler
and the data.

**Separate repo, separate compose project, eventually a separate droplet.** It
is not part of CounselorAI and does not share `lf-postgres` — see
BUILD_BRIEF.md for the three load-bearing reasons.

## Status: PHASE 1 built — still nothing can dial

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
