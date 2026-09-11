#!/usr/bin/env python3
"""
⚠️ A FORM FIELD NO HANDLER READS IS WORSE THAN A MISSING ONE.

A missing control is obviously missing. A DEAD one tells the operator the thing
works: /upload-form offered a "kind" select and a drip picker for months, read
neither, and every email-only CSV had every row rejected for a missing phone -
immediately after the screen offered to import it.

So this walks every <form> in every template, collects the field names inside it,
finds the handler for its action, and reports any field that handler never
mentions. Static and approximate on purpose: it is a smoke alarm, not a compiler.

  ./scripts/check_dead_controls.py          report
  ./scripts/check_dead_controls.py --quiet  exit status only

KNOWN-OK entries are listed explicitly below, with a reason each, because a check
whose output is 90% noise is a check nobody runs.
"""
import glob
import os
import re
import sys

# Fields that legitimately reach no named handler parameter, with WHY. Anything
# not listed here and not read is reported.
KNOWN_OK = {
    # read off the raw form dict rather than as a parameter
    ('/campaign/*/steps', 'subject_'): 'read via request.form() by index',
    ('/campaign/*/steps', 'body_'): 'read via request.form() by index',
    ('/campaign/*/steps', 'delay_'): 'read via request.form() by index',
    ('/campaign/*/steps', 'day1_'): 'read via request.form() by index',
    ('/campaign/*/steps', 'step_id_'): 'read via request.form() by index',
    ('/campaign/*/steps', 'enabled_'): 'read via request.form() by index',
    ('/campaign/*/steps', 'delete_'): 'read via request.form() by index',
    ('/campaign/*/steps/preview', 'lead_id'): 'read via request.form()',
    ('/campaign/*/windows', 'enabled_'): 'read by weekday index',
    ('/campaign/*/windows', 'start_'): 'read by weekday index',
    ('/campaign/*/windows', 'end_'): 'read by weekday index',
    ('/campaign/*/save', 'retry_'): 'read as retry_busy / retry_no_answer / ...',
    ('/leads/bulk', 'lead_id'): 'repeated checkbox, read as a list',
    # the JS clone template: these names are rewritten to real indices before the
    # form is ever submitted, so the handler only sees subject_0, subject_1, ...
    ('/campaign/*/steps', '__I__'): 'clone placeholder, renamed by JS pre-submit',
    ('/campaign/*/steps', 'lead_id'): 'the preview picker, read by /steps/preview',
    # a CLIENT-SIDE gate: `required` forces the operator to tick it before the
    # browser will submit. It carries no information the handler needs.
    ('/leads/*/reply', 'got'): 'client-side required confirmation, not data',
    ('/leads/bulk', 'action'): 'read via request.form()',
    ('/leads/bulk', 'value'): 'read via request.form()',
}

FIELD = re.compile(r'name="([^"]+)"')
FORM = re.compile(r'<form\b([^>]*)>(.*?)</form>', re.S)
ACTION = re.compile(r'action="([^"]*)"')
# ⚠️ A BUTTON CAN RETARGET ITS FORM. /leads has one form posting to /leads/queue
# with a second button carrying formaction="/leads/queue-all" - and the hidden
# filter fields are read by THAT handler, not the form's own. Missing this reported
# fourteen live controls as dead, which is exactly the noise that gets a check
# switched off.
FORMACTION = re.compile(r'formaction="([^"]*)"')


def normalise(path: str) -> str:
    """A template action and a route pattern, in one shape: /campaign/*/save."""
    path = re.sub(r'\{\{[^}]*\}\}', '*', path)          # Jinja
    path = re.sub(r'\{[^}]*\}', '*', path)              # FastAPI
    return path.split('?')[0].rstrip('/') or '/'


def field_key(name: str) -> str:
    """`subject_{{ i }}` -> `subject_`; `delay___I__` -> `__I__`."""
    if '__I__' in name:
        return '__I__'          # the JS clone template, one entry for all of them
    if '{{' in name:
        return re.sub(r'\{\{.*', '', name)
    return name


def lookup(routes: dict, route: str):
    """
    Handler for a route, treating `*` as a wildcard SEGMENT on both sides.

    A template action can contain a Jinja expression in the path itself -
    action="/campaign/{{ id }}/{{ 'stop' if running else 'start' }}" - which
    normalises to /campaign/*/*. That has no literal route, and reporting it as
    "no handler" is a false alarm about the most ordinary markup there is.
    """
    if route in routes:
        return routes[route]
    parts = route.split('/')
    hits = ''
    for cand, body in routes.items():
        cp = cand.split('/')
        if len(cp) != len(parts):
            continue
        if all(a == b or '*' in (a, b) for a, b in zip(parts, cp)):
            hits += body
    return hits or None


def handlers(source: str) -> dict:
    """{normalised route: handler body}. Bodies run to the next decorator."""
    out = {}
    marks = [(m.start(), normalise(m.group(2)))
             for m in re.finditer(r"@router\.(get|post)\('([^']+)'", source)]
    for i, (pos, route) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(source)
        out.setdefault(route, '')
        out[route] += source[pos:end]
    return out


def main() -> int:
    quiet = '--quiet' in sys.argv
    src = ''.join(open(f).read() for f in glob.glob('api/*.py'))
    routes = handlers(src)
    dead, checked = [], 0
    for tpl in sorted(glob.glob('api/templates/*.html')):
        body = open(tpl).read()
        for attrs, inner in FORM.findall(body):
            m = ACTION.search(attrs)
            if not m or not m.group(1):
                continue                      # posts to itself; nothing to match
            route = normalise(m.group(1))
            # EVERY handler this form's fields can reach: its own action plus any
            # button that retargets it.
            targets = [route] + [normalise(a) for a in FORMACTION.findall(inner)]
            bodies = [lookup(routes, t) for t in targets]
            if all(b is None for b in bodies):
                dead.append((tpl, route, '(no handler for this action at all)'))
                continue
            handler = ''.join(b for b in bodies if b)
            for raw in dict.fromkeys(FIELD.findall(inner)):
                key = field_key(raw)
                checked += 1
                if any((t, key) in KNOWN_OK for t in targets):
                    continue
                if key and key in handler:
                    continue
                dead.append((tpl, route, key))
    if dead:
        if not quiet:
            print('⚠️  form fields no handler reads:')
            for tpl, route, key in dead:
                print(f'  {os.path.basename(tpl):22} {route:34} {key}')
            print()
            print('A dead control tells the operator the thing works. Either wire')
            print('it, delete it, or add it to KNOWN_OK with the reason.')
        return 1
    if not quiet:
        print(f'✓ every form field is read by its handler ({checked} checked)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
