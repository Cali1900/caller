"""
State -> IANA timezone.

A wrong timezone is a TCPA problem, not a cosmetic one: the calling window is
evaluated in the CALLED PARTY's local time, so a lead mapped to the wrong zone
gets dialed at the wrong hour.

STATES THAT SPAN ZONES get the DOMINANT zone and are FLAGGED for review rather
than guessed at silently. The operator sees them and decides.

Arizona is America/Phoenix - it does not observe DST, so mapping it to
America/Denver would be an hour out for most of the year.
"""

# Single-zone states: safe to derive.
STATE_TZ = {
    'AL': 'America/Chicago',    'AK': 'America/Anchorage',
    'AZ': 'America/Phoenix',    'AR': 'America/Chicago',
    'CA': 'America/Los_Angeles','CO': 'America/Denver',
    'CT': 'America/New_York',   'DE': 'America/New_York',
    'DC': 'America/New_York',   'GA': 'America/New_York',
    'HI': 'Pacific/Honolulu',   'IA': 'America/Chicago',
    'IL': 'America/Chicago',    'LA': 'America/Chicago',
    'MA': 'America/New_York',   'MD': 'America/New_York',
    'ME': 'America/New_York',   'MN': 'America/Chicago',
    'MO': 'America/Chicago',    'MS': 'America/Chicago',
    'MT': 'America/Denver',     'NC': 'America/New_York',
    'NH': 'America/New_York',   'NJ': 'America/New_York',
    'NM': 'America/Denver',     'NV': 'America/Los_Angeles',
    'NY': 'America/New_York',   'OH': 'America/New_York',
    'OK': 'America/Chicago',    'PA': 'America/New_York',
    'RI': 'America/New_York',   'SC': 'America/New_York',
    'UT': 'America/Denver',     'VA': 'America/New_York',
    'VT': 'America/New_York',   'WA': 'America/Los_Angeles',
    'WI': 'America/Chicago',    'WV': 'America/New_York',
    'WY': 'America/Denver',
}

# Split states: dominant zone by population, FLAGGED for review.
SPLIT_STATE_TZ = {
    'TX': 'America/Chicago',      # far west (El Paso) is Mountain
    'FL': 'America/New_York',     # panhandle west of Apalachicola is Central
    'TN': 'America/Chicago',      # east TN (Knoxville, Chattanooga) is Eastern
    'KY': 'America/New_York',     # western KY is Central
    'IN': 'America/New_York',     # northwest + southwest corners are Central
    'ND': 'America/Chicago',      # southwest corner is Mountain
    'SD': 'America/Chicago',      # west river is Mountain
    'NE': 'America/Chicago',      # panhandle is Mountain
    'KS': 'America/Chicago',      # four western counties are Mountain
    'MI': 'America/New_York',     # four western UP counties are Central
    'OR': 'America/Los_Angeles',  # most of Malheur County is Mountain
    'ID': 'America/Boise',        # north of the Salmon River is Pacific
}

DEFAULT_TZ = 'America/New_York'


def for_state(raw):
    """
    Returns (timezone, source, needs_review).

    source: 'state' when derived, 'default' when the state is unknown.
    needs_review is True for split states and for anything we had to default.
    """
    code = (raw or '').strip().upper()[:2]
    if code in STATE_TZ:
        return STATE_TZ[code], 'state', False
    if code in SPLIT_STATE_TZ:
        return SPLIT_STATE_TZ[code], 'state', True
    return DEFAULT_TZ, 'default', True
