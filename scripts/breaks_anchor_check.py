"""
Every break's OLD text must appear EXACTLY ONCE in its target.

Zero times means the definition is stale - the code moved under it. The pass
does report that, but only when it reaches the break, which on a 90-break run
can be twenty minutes in and after every earlier chunk has been recorded
green. Checking statically costs a second.

More than once is worse than stale: the patch would apply to whichever
occurrence came first, so the break might remove a copy of the guard nobody
was testing and the named test would stay green - a masked guard manufactured
by the tool meant to find them.
"""
import glob
import os
import re
import runpy
import sys


def main() -> int:
    specs = sorted(glob.glob('scripts/breaks/*.py'))
    bad = []
    for spec in specs:
        try:
            m = runpy.run_path(spec)
            src = open(m['TARGET']).read()
        except Exception as exc:
            bad.append((os.path.basename(spec), f'could not read: {exc}'))
            continue
        n = src.count(m['OLD'])
        if n == 0:
            bad.append((os.path.basename(spec),
                        f"anchor is GONE from {m['TARGET']} - the code moved "
                        f"under this definition and it now tests nothing"))
        elif n > 1:
            bad.append((os.path.basename(spec),
                        f"anchor appears {n} times in {m['TARGET']} - the "
                        f"patch would hit the first one, which may not be "
                        f"the guard the named test reads"))
    if bad:
        print(f'{len(bad)} of {len(specs)} break definitions are unusable:')
        for name, why in bad:
            print(f'  {name}: {why}')
        return 1
    print(f'  all {len(specs)} break anchors match exactly once ✓')
    return 0


def check_expect_tests() -> int:
    """
    ⚠️ EVERY EXPECT MUST NAME A TEST THAT EXISTS.

    A break whose named test has been renamed or deleted is a guard NOTHING
    verifies, and it reads like coverage until a full pass gets to it - which is
    how break 136 sat pointing at test_stopping_a_fed_drip_asks_first after the
    status gate renamed it, and cost a chunk failure forty breaks in.

    Checked here, beside the anchors, because both answer the same question: is
    this definition still connected to the thing it claims to guard? The anchor
    check catches the code end; this catches the test end.
    """
    import glob
    tests = set()
    for f in glob.glob('tests/*.py'):
        tests |= set(re.findall(r'^def (test_\w+)', open(f).read(), re.M))
    bad = []
    for f in sorted(glob.glob('scripts/breaks/*.py')):
        m = re.search(r"^EXPECT = ['\"](\w+)['\"]", open(f).read(), re.M)
        name = m.group(1) if m else None
        if name is None:
            bad.append((os.path.basename(f), '(no EXPECT at all)'))
        elif name not in tests:
            bad.append((os.path.basename(f), name))
    for f, e in bad:
        print(f'  {f}: EXPECT names {e}, which no test defines - '
              f'a guard nothing verifies')
    return len(bad)


if __name__ == '__main__':
    # BOTH ENDS, in one exit status. An anchor that has moved and an EXPECT that
    # names nothing are the same failure seen from opposite sides: a definition
    # that is no longer connected to what it claims to guard.
    sys.exit(main() + check_expect_tests())
