"""
WHY IS THIS LEAD HERE - one line, assembled from state.

No new data and no new column. Everything below is already on the row or in
a count beside it; this only says it in a sentence.

ONE IMPLEMENTATION, TWO PLACES. The same line renders at the top of lead
detail and under the firm name in the leads list, because the whole reason
to search for a firm is to find out where it stands - and a search result
that makes you click through to learn that has not answered the question.
Two generators would drift, and the one on the list is the one that would
quietly go stale.

The row it reads is the LEADS LIST ROW: `leads.*` plus the counts the list
query already computes. Lead detail builds the same shape rather than a
richer one, so the two cannot diverge in what they can say.
"""

import datetime

# Reasons that mean the phone never reached a person. Mirrors
# drain.NO_CONNECT_REASONS - imported rather than restated, so a new
# disconnection reason cannot be a human here and not there.
from api.drain import NO_CONNECT_REASONS      # noqa: F401  (re-exported below)

_WORDS = {1: 'once', 2: 'twice'}


def _times(n: int) -> str:
    """'once', 'twice', '3 times'. Sean's own phrasing."""
    return _WORDS.get(n, f'{n} times')


def _count(n: int, noun: str) -> str:
    return f'{n} {noun}' if n != 1 else f'1 {noun}'


def _day(dt) -> str:
    """'Sep 8'. Year only when it is not this one - a date that reads as
    this year when it is not is worse than no date."""
    if not isinstance(dt, (datetime.datetime, datetime.date)):
        return ''
    if dt.year != datetime.date.today().year:
        return dt.strftime('%b %-d, %Y')
    return dt.strftime('%b %-d')


def _calls(row) -> str:
    n = int(row.get('call_count') or 0)
    if not n:
        return 'Not called yet'
    human = int(row.get('human_calls') or 0)
    if human:
        return f'Called {_times(n)}, reached a human {_times(human)}'
    return f'Called {_times(n)}, never reached a human'


def _capture(row) -> str:
    name = (row.get('dm_name') or '').strip()
    email = (row.get('dm_email') or '').strip()
    if not email:
        return ''
    when = _day(row.get('stage_changed_at'))
    who = name or 'Someone there'
    if not row.get('dm_email_confirmed'):
        # UNCONFIRMED IS THE WHOLE POINT of saying it. An unconfirmed email
        # is why a lead sits at L1 with an address on it and nothing sends.
        return f'{who} gave an email but it is unconfirmed'
    return f'{who} gave their email{f" {when}" if when else ""}'


def _email(row) -> str:
    bits = []
    if row.get('emailed_at'):
        n = int(row.get('email_count') or 0)
        when = _day(row.get('emailed_at'))
        bits.append(f'Emailed {when}' if when else 'Emailed')
        if n > 1:
            bits[-1] += f' ({_count(n, "emails")} in all)'
        clicks = int(row.get('clicks') or 0)
        if clicks:
            bits.append(f'clicked the sample {_times(clicks)}')
    elif row.get('email_state') == 'draft ready':
        bits.append('A draft is ready to send')
    return ', '.join(bits)


def _waiting(row) -> str:
    """What is actually being waited on. The last clause, and the one Sean
    is reading the line for."""
    status = row.get('status')

    if status == 'archived':
        when = _day(row.get('archived_at'))
        reason = (row.get('archive_reason') or '').replace('_', ' ')
        back = _day(row.get('returns_at'))
        out = f'Archived {when}' if when else 'Archived'
        if reason:
            out += f' — {reason}'
        if back:
            out += f'. Returns to the pool {back}'
        return out

    if row.get('replied_at'):
        return f'They replied {_day(row["replied_at"])} — yours to work'
    if status == 'human_review':
        return 'Flagged for you'
    if status == 'dnc':
        return 'Do not call'
    if status == 'engaged':
        # 'Engaged.' on its own does not answer the question the line exists
        # to answer. A click is what usually sets it, and a click is interest,
        # not an answer - saying so is the difference between "chase this" and
        # "wait".
        if int(row.get('clicks') or 0):
            return 'Clicked but has not replied — yours to chase'
        return 'Engaged — yours to work'
    if status == 'bad_email':
        return 'The address bounced — it needs a new one'
    if status == 'lost_no_response':
        return 'Never replied — nothing automated will touch it again'
    if status in ('demo_booked', 'won', 'lost'):
        return status.replace('_', ' ').capitalize()
    if status == 'max_attempts':
        return 'Out of attempts — nothing will dial it again'
    if row.get('emailed_at'):
        return 'Waiting on a reply'
    if row.get('stage') == 'L2':
        return 'Owed an email'
    if row.get('next_attempt_at') and row.get('call_count'):
        return f'Next call due {_day(row["next_attempt_at"])}'
    return ''


def line(row) -> str:
    """
    The whole line. Two sentences at most, no empty clauses, always ends
    with a full stop - or is empty, which reads better than 'Not called yet.'
    on a lead nobody has touched and nobody is asking about.
    """
    row = dict(row or {})
    first = [c for c in (_calls(row), _capture(row)) if c]
    second = [c for c in (_email(row), _waiting(row)) if c]
    out = []
    if first:
        out.append('. '.join(first) + '.')
    if second:
        out.append('. '.join(second) + '.')
    return ' '.join(out)
