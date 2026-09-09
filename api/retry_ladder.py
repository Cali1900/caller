"""
RETRY LADDERS: how long to wait before dialing a firm again.

A ladder is an ordered list of rungs, one per attempt. After the Nth attempt
the dialer waits rung N. Busy is deliberately the shortest - a busy signal
means a human is there, which is the best signal in the list.

A RUNG IS A DURATION AND NOTHING ELSE: '15m', '4h', '3d'.

THE CALLING WINDOW IS THE CLAMP, NOT THE LADDER. next_attempt_at is a
NOT-BEFORE gate, never a scheduled dial time. windows.LEGAL_WINDOW and
windows.PREFERENCE_WINDOW are ANDed into the selection query and evaluated in
the called party's local time, so NO rung value can cause a call outside the
allowed hours. A rung that lands at 03:00 simply waits until the window opens.

THIS IS WHY 'next_day' WAS REMOVED (2026-09-09). It computed 09:00 tomorrow in
the lead's timezone, and the argument for it was that a plain '1d' after a
19:50 dial lands at 19:50, which the window then pushes to the following
morning - a day later than intended. That is true only when the preference
window is WIDE enough to have dialed at 19:50 in the first place. Under the
default 09:00-17:00 it cannot happen: the previous attempt was inside the
window, so the same local time tomorrow is inside it too.

So it bought nothing at the configured hours and cost the only raw-SQL
fragment in this module, a special case in three functions, and its own break
definition. If the evening window is ever widened, reconsider it - and bring
it back with a test that widens the window, because that is the only condition
under which it is observable at all. (Same shape as masked guard #1 in
README.md.)

EVERY VALUE IS BOUND, never interpolated. The old BACKOFF dict interpolated
its intervals as SQL because the voicemail rule referenced l.timezone; with
one rung shape there is no caller data in the fragment at all.
"""

import re

# Sean's values, and the defaults on every new campaign.
DEFAULTS = {
    'busy':      ['15m', '1h', '4h', '1d'],
    'no_answer': ['2h', '8h', '1d', '3d'],
    'voicemail': ['1d'],
}

# outcome -> the campaign column holding its ladder
COLUMNS = {
    'busy':      'retry_busy',
    'no_answer': 'retry_no_answer',
    'voicemail': 'retry_voicemail',
}

# Outcomes with no ladder of their own fall back to no_answer's: they are all
# "the call did not reach a person", and inventing a fourth editable ladder
# for api_error would be a setting nobody would ever tune.
FALLBACK = 'no_answer'

_UNITS = {'m': 'minutes', 'h': 'hours', 'd': 'days'}
_RUNG = re.compile(r'^(\d+)([mhd])$')


# The gap used when a rung cannot be read at all. Deliberately the widest
# rung in the defaults, not a tight one: an unreadable ladder is an UNKNOWN,
# and unknown must never dial faster than configured.
FALLBACK_RUNG = '1d'


class BadLadder(ValueError):
    pass


def parse(rung: str) -> str:
    """
    '15m' -> '15 minutes'. A Postgres interval string, ready to BIND.

    Raises rather than guessing. A rung nobody can parse must not silently
    become a default gap - that is how a firm gets called four times in a
    morning while the screen says four hours.
    """
    r = (rung or '').strip().lower()
    m = _RUNG.match(r)
    if not m:
        raise BadLadder(
            f'{rung!r} is not a retry gap. Use a number with m, h or d '
            f'(15m, 4h, 3d).')
    n = int(m.group(1))
    if n < 1:
        raise BadLadder(f'{rung!r} is not a wait at all - a zero gap would '
                        f'redial immediately.')
    return f'{n} {_UNITS[m.group(2)]}'


def minutes(rung: str) -> int:
    """How long a rung is, for ORDERING ONLY - validate() uses it to refuse a
    ladder that goes backwards."""
    n, unit = parse(rung).split()
    return int(n) * {'minutes': 1, 'hours': 60, 'days': 1440}[unit]


def validate(ladder) -> list:
    """
    Clean a ladder or refuse it. Returns the normalised rungs.

    A ladder that goes BACKWARDS is refused: waits that shrink as attempts
    rise means calling more often the less they want to hear from us, which
    is precisely the pattern this replaced.
    """
    rungs = [str(x).strip().lower() for x in (ladder or []) if str(x).strip()]
    if not rungs:
        raise BadLadder('a retry ladder needs at least one rung - an empty '
                        'one would leave next_attempt_at unset and the lead '
                        'would never be dialed again.')
    if len(rungs) > 10:
        raise BadLadder('ten rungs is more attempts than max_attempts allows.')
    prev = 0
    for r in rungs:
        m = minutes(r)          # parses, and raises on anything unreadable
        if m < prev:
            raise BadLadder(
                f'{r!r} is shorter than the rung before it. A ladder that '
                f'goes backwards calls a firm more often the longer they '
                f'have ignored us.')
        prev = m
    return rungs


def rung_for(ladder, attempts: int) -> str:
    """
    The wait after `attempts` attempts. 1-based: the first attempt uses rung 1.

    Past the end it holds at the last rung rather than falling off. A ladder
    shorter than max_attempts is a configuration to warn about, not a reason
    to redial in fifteen minutes.
    """
    rungs = ladder or DEFAULTS[FALLBACK]
    i = min(max(attempts, 1), len(rungs)) - 1
    return rungs[i]


def sql_for(rung: str):
    """
    (sql_fragment, params) for next_attempt_at.

    The fragment is a CONSTANT and contains no caller data whatsoever - the
    duration is bound. There is exactly one rung shape, so there is exactly
    one fragment; the calling window handles the hours.
    """
    return ('now() + %s::interval', [parse(rung)])


def reachable(ladder, max_attempts: int) -> int:
    """
    How many rungs can ever fire.

    A rung only fires if an attempt FOLLOWS it, so at max_attempts=4 the
    fourth rung of a four-rung ladder is unreachable. Surfacing this is the
    point: an editable value that does nothing is dead surface wearing the
    costume of a setting.
    """
    return max(0, min(len(ladder or []), max(0, max_attempts - 1)))


def for_campaign(campaign, outcome: str):
    """The ladder a campaign uses for an outcome, defaults if it has none."""
    key = outcome if outcome in COLUMNS else FALLBACK
    got = (campaign or {}).get(COLUMNS[key])
    return list(got) if got else list(DEFAULTS[key])
