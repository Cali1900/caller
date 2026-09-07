"""
The calling window.

TCPA is 8:00am-9:00pm IN THE CALLED PARTY'S LOCAL TIME. We are in Los Angeles
calling nationwide, so an East Coast firm opens at 5am our time and closes at
6pm our time. This is not something the agent can be told; it is a WHERE
clause, evaluated in SQL with AT TIME ZONE against leads.timezone.

Three deliberate choices:

  * leads.timezone is an IANA NAME (America/New_York), never an offset. An
    offset is wrong twice a year.

  * The window closes at 20:30, not 21:00. A call placed at 20:59 is still
    running at 21:02, and the violation is measured on the call, not the dial.

  * The operator's per-weekday preference can only ever NARROW the legal
    window, never widen it. That is enforced structurally: both fragments are
    ANDed into the same query, so no row can pass the preference check without
    also passing the legal one. There is no configuration that makes 22:00
    dialable.

Each fragment is a separate constant so the break pass can remove exactly one
and watch exactly the matching test go red.
"""

# TCPA. Federal, non-negotiable, applies to every lead.
LEGAL_WINDOW = """
    AND (now() AT TIME ZONE l.timezone)::time
        BETWEEN TIME '08:00' AND TIME '20:30'
"""

# The operator's own hours, per weekday, ALSO in the called party's local
# time - so "9 to 5" means 9-5 where the phone is ringing, not where we are.
# Sunday is disabled by default: legal, but pointless for a law-firm front
# desk.
PREFERENCE_WINDOW = """
    AND EXISTS (
        SELECT 1 FROM dialing_windows w
         WHERE w.dow = EXTRACT(dow FROM now() AT TIME ZONE l.timezone)::int
           AND w.enabled
           AND (now() AT TIME ZONE l.timezone)::time
               BETWEEN w.start_time AND w.end_time
    )
"""


def window_debug_sql() -> str:
    """
    Why a specific lead is or is not dialable right now. Used by tests and by
    `scripts/why_not_dialed.sh` - "nothing dialed" must never be a mystery.
    """
    return """
        SELECT l.lead_id, l.company, l.phone_e164, l.timezone,
               (now() AT TIME ZONE l.timezone)              AS local_now,
               (now() AT TIME ZONE l.timezone)::time        AS local_time,
               EXTRACT(dow FROM now() AT TIME ZONE l.timezone)::int AS local_dow,
               ((now() AT TIME ZONE l.timezone)::time
                    BETWEEN TIME '08:00' AND TIME '20:30')  AS in_legal_window,
               EXISTS (
                   SELECT 1 FROM dialing_windows w
                    WHERE w.dow = EXTRACT(dow FROM now() AT TIME ZONE l.timezone)::int
                      AND w.enabled
                      AND (now() AT TIME ZONE l.timezone)::time
                          BETWEEN w.start_time AND w.end_time
               )                                            AS in_preference_window,
               EXISTS (SELECT 1 FROM suppression s
                        WHERE s.phone_e164 = l.phone_e164)  AS suppressed
          FROM leads l
         WHERE l.lead_id = %s
    """
