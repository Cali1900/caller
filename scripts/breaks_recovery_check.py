import glob, hashlib, os, sys
state = sys.argv[1]
manifest = os.path.join(state, 'MANIFEST')
if not os.path.exists(manifest):
    sys.exit(0)

def md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()

edited = []
for line in open(manifest):
    want, path = line.split(None, 1)
    path = path.strip()
    if not os.path.exists(path) or md5(path) == want:
        continue
    original = open(os.path.join(state, 'originals',
                                 os.path.basename(path))).read()
    current = open(path).read()
    is_a_break = False
    for f in sorted(glob.glob('scripts/breaks/*.py')):
        d = {}
        exec(open(f).read(), d)
        if d['TARGET'] != path:
            continue
        if d['OLD'] in original and original.replace(d['OLD'], d['NEW'], 1) == current:
            is_a_break = True
            break
    if not is_a_break:
        edited.append(path)

if edited:
    print('  THESE FILES CHANGED SINCE THE SNAPSHOT AND ARE NOT A BREAK:')
    for p in edited:
        print(f'      {p}')
    print('  Restoring would DISCARD that work. Refusing.')
    print(f'  Inspect {state}/originals, then keep your version and delete')
    print(f'  {state}, or copy the original back by hand.')
    sys.exit(1)
