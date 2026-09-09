"""
AUTO-SEND FOR EMAIL 1 — the gate, not the sender.

THIS IS THE FIRST THING THAT MAILS WITHOUT A PERSON, so it is built the way
assert_dialable is: it decides NO by default and says yes only when every
check has actually run and actually passed.

FAILS CLOSED. If a check cannot run - DNS down, the campaign unreadable, an
exception nobody predicted - the answer is HOLD. Never "log and continue".
A wrong email cannot be recalled; a held one costs Sean thirty seconds.

THE EXCLUSIONS. Each has its own break definition, because a silently-stopped
exclusion is an email to the wrong person, not a red test:

  1  dm_email_confirmed is false
  2  no contact name captured
  3  domain neither matches the firm's website nor is known free-mail
  4  needs_human flagged
  5  replied_at already set - they answered us once already
  6  the ADDRESS is on the email do-not-send list
  7  the lead is archived - it is resting, nothing should reach it

5 is not on Sean's list; it is here because a lead can carry replied_at from
earlier work, and "they already answered" is exactly the case auto-send must
not walk into.

6 is keyed on the ADDRESS, not the lead, which is the only reason it survives
a lead being archived and returned to the pool. A hard bounce used to be a
STATUS, and the archive sweep rewrites status - so the address would have come
back fully sendable with nothing on the row remembering the bounce.

WHAT THIS DOES NOT DO: send. It returns a decision. The sender is a separate
caller so that the decision can be tested, logged and displayed without any
possibility of a test sending mail.
"""

from api import db, email_validation


class HoldReason:
    UNCONFIRMED = 'email not confirmed by the agent'
    NO_NAME = 'no contact name captured'
    DOMAIN = 'email domain is neither the firm\'s website nor known free-mail'
    NEEDS_HUMAN = 'flagged for human review'
    ALREADY_REPLIED = 'they already replied - never auto-contact again'
    NO_CAMPAIGN = 'lead is not on a campaign'
    NOT_AUTO = 'campaign email 1 is set to manual'
    DRIP_STOPPED = 'the drip campaign is not running'
    NO_EMAIL = 'no email address'
    DO_NOT_SEND = 'address is on the email do-not-send list'
    ARCHIVED = 'lead is archived - it is resting'
    CHECK_FAILED = 'eligibility check could not run'


def eligibility(lead, campaign, validate=None, drip_step: bool = False) -> dict:
    """
    {'ok': bool, 'reasons': [str], 'domain_class': str|None}

    ok=True means EVERY exclusion ran and passed. Anything else holds.

    `drip_step=True` swaps ONE thing: the switch. Email 1 is gated by the call
    campaign's email_1_mode; a drip step is gated by its own campaign's
    is_running, which is the switch a person throws on /campaigns and is
    already unscoped from one_running_campaign so many drips may run.
    THE SEVEN EXCLUSIONS ARE IDENTICAL AND REUSED VERBATIM - the brief requires
    them on every step, not just the first, and a second copy of that list is
    the copy that drifts.

    `validate` is injectable so tests never touch DNS. It defaults to the same
    email_validation.check() the restart button uses - ONE implementation, so a
    rule cannot hold on one path and pass on the other.
    """
    validate = validate or email_validation.check
    reasons = []
    domain_class = None

    try:
        lead = dict(lead or {})
        campaign = dict(campaign or {})

        if not campaign:
            return {'ok': False, 'reasons': [HoldReason.NO_CAMPAIGN],
                    'domain_class': None}

        # THE SWITCH. Not an exclusion - a campaign that is off is not holding
        # anything, it is simply not sending.
        if drip_step:
            if not campaign.get('is_running'):
                return {'ok': False, 'reasons': [HoldReason.DRIP_STOPPED],
                        'domain_class': None}
        elif campaign.get('email_1_mode') != 'auto':
            return {'ok': False, 'reasons': [HoldReason.NOT_AUTO],
                    'domain_class': None}

        # 5 first: if they already answered, nothing else matters.
        if lead.get('replied_at') is not None:
            reasons.append(HoldReason.ALREADY_REPLIED)

        # 7 - resting. Nothing automated reaches an archived lead.
        if lead.get('status') == 'archived':
            reasons.append(HoldReason.ARCHIVED)

        # 4
        if lead.get('status') == 'human_review' or lead.get('needs_human'):
            reasons.append(HoldReason.NEEDS_HUMAN)

        # 1 and 2 are SOURCE-AWARE, and this is the only difference between an
        # imported lead and a call-sourced one.
        #
        # Both exclusions substitute for a human having verified the contact.
        # For a CALL-sourced lead the evidence is a spellback on a recorded
        # call, and nothing below changes for it - byte for byte, asserted by
        # test_the_call_path_is_unchanged_by_source_awareness.
        #
        # For an IMPORTED lead the evidence is different in kind: a person chose
        # to upload the file, asserted once for a batch rather than per lead.
        # That is why lead_source exists instead of setting
        # dm_email_confirmed = true on import - which would make that flag mean
        # two different things depending on origin, with the gate unable to tell
        # them apart. One fact, two homes.
        #
        # WHAT IS **NOT** RELAXED FOR AN IMPORT: the domain check below, the
        # do-not-send list, replied_at, needs_human, archived, and the dev
        # allowlist. An imported address still has to look like it belongs to
        # the firm.
        imported = lead.get('lead_source') == 'import'

        # 1
        if not imported and lead.get('dm_email_confirmed') is not True:
            reasons.append(HoldReason.UNCONFIRMED)

        # 2
        if not imported and not (lead.get('dm_name') or '').strip():
            reasons.append(HoldReason.NO_NAME)

        email = (lead.get('dm_email') or '').strip()
        if not email:
            reasons.append(HoldReason.NO_EMAIL)
        else:
            # 6 - the ADDRESS, checked before anything about the lead. This
            # list outlives the lead row on purpose.
            from api import archive as _archive
            if _archive.is_do_not_send(email):
                reasons.append(HoldReason.DO_NOT_SEND)
            # 3 - and the format / MX / role / disposable checks with it.
            v = validate(email, lead.get('website'))
            domain_class = v.get('domain_class')
            reasons.extend(v.get('reasons') or [])

        return {'ok': not reasons, 'reasons': reasons,
                'domain_class': domain_class}

    except Exception as exc:
        # FAIL CLOSED. An unexpected error is not permission to send.
        return {'ok': False,
                'reasons': [f'{HoldReason.CHECK_FAILED}: {type(exc).__name__}'],
                'domain_class': domain_class}


def record(lead_id, decision: dict) -> None:
    """
    Store why a lead was held, so it is visible ON THE LEAD rather than only in
    a log nobody opens. Cleared when the decision is ok.
    """
    reason = None if decision.get('ok') else '; '.join(decision.get('reasons') or [])
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE leads
                              SET autosend_hold_reason = %s,
                                  autosend_checked_at = now()
                            WHERE lead_id = %s""", (reason, lead_id))
