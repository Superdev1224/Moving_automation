#!/usr/bin/env python3
"""Deploy built workflows to n8n, keeping each workflow's saved state (staticData) and on/off state.

  python3 deploy.py lead-sync inbound-leads      (names match workflows/<name>.json and .id)
Run ./check-workflows.sh first.
"""
import json, pathlib, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
API = 'https://n8n.expresshaulers.com/api/v1'
KEY = next(l.split('=', 1)[1].strip().strip('"\'') for l in (ROOT / '.secrets').read_text().splitlines()
           if l.startswith('N8N_API_KEY='))


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'X-N8N-API-KEY': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b'{}')


for name in sys.argv[1:]:
    wf_id = (ROOT / f'workflows/{name}.id').read_text().strip()
    new = json.loads((ROOT / f'workflows/{name}.json').read_text())
    old = call('GET', f'/workflows/{wf_id}')
    body = {k: new[k] for k in ('name', 'nodes', 'connections', 'settings')}
    if old.get('staticData'):
        body['staticData'] = old['staticData']
    call('PUT', f'/workflows/{wf_id}', body)
    if old.get('active'):   # re-register triggers so the new version runs
        call('POST', f'/workflows/{wf_id}/deactivate')
        call('POST', f'/workflows/{wf_id}/activate')
    print(f'{name}: deployed ({wf_id}), {"active" if old.get("active") else "inactive"}')
