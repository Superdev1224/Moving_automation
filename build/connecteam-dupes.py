#!/usr/bin/env python3
"""Builds workflows/connecteam-dupes.json — no double shifts in Connecteam (2026-11-06).

The ClickUp → Connecteam sync (#3/#4) makes a shift when a card enters Moves Booked, and skips it when
the team already made one for that customer that day. But the team often schedules the job by hand a
minute or two AFTER the sync did (Philip Martinelli, 11/5: ours at 12:11 PM, theirs at 12:13 PM), and
then the crew sees the move twice.

Every 10 minutes: the next ~4 months of shifts. Where one customer has an automatic shift (ours carry
"clickup-id:" in their notes) AND a hand-made one on the same day, the team's is the real one - it is
the one they edit and staff - so ours is deleted. Only when nobody is assigned to ours; otherwise it
is left alone and flagged. The ClickUp card gets a note either way.

  python3 build/connecteam-dupes.py          dry run: reports what it would delete
  python3 build/connecteam-dupes.py --live   live: deletes our duplicate shifts
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                 # #automation-errors
SCHEDULER = 16427123
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

WINDOWS = """
// Connecteam returns at most a month at a time: four windows from now
const now = Math.floor(Date.now() / 1000), month = 30 * 86400;
return [0, 1, 2, 3].map(i => ({ json: { from: now - 6 * 3600 + i * month, to: now - 6 * 3600 + (i + 1) * month } }));
"""

FIND = f"""
// Same customer, same day, one of ours and one of the team's: ours goes.
const rows = $input.all().flatMap(i => i.json.data?.shifts || []);
const byId = new Map();
for (const s of rows) {{                   // one row per crew member: merge them back into one shift
  const prev = byId.get(s.id);
  const users = [...new Set([...(prev?.users || []), ...(s.assignedUserIds || [])])];
  byId.set(s.id, {{ ...s, users }});
}}
const shifts = [...byId.values()];
const notes = s => (s.notes || []).map(n => n.html || '').join(' ');
const cardOf = s => (notes(s).match(/clickup-id:(\\w+)/) || [])[1] || '';
const person = t => String(t || '').split('|')[0].toLowerCase().replace(/\\s+/g, ' ')
  .replace(/\\b(referred by|c\\/o)\\b.*$/, '').replace(/\\bday \\d+\\b/, '').trim();
