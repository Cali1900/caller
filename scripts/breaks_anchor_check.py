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


if __name__ == '__main__':
    sys.exit(main())
