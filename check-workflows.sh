#!/bin/bash
# Pre-deploy checks for every built workflow:
#   1. each Code node's JavaScript parses
#   2. no function name is declared twice in one Code node (libraries pasted together can clash,
#      and the later copy silently wins — that bug cost an hour once)
set -u
tmp=$(mktemp -d); rc=0

for wf in /root/Moving_automation/workflows/*.json; do
  python3 - "$wf" "$tmp" <<'PY'
import json, pathlib, re, sys
wf, tmp = sys.argv[1], sys.argv[2]
for n in json.load(open(wf)).get('nodes', []):
    if n.get('type') == 'n8n-nodes-base.code':
        name = re.sub(r'\W+', '_', f"{pathlib.Path(wf).stem}__{n['name']}")
        pathlib.Path(tmp, name + '.js').write_text(n['parameters']['jsCode'])
PY
done

python3 - "$tmp" <<'PY'
import collections, pathlib, re, sys
bad = False
for f in sorted(pathlib.Path(sys.argv[1]).glob('*.js')):
    names = re.findall(r'^function\s+(\w+)\s*\(', f.read_text(), re.M)
    dupes = [n for n, c in collections.Counter(names).items() if c > 1]
    if dupes:
        print(f'DUPLICATE {f.stem}: {", ".join(dupes)}')
        bad = True
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] || rc=1

for f in "$tmp"/*.js; do
  if docker run --rm --user root -v "$tmp":/w --entrypoint node docker.n8n.io/n8nio/n8n:2.39.8 --check "/w/$(basename "$f")" >/dev/null 2>&1
  then echo "OK     $(basename "$f" .js)"
  else echo "FAILED $(basename "$f" .js)"; rc=1; fi
done

rm -rf "$tmp"
exit $rc
