"""
Retell: create calls, and verify inbound webhook signatures.

SIGNATURE SCHEME - do not guess at this, it is not a plain HMAC of the body.
Retell sends:

    x-retell-signature: v=<unix_ms>,d=<64 hex chars>

and the signed message is **body + str(timestamp)**, HMAC-SHA256 with the API
key, plus a 5-minute replay window on the timestamp. A naive
`hmac(api_key, body)` comparison rejects every real webhook and looks like a
credentials problem.

We import Retell's own implementation rather than transcribing it, so a scheme
change arrives with a dependency bump instead of a silent 401 storm. Note it is
bound as an INSTANCE attribute on the client (`self.verify = verify`), not a
classmethod, so `Retell.verify(...)` does not exist - we import the function.
"""

import json

from retell import Retell
from retell.lib.webhook_auth import verify as _retell_verify

# Retell's docs re-serialize the parsed body compactly before verifying. When
# the raw bytes already match that, the first attempt succeeds; when a proxy
# has reformatted the JSON, the second does. Both are encodings of the same
# signed content and both still require the API key, so this widens nothing.
_COMPACT = (',', ':')


def verify_signature(raw_body: bytes, signature: str | None, api_key: str) -> bool:
    """True only for a genuine, in-window Retell signature."""
    if not signature:
        return False

    body_str = raw_body.decode('utf-8', errors='replace')
    if _retell_verify(body_str, api_key, signature):
        return True

    try:
        reserialized = json.dumps(
            json.loads(raw_body), separators=_COMPACT, ensure_ascii=False
        )
    except (ValueError, TypeError):
        return False
    return bool(_retell_verify(reserialized, api_key, signature))


def _client(cfg) -> Retell:
    return Retell(api_key=cfg.RETELL_API_KEY)


def create_phone_call(cfg, to_number: str, lead_id, dynamic_vars=None):
    """
    Place an outbound call. Returns the Retell call object.

    The caller MUST have passed the dial guard before reaching this function.
    Nothing in here re-checks the allowlist - that is the dialer's job, and
    keeping it there means there is exactly one place the guard can be
    bypassed rather than two.
    """
    resp = _client(cfg).call.create_phone_call(
        from_number=cfg.RETELL_FROM_NUMBER,
        to_number=to_number,
        override_agent_id=cfg.AGENT_L1,
        # metadata is how the drain finds the lead. Matching on
        # leads.last_call_id alone breaks the moment a lead is re-dialed
        # before its previous webhooks have drained.
        metadata={'lead_id': str(lead_id)},
        retell_llm_dynamic_variables=dynamic_vars or {},
    )
    return resp


def create_web_call(cfg, lead_id, dynamic_vars=None):
    """Browser call. No telephony, no risk - this is rollout step 1."""
    return _client(cfg).call.create_web_call(
        agent_id=cfg.AGENT_L1,
        metadata={'lead_id': str(lead_id)},
        retell_llm_dynamic_variables=dynamic_vars or {},
    )


def get_call(cfg, call_id: str):
    return _client(cfg).call.retrieve(call_id)
