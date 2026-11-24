#!/usr/bin/env python3
"""Builds workflows/clickup-to-connecteam.json (items #3 and #4).

ClickUp card moved to MOVES BOOKED    -> open shift in Connecteam job "Pending"
ClickUp card moved to ONSITE ESTIMATE -> open shift in Connecteam job "Onsite Estimate"
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PARSER = (ROOT / 'lib/parse-card.js').read_text()
PARSER = re.sub(r"\nif \(typeof module.*\n?$", "\n", PARSER)  # Code nodes have no module.exports

CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
SLACK_ERRORS = 'C0C2X8B9VS9'  # #automation-errors
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}

ROUTE = r"""
// Keep only status changes into the two statuses we schedule.
const KIND = { 'moves booked': 'move', 'onsite estimate': 'onsite' };
const out = [];
for (const item of $input.all()) {
  const b = item.json.body ?? item.json;
  const h = (b.history_items || []).find(x => x.field === 'status');
  const status = (h?.after?.status || '').toLowerCase();
  if (KIND[status] && b.task_id) out.push({ json: { taskId: b.task_id, kind: KIND[status], status } });
}
return out;
"""

PARSE = PARSER + r"""
// ---- settings ----
const SCHEDULER_ID = 16427123;
const JOBS = { move: '11379364-1760-9b0c-d58a-dd768c971dc8',   // Connecteam job "Pending"
               onsite: 'fbdae921-44b2-2c2b-fc49-bbdf4c8cad91' }; // Connecteam job "Onsite Estimate"
const PUBLISH = true; // true = crew sees it immediately; false = draft a manager publishes

const task = $input.first().json;
const { kind } = $('Route').first().json;
const r = parseCard(task, kind);
if (!r.ok) return [{ json: { ...r, kind, url: task.url } }];
return [{ json: {
  ...r,
  schedulerId: SCHEDULER_ID,
  windowStart: r.startTime - 3 * 86400,
  windowEnd: r.startTime + 3 * 86400,
  shiftBody: [{
    title: r.name,
    startTime: r.startTime,
    endTime: r.endTime,
    timezone: 'America/New_York',
    isOpenShift: true,
    openSpots: 1,
    isPublished: PUBLISH,
    jobId: JOBS[kind],
    notes: [{ type: 'html', html: r.notesHtml }],
  }],
} }];
"""

DEDUPE = r"""
// Skip if this job already has a shift. It counts as the same job when:
//   - the shift was made by this automation for this card (clickup-id in its notes), or
//   - it is the same customer on the same day (the team often edits shift titles,
//     e.g. "... | 9/13 | NEED TO SIGN AGAIN", so the full title can't be relied on), or
//   - the title is identical within +/-3 days.
const p = $('Parse card').first().json;
const shifts = $input.first().json.data?.shifts || [];
const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
const person = s => norm(String(s).split('|')[0]).replace(/\b(referred by|c\/o)\b.*$/, '').replace(/\bday \d+\b/, '').trim();
const nyDay = sec => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(sec * 1000));
const who = person(p.name), day = nyDay(p.startTime);
const dup = shifts.find(s =>
  (s.notes || []).some(n => (n.html || '').includes('clickup-id:' + p.taskId))
  || (who && person(s.title) === who && nyDay(s.startTime) === day)
  || norm(s.title) === norm(p.name));
