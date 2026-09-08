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


# Which Retell agent runs which stage. L2 is deliberately absent: at L2 we OWE
# them an email and nothing dials.
STAGE_AGENTS = {
    'L1': ('AGENT_L1', 'AGENT_L1_VERSION'),
    'L3': ('AGENT_L3', 'AGENT_L3_VERSION'),
}


# The live version per stage is an OPERATOR SETTING, not an env value.
# v8 went live as a side effect of an unrelated dashboard edit; the point of
# reading it from settings is that editing a draft in Retell can no longer
# change what actually dials.
STAGE_VERSION_SETTING = {'L1': 'agent_l1_version', 'L3': 'agent_l3_version'}


def agent_for(cfg, stage: str, campaign=None):
    """
    (agent_id, version) for a stage. Raises for a stage that must not dial.

    Version comes from settings, falling back to the env seed if the settings
    table cannot be read - falling back to a KNOWN version is safer than
    refusing to dial or guessing at 'latest'.
    """
    try:
        aid, ver = STAGE_AGENTS[stage]
    except KeyError:
        raise ValueError(f'no dialing agent for stage {stage!r} - refusing')
    version = getattr(cfg, ver)
    key = STAGE_VERSION_SETTING.get(stage)
    if key:
        try:
            # The prompt version is a PROPERTY OF THE RUNNING CAMPAIGN.
            # Falling back to the env seed beats guessing at 'latest'.
            from api import campaigns as _campaigns
            camp = campaign or _campaigns.running()
            if camp and camp.get(key) is not None:
                version = camp[key]
        except Exception:
            pass
    return getattr(cfg, aid), version


def dynamic_vars(lead) -> dict:
    """
    Every {{variable}} any prompt references, as STRINGS.

    Retell substitutes these into the prompt; an unsupplied variable is left
    in the text, so the agent reads "{{company}}" aloud or reasons about a
    literal placeholder. The L1 prompt has referenced {{company}} since it was
    written and we never passed it - caught 2026-09-07. Build the whole set
    here, once, so a prompt edit cannot silently outrun the dialer.
    """
    name = (lead.get('dm_name') or '').strip()
    title = (lead.get('dm_title') or '').strip()
    emailed = lead.get('emailed_at')
    return {
        'company': (lead.get('company') or 'the firm').strip(),
        'dm_name': name,
        'dm_title': title,
        # renders as ", Intake Manager" or "" so the prompt reads naturally
        'dm_title_suffix': f', {title}' if title else '',
        'dm_email': (lead.get('dm_email') or '').strip(),
        'emailed_when': emailed.strftime('%A') if emailed else 'a few days ago',
        'callback_person': (lead.get('callback_person') or '').strip(),
        'lead_id': str(lead.get('lead_id') or ''),
    }


def create_phone_call(cfg, to_number: str, lead, dynamic=None):
    """
    Place an outbound call, using the agent for the LEAD'S STAGE.

    The caller MUST have passed the dial guard before reaching this function.
    Nothing in here re-checks the allowlist - that is the dialer's job, and
    keeping it there means there is exactly one place the guard can be
    bypassed rather than two.
    """
    stage = lead.get('stage') or 'L1'
    agent_id, agent_version = agent_for(cfg, stage, lead.get('campaign'))
    lead_id = lead.get('lead_id')
    resp = _client(cfg).call.create_phone_call(
        from_number=cfg.RETELL_FROM_NUMBER,
        to_number=to_number,
        override_agent_id=agent_id,
        # PIN THE VERSION. Retell can report more than one agent version as
        # published at once, which leaves "which prompt did this call run"
        # ambiguous - and an unpublished version carrying the webhook_url
        # means a call happens and NO events are ever delivered, which reads
        # as a broken drain rather than a missing webhook. Naming the version
        # removes the guess, and calls.prompt_version records what ran.
        override_agent_version=agent_version,
        # metadata is how the drain finds the lead. Matching on
        # leads.last_call_id alone breaks the moment a lead is re-dialed
        # before its previous webhooks have drained.
        # agent_version in metadata so the drain can stamp EXACTLY what ran.
        metadata={'lead_id': str(lead_id), 'stage': stage,
                  'agent_version': agent_version},
        retell_llm_dynamic_variables=dynamic if dynamic is not None
                                     else dynamic_vars(lead),
    )
    return resp


def create_web_call(cfg, lead, dynamic=None):
    """Browser call. No telephony, no risk - this is rollout step 1."""
    if not isinstance(lead, dict):          # tolerate a bare lead_id
        lead = {'lead_id': lead, 'stage': 'L1'}
    agent_id, agent_version = agent_for(cfg, lead.get('stage') or 'L1')
    return _client(cfg).call.create_web_call(
        agent_id=agent_id,
        agent_version=agent_version,
        metadata={'lead_id': str(lead.get('lead_id')), 'stage': lead.get('stage')},
        retell_llm_dynamic_variables=dynamic if dynamic is not None
                                     else dynamic_vars(lead),
    )


def get_call(cfg, call_id: str):
    return _client(cfg).call.retrieve(call_id)
