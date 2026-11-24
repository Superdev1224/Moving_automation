#!/usr/bin/env python3
"""One-off: works out why each PAST lead was lost and writes the rows for the spreadsheet.

Reads every card in "Moves Lost", asks Claude in batches why each was lost (using the team's own
notes on the card), and writes the rows to docs/lost-leads-backfill.json ready to load into the
sheet. It deliberately does NOT comment on the old cards - nobody wants 2,000 new notifications.

  python3 build/lost-backfill.py --limit 50      try it on 50 cards first
  python3 build/lost-backfill.py                 the whole history
"""
import json, pathlib, re, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIST_ID = '901705949004'
BATCH = 12
LIMIT = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else None
OUT = ROOT / 'docs/lost-leads-backfill.json'

secret = lambda name: next(l.split('=', 1)[1].strip() for l in (ROOT / '.secrets').read_text().splitlines()
                           if l.startswith(name + '='))
CLICKUP, ANTHROPIC = secret('CLICKUP_TOKEN'), secret('ANTHROPIC_API_KEY')

lib = (ROOT / 'lib/lost-reasons.js').read_text()
REASONS = re.findall(r"^  '(.+?)',$", lib[lib.index('const REASONS'):lib.index('const MARK')], re.M)
SYSTEM = ' '.join(re.findall(r"^\s*'(.*?)',$", lib[lib.index('const lostReasonSystem'):lib.index('// Cards come in batches')], re.M))
SYSTEM = SYSTEM.replace('Choose exactly one reason from this list:',
                        'Choose exactly one reason from this list: ' + ' | '.join(REASONS))


def clickup(path):
    req = urllib.request.Request(f'https://api.clickup.com/api/v2/{path}', headers={'Authorization': CLICKUP})
    return json.load(urllib.request.urlopen(req))


def lost_cards():
    cards, page = [], 0
    while page < 40:
        d = clickup(f'list/{LIST_ID}/task?include_markdown_description=true&include_closed=true'
                    f'&page={page}&statuses%5B%5D=moves%20lost')
        cards += d['tasks']
        if d.get('last_page') is not False or not d['tasks']:
            break
        page += 1
    return sorted(cards, key=lambda t: -int(t['date_updated']))


def context(task):
    comments = clickup(f"task/{task['id']}/comment").get('comments', [])
    notes = ' // '.join(re.sub(r'\s+', ' ', (c.get('comment_text') or '')).strip()
                        for c in comments[-12:] if c.get('comment_text'))
    desc = re.sub(r'\s+', ' ', (task.get('markdown_description') or '').replace('**', ''))[:400]
    return {'id': task['id'], 'name': (task.get('name') or '')[:80], 'url': task.get('url'),
            'lostAt': int(task['date_updated']),
            'notes': f'{desc} // {notes}'.strip(' /')[:1400] or '(no notes on the card)'}


def ask(batch):
    blocks = '\n---\n'.join(f"id: {c['id']}\nname: {c['name']}\nnotes: {c['notes']}" for c in batch)
    body = {'model': 'claude-opus-5', 'max_tokens': 4000, 'system': SYSTEM,
            'messages': [{'role': 'user', 'content': f'Cards ({len(batch)}). Answer with '
                          '{"items":[{"id":...,"reason":...,"note":...,"confident":...}]}\n\n' + blocks}]}
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', json.dumps(body).encode(),
                                 {'x-api-key': ANTHROPIC, 'anthropic-version': '2023-06-01',
                                  'content-type': 'application/json'})
    txt = ''.join(b.get('text', '') for b in json.load(urllib.request.urlopen(req))['content'])
    m = re.search(r'\{[\s\S]*\}', txt)
    return {str(i['id']): i for i in (json.loads(m.group(0)).get('items', []) if m else [])}


def main():
    cards = lost_cards()
    if LIMIT:
        cards = cards[:LIMIT]
    print(f'{len(cards)} lost cards to work through')
    rows, done = [], 0
    for start in range(0, len(cards), BATCH):
        chunk = [context(t) for t in cards[start:start + BATCH]]
        try:
            verdicts = ask(chunk)
        except Exception as e:
            print('  batch failed, skipping:', str(e)[:80]); continue
        for c in chunk:
            v = verdicts.get(c['id'])
            if not v:
                continue
            rows.append({
                'Lost on': time.strftime('%m/%d/%Y', time.localtime(c['lostAt'] / 1000)),
                'Customer': c['name'].split('|')[0].strip(),
                'Reason': v['reason'] if v.get('reason') in REASONS else 'Other',
                'What happened': str(v.get('note', ''))[:90],
                'Card': c['url'],
                'Certain': 'yes' if v.get('confident', True) else 'best guess',
            })
        done += len(chunk)
        print(f'  {done}/{len(cards)} cards, {len(rows)} rows')
        OUT.write_text(json.dumps(rows, indent=1))
        time.sleep(1)
    counts = {}
    for r in rows:
        counts[r['Reason']] = counts.get(r['Reason'], 0) + 1
    print('\nreasons found:')
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {v:>4}  {k}')
    print('\nwritten to', OUT)


if __name__ == '__main__':
    main()