return [{ json: { ...p, duplicate: !!dup, existingShiftId: dup?.id ?? null, existingShiftTitle: dup?.title ?? null } }];
"""

ALERT = r"""
// Message for #automation-errors when a card has no readable date.
const p = $input.first().json;
const what = p.kind === 'move' ? 'Moves Booked → Connecteam "Pending"' : 'Onsite Estimate → Connecteam "Onsite Estimate"';
return [{ json: { text: `:warning: Could not create a Connecteam shift (${what})\n*${p.name}*\n${p.reason}. Please add the date/time to the card (or set its due date) and create the shift by hand.\n${p.url || ''}` } }];
"""


JOB_NAMES = {'64483612-ed0e-5631-3bda-a99d1489bac6': 'Job', 'fb970c79-fad7-176f-8330-3a677c6a8898': 'Job 2',
             '7c424435-e57f-deee-6275-2d43a256bd04': 'Job 3', 'e6254a3b-2397-1e0c-a2fa-45af03f34bb4': 'Job 4',
             'fbdae921-44b2-2c2b-fc49-bbdf4c8cad91': 'Onsite Estimate', '11379364-1760-9b0c-d58a-dd768c971dc8': 'Pending',
             'a5143d69-26b6-ddb5-ff70-09f632b77043': 'Scheduled Calls', '2250f597-f864-ab9e-21a8-201be017fe0d': 'Virtual Estimate'}

NOTE_HELPERS = """
const JOBS = %JOBS%;
const when = sec => new Date(sec * 1000).toLocaleString('en-US', { timeZone: 'America/New_York',
  weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
""".replace('%JOBS%', json.dumps(JOB_NAMES))

NOTE_CREATED = NOTE_HELPERS + """
// Tell the team on the card that the shift is in Connecteam.
return $input.all().map((item, i) => {
  const p = $('Already scheduled?').all(1)[i]?.json || $('Parse card').first().json;
  const sh = item.json?.data?.shifts?.[0] || {};
  const warn = (p.warnings || []).filter(w => /disagree/i.test(w));
  return { json: { taskId: p.taskId, comment:
    `📅 Added to Connecteam → ${JOBS[sh.jobId] || 'Pending'}, ${when(sh.startTime || p.startTime)} (${p.hours} hrs)` +
    (warn.length ? `\\n⚠ ${warn[0]}` : '') } };
});
"""

NOTE_SKIPPED = NOTE_HELPERS + """
// Tell the team on the card that a shift already existed, and where.
return $input.all().map(item => {
  const p = item.json;
  const found = ($('Find nearby shifts').first().json.data?.shifts || []).find(s => s.id === p.existingShiftId) || {};
  return { json: { taskId: p.taskId, comment:
    `📅 Already in Connecteam: "${(p.existingShiftTitle || '').slice(0, 60)}" on ${found.startTime ? when(found.startTime) : '?'}` +
    ` (${JOBS[found.jobId] || 'another schedule'}). No new shift created.` } };
});
"""

NOTE_PROBLEM = """
// Tell the team on the card why it could not be added.
return $input.all().map(() => {
  const p = $('Parse card').first().json;
  return { json: { taskId: p.taskId || $('Route').first().json.taskId,
    comment: `⚠ Not added to Connecteam: ${p.reason || 'the date could not be read'}.` } };
});
"""

def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}

def if_node(name, left, pos):
    return {'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': pos,
            'parameters': {'conditions': {
                'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                'combinator': 'and',
                'conditions': [{'id': name, 'leftValue': left, 'rightValue': '',
                                'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                'options': {}}}

nodes = [
    {'name': 'ClickUp status changed', 'type': 'n8n-nodes-base.clickUpTrigger', 'typeVersion': 1, 'position': [0, 300],
     'parameters': {'team': '9017263768', 'events': ['taskStatusUpdated'], 'filters': {'listId': '901705949004'}},
     'credentials': CRED_CLICKUP},
    code('Route', ROUTE, [220, 300]),
    {'name': 'Get card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [440, 300],
     'parameters': {'method': 'GET',
                    'url': '=https://api.clickup.com/api/v2/task/{{ $json.taskId }}?include_markdown_description=true',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi', 'options': {}},
     'credentials': CRED_CLICKUP},
    code('Parse card', PARSE, [660, 300]),
    if_node('Date found?', '={{ $json.ok }}', [880, 300]),
    {'name': 'Find nearby shifts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, 200],
     'parameters': {'method': 'GET',
                    'url': '=https://api.connecteam.com/scheduler/v1/schedulers/{{ $json.schedulerId }}/shifts?startTime={{ $json.windowStart }}&endTime={{ $json.windowEnd }}&limit=500',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'options': {}},
     'credentials': CRED_CONNECTEAM},
    code('Check duplicate', DEDUPE, [1320, 200]),
    if_node('Already scheduled?', '={{ $json.duplicate }}', [1540, 200]),
    {'name': 'Create shift', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 300],
     'parameters': {'method': 'POST',
                    'url': '=https://api.connecteam.com/scheduler/v1/schedulers/{{ $json.schedulerId }}/shifts?notifyUsers=false',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.shiftBody) }}',
                    'options': {}},
     'credentials': CRED_CONNECTEAM},
    code('Note: already scheduled', NOTE_SKIPPED, [1760, 100]),
    code('Note: shift created', NOTE_CREATED, [1980, 300]),
    code('Note: problem', NOTE_PROBLEM, [1320, 560]),
    {'name': 'Comment on card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2200, 200],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.taskId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
    code('Build alert', ALERT, [1100, 420]),
    {'name': 'Alert #automation-errors', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1320, 420],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': SLACK_ERRORS, 'mode': 'id'},
                    'text': '={{ $json.text }}', 'otherOptions': {'includeLinkToWorkflow': False}},
     'credentials': CRED_SLACK},
]

def link(*names):
    return {'main': [[{'node': n, 'type': 'main', 'index': 0}] for n in names]}

connections = {
    'ClickUp status changed': link('Route'),
    'Route': link('Get card'),
    'Get card': link('Parse card'),
    'Parse card': link('Date found?'),
    'Date found?': {'main': [[{'node': 'Find nearby shifts', 'type': 'main', 'index': 0}],
                             [{'node': 'Build alert', 'type': 'main', 'index': 0}]]},
    'Find nearby shifts': link('Check duplicate'),
    'Check duplicate': link('Already scheduled?'),
    'Already scheduled?': {'main': [[{'node': 'Note: already scheduled', 'type': 'main', 'index': 0}],
                                    [{'node': 'Create shift', 'type': 'main', 'index': 0}]]},
    'Create shift': link('Note: shift created'),
    'Note: already scheduled': link('Comment on card'),
    'Note: shift created': link('Comment on card'),
    'Build alert': {'main': [[{'node': 'Alert #automation-errors', 'type': 'main', 'index': 0},
                              {'node': 'Note: problem', 'type': 'main', 'index': 0}]]},
    'Note: problem': link('Comment on card'),
}

wf = {'name': '#3/#4 ClickUp → Connecteam shifts (Moves Booked, Onsite Estimate)',
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York', 'saveManualExecutions': True}}

out = ROOT / 'workflows/clickup-to-connecteam.json'
out.write_text(json.dumps(wf, indent=2))
print(out)
