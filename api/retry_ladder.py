"""
RETRY LADDERS: how long to wait before dialing a firm again.

A ladder is an ordered list of rungs, one per attempt. After the Nth attempt
the dialer waits rung N. Busy is deliberately the shortest - a busy signal
means a human is there, which is the best signal in the list.

A RUNG IS ONE OF TWO THINGS, and it has to be, because they are not
interchangeable:

    '15m' '4h' '3d'   a duration from now
    'next_day'        09:00 tomorrow in the CALLED PARTY's timezone

"4 hours" cannot express "tomorrow morning their time", and a fixed '24h'
lands at whatever hour the previous attempt happened to fall on - dial at
19:50 and the next attempt is 19:50, which the calling window then pushes to
the following morning anyway, a day later than intended.

EVERY VALUE IS BOUND, never interpolated. The old BACKOFF dict interpolated
its intervals as SQL because the voicemail rule referenced l.timezone and a
bound interval cannot. Naming the two shapes separately fixes that: the
timezone expression is a fixed fragment with no caller data in it, and the
duration is a bound ::interval.
"""

import re

NEXT_DAY = 'next_day'
NEXT_DAY_AT = '09:00'

# Sean's values, and the defaults on every new campaign.
DEFAULTS = {
    'busy':      ['15m', '1h', '4h', NEXT_DAY],
    'no_answer': ['2h', '8h', '1d', '3d'],
    'voicemail': [NEXT_DAY],
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


class BadLadder(ValueError):
    pass


def parse(rung: str):
    """
    ('next_day', None) or ('interval', '15 minutes').

    Raises rather than guessing. A rung nobody can parse must not silently
    become a default gap - that is how a firm gets called four times in a
    morning while the screen says four hours.
    """
    r = (rung or '').strip().lower()
    if r == NEXT_DAY:
        return (NEXT_DAY, None)
    m = _RUNG.match(r)
    if not m:
        raise BadLadder(
            f'{rung!r} is not a retry gap. Use a number with m, h or d '
            f'(15m, 4h, 3d) or the word next_day.')
    n = int(m.group(1))
    if n < 1:
        raise BadLadder(f'{rung!r} is not a wait at all - a zero gap would '
                        f'redial immediately.')
    return ('interval', f'{n} {_UNITS[m.group(2)]}')


def minutes(rung: str) -> int:
    """Roughly how long a rung is, for ORDERING ONLY. next_day is treated as
    a day because that is what it is for - it must not sort before 4h."""
    kind, val = parse(rung)
    if kind == NEXT_DAY:
        return 24 * 60
    n, unit = val.split()
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
    (sql_fragment, params) for next_attempt_at. `l` is the leads alias.

    The fragment contains NO caller data - the duration and the hour are
    bound. That is the whole reason the two shapes are named separately.
    """
    kind, val = parse(rung)
    if kind == NEXT_DAY:
        return ("(((now() AT TIME ZONE l.timezone)::date + 1) + %s::time)"
                " AT TIME ZONE l.timezone", [NEXT_DAY_AT])
    return ('now() + %s::interval', [val])


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