const day = sec => new Intl.DateTimeFormat('en-CA', {{ timeZone: 'America/New_York' }}).format(new Date(sec * 1000));
const clock = sec => new Date(sec * 1000).toLocaleString('en-US', {{ timeZone: 'America/New_York', month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' }});
const groups = new Map();
for (const s of shifts) {{
  const who = person(s.title);
  if (!who || /^(dont|don't|do not) book/.test(who)) continue;
  const k = who + '|' + day(s.startTime);
  groups.set(k, [...(groups.get(k) || []), s]);
}}
const out = [], kept = [];
for (const list of groups.values()) {{
  const ours = list.filter(cardOf), theirs = list.filter(s => !cardOf(s));
  if (!ours.length || !theirs.length) continue;
  const team = theirs[0];
  for (const s of ours) {{
    const row = {{ shiftId: s.id, cardId: cardOf(s), title: s.title, ours: clock(s.startTime), theirs: clock(team.startTime), teamTitle: team.title }};
    if (s.users.length) {{ kept.push(row); continue; }}
    out.push({{ json: {{ ...row, comment: `🗓️ Removed the automatic Connecteam shift (${{row.ours}}): the team scheduled this job by hand (${{row.theirs}}), so it was on the schedule twice.` }} }});
  }}
}}
// a double with crew already on our shift is flagged once, not every 10 minutes
const memory = $getWorkflowStaticData('global');
memory.flagged = memory.flagged || {{}};
for (const [k, v] of Object.entries(memory.flagged)) if (Date.now() - v > 3 * 86400000) delete memory.flagged[k];
const newKept = kept.filter(k => !memory.flagged[k.shiftId]);
newKept.forEach(k => {{ memory.flagged[k.shiftId] = Date.now(); }});
const lines = [];
if (out.length) lines.push(`:wastebasket: *${{out.length}} duplicate Connecteam shift${{out.length > 1 ? 's' : ''}} ${{{json.dumps(LIVE)} ? 'removed' : 'found - dry run'}}* (kept the team's, removed the automatic one)`,
  ...out.map(o => `• ${{o.json.title}} - automatic ${{o.json.ours}} removed, team's ${{o.json.theirs}} kept`));
if (newKept.length) lines.push(`:warning: *Possible double shift, crew already on the automatic one - please check:*`,
  ...newKept.map(k => `• ${{k.title}} - automatic ${{k.ours}} / team's ${{k.theirs}}`));
if (lines.length) out.push({{ json: {{ isReport: true, text: lines.join('\\n') }} }});
return out;
"""


def http(name, pos, method, url, cred, body=None, extra=None):
    p = {'method': method, 'url': url, 'options': {}}
    if 'clickUpApi' in cred:
        p.update({'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi'})
    else:
        p.update({'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth'})
    if body is not None:
        p.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos, 'parameters': p,
         'credentials': cred, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000}
    n.update(extra or {})
    return n

def if_node(name, left, pos):
    return {'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': pos,
            'parameters': {'conditions': {
                'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                'combinator': 'and',
                'conditions': [{'id': name, 'leftValue': left, 'rightValue': '',
                                'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                'options': {}}}

nodes = [
    {'name': 'Every 10 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 10}]}}},
    {'name': 'Four months', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [200, 0],
     'parameters': {'jsCode': WINDOWS.strip()}},
    http('Shifts', [400, 0], 'GET',
         f'=https://api.connecteam.com/scheduler/v1/schedulers/{SCHEDULER}/shifts?startTime={{{{ $json.from }}}}&endTime={{{{ $json.to }}}}&limit=500',
         CRED_CONNECTEAM),
    {'name': 'Doubles', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [600, 0],
     'parameters': {'jsCode': FIND.strip()}},
    if_node('Report?', '={{ $json.isReport }}', [800, 0]),
    {'name': 'Tell us', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1000, -120],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'}, 'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
    http('Delete our shift', [1000, 120], 'DELETE',
         f'=https://api.connecteam.com/scheduler/v1/schedulers/{SCHEDULER}/shifts/{{{{ $json.shiftId }}}}?notifyUsers=false',
         CRED_CONNECTEAM, extra={'disabled': not LIVE, 'onError': 'continueErrorOutput'}),
    http('Note on the card', [1200, 120], 'POST',
         "=https://api.clickup.com/api/v2/task/{{ $('Report?').item.json.cardId }}/comment", CRED_CLICKUP,
         body="={{ JSON.stringify({ comment_text: $('Report?').item.json.comment, notify_all: false }) }}",
         extra={'disabled': not LIVE, 'onError': 'continueRegularOutput'}),
]
connections = {
    'Every 10 minutes': {'main': [[{'node': 'Four months', 'type': 'main', 'index': 0}]]},
    'Four months': {'main': [[{'node': 'Shifts', 'type': 'main', 'index': 0}]]},
    'Shifts': {'main': [[{'node': 'Doubles', 'type': 'main', 'index': 0}]]},
    'Doubles': {'main': [[{'node': 'Report?', 'type': 'main', 'index': 0}]]},
    'Report?': {'main': [[{'node': 'Tell us', 'type': 'main', 'index': 0}],
                         [{'node': 'Delete our shift', 'type': 'main', 'index': 0}]]},
    'Delete our shift': {'main': [[{'node': 'Note on the card', 'type': 'main', 'index': 0}], []]},
}

wf = {'name': '#3c Connecteam: remove double shifts' + ('' if LIVE else ' [DRY RUN]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all'}}
out = ROOT / 'workflows/connecteam-dupes.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'DRY RUN')
