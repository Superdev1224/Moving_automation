#!/usr/bin/env python3
"""Builds workflows/sync-audit.json — daily proof that ClickUp → Connecteam is working (items #9/#13).

Every morning at 7:30 AM it checks:
  1. the ClickUp webhook that feeds the automation is still healthy (ClickUp suspends failing ones),
  2. every upcoming "moves booked" / "onsite estimate" card has a shift in Connecteam,
  3. the ClickUp → Connecteam workflow had no failed runs in the last 24 hours,
and posts a one-line all-clear, or the list of problems, to #automation-errors.
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/parse-card.js').read_text())

ERRORS = 'C0C2X8B9VS9'
LIST_ID = '901705949004'
SYNC_WF = 'k8K8SUNU4sMzI3vp'

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_N8N = {'httpHeaderAuth': {'id': 'Bc7JADy3VUTvng5V', 'name': 'n8n API (self)'}}

AUDIT = LIB + f"""
const now = Date.now();
const get = (node, f, d) => {{ try {{ return f($(node).all()); }} catch (e) {{ return d; }} }};

// 1. is ClickUp still sending status changes to the automation?
const hooks = get('ClickUp webhooks', a => a.flatMap(i => i.json.webhooks || []), []);
const hook = hooks.find(h => String(h.list_id) === '{LIST_ID}' && (h.events || []).includes('taskStatusUpdated'));

// 2. does every upcoming booked move / onsite visit have a shift?
const cards = get('Cards to check', a => a.flatMap(i => i.json.tasks || []), []);
const shiftPages = get('Connecteam shifts', a => a.map(i => i.json), []);
const shifts = shiftPages.flatMap(j => j.data?.shifts || []);
// 2026-11-18: Connecteam started refusing ranges over 92 days and every move looked "missing" for two weeks.
// If Connecteam answers with an error, say so instead of listing every move.
const ctErrors = shiftPages.filter(j => !j.data).map(j => j.error?.message || j.error || j.message || 'no data');
const norm = s => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
const person = s => norm(String(s).split('|')[0]).replace(/\\b(referred by|c\\/o)\\b.*$/, '').replace(/\\bday \\d+\\b/, '').trim();
const nyDay = sec => new Intl.DateTimeFormat('en-CA', {{ timeZone: 'America/New_York' }}).format(new Date(sec * 1000));
let checked = 0;
const missing = [], unreadable = [];
for (const t of cards) {{
  const kind = (t.status?.status || '') === 'onsite estimate' ? 'onsite' : 'move';
  const r = parseCard(t, kind, now);
  if (!r.ok) {{ if (!/in the past/.test(r.reason)) unreadable.push(t); continue; }}
  if (r.startTime * 1000 < now) continue;                               // already happened
  checked++;
  const who = person(t.name), day = nyDay(r.startTime);
  const has = shifts.some(s => (s.notes || []).some(n => (n.html || '').includes('clickup-id:' + t.id))
    || (person(s.title) === who && nyDay(s.startTime) === day)
    || (person(s.title) === who && Math.abs(s.startTime - r.startTime) < 3 * 86400));
  if (!has && !ctErrors.length) missing.push({{ t, r }});
}}

// 3. did the sync workflow fail recently?
const failed = get('Failed sync runs', a => a.flatMap(i => i.json.data || []), [])
  .filter(e => Date.parse(e.startedAt || '') > now - 86400000);

const problems = [];
if (!hook) problems.push(':rotating_light: The ClickUp connection for status changes is *missing*. Cards moved to Moves Booked will not reach Connecteam.');
else if (hook.health?.status !== 'active') problems.push(`:rotating_light: ClickUp reports the connection as *${{hook.health?.status}}* (${{hook.health?.fail_count}} failures). Cards may not be reaching Connecteam.`);
if (ctErrors.length) problems.push(`:rotating_light: Could not read the shifts from Connecteam (${{String(ctErrors[0]).slice(0, 200)}}), so the shifts were not checked today.`);
if (failed.length) problems.push(`:warning: ${{failed.length}} ClickUp → Connecteam run(s) failed in the last 24 hours.`);
if (missing.length) problems.push(`:warning: *${{missing.length}} upcoming move(s)/visit(s) have no shift in Connecteam:*\\n` +
  missing.slice(0, 15).map(({{ t, r }}) => `• <${{t.url}}|${{t.name.replace(/\\s+/g, ' ').slice(0, 60)}}> — ${{r.localStart}}`).join('\\n'));
if (unreadable.length) problems.push(`:grey_question: ${{unreadable.length}} card(s) have no readable date, so they can't be scheduled:\\n` +
  unreadable.slice(0, 10).map(t => `• <${{t.url}}|${{t.name.replace(/\\s+/g, ' ').slice(0, 60)}}>`).join('\\n'));

// the headline always says how the sync is doing; unreadable cards are a note, not a failure
const broken = problems.filter(x => !x.startsWith(':grey_question:'));
const head = broken.length
  ? `:warning: *ClickUp → Connecteam daily check: needs attention*`
  : `:white_check_mark: *ClickUp → Connecteam daily check:* ${{checked - missing.length}} of ${{checked}} upcoming booked moves and onsite visits have a shift, and the ClickUp connection is healthy.`;
const text = [head, ...problems].join('\\n\\n');
return [{{ json: {{ text }} }}];
"""

def http(name, pos, url, cred, generic=None, predefined=None, qs=None, paginate=False):
    p = {'method': 'GET', 'url': url, 'options': {}, 'executeOnce': True}
    if predefined:
        p['authentication'] = 'predefinedCredentialType'; p['nodeCredentialType'] = predefined
    if generic:
        p['authentication'] = 'genericCredentialType'; p['genericAuthType'] = generic
    if qs:
        p['sendQuery'] = True; p['queryParameters'] = {'parameters': [{'name': k, 'value': v} for k, v in qs]}
    if paginate:
        p['options']['pagination'] = {'pagination': {
            'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
            'paginationCompleteWhen': 'other',
            'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
            'limitPagesFetched': True, 'maxRequests': 10}}
    return {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
            'parameters': p, 'credentials': cred, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'}

nodes = [
    {'name': 'Every day 7:30 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 7, 'triggerAtMinute': 30}]}}},
    http('ClickUp webhooks', [220, 0], 'https://api.clickup.com/api/v2/team/9017263768/webhook', CRED_CLICKUP, predefined='clickUpApi'),
    # repeated statuses[] belong in the URL: n8n keeps only the last same-named query parameter
    http('Cards to check', [440, 0],
         f'https://api.clickup.com/api/v2/list/{LIST_ID}/task?include_markdown_description=true'
         '&statuses%5B%5D=moves%20booked&statuses%5B%5D=onsite%20estimate',
         CRED_CLICKUP, predefined='clickUpApi', paginate=True),
    # Connecteam allows at most 92 days per request: five 30-day windows (yesterday → ~5 months ahead)
    {'name': 'Windows', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [550, 0],
     'parameters': {'jsCode': "const now = Math.floor(Date.now() / 1000) - 86400, m = 30 * 86400;\n"
                              "return [0, 1, 2, 3, 4].map(i => ({ json: { from: now + i * m, to: now + (i + 1) * m } }));"}},
    {**http('Connecteam shifts', [660, 0], 'https://api.connecteam.com/scheduler/v1/schedulers/16427123/shifts', CRED_CONNECTEAM,
            generic='httpHeaderAuth',
            qs=[('startTime', '={{ $json.from }}'), ('endTime', '={{ $json.to }}'), ('limit', '500')])},
    http('Failed sync runs', [880, 0], f'https://n8n.expresshaulers.com/api/v1/executions?workflowId={SYNC_WF}&status=error&limit=50',
         CRED_N8N, generic='httpHeaderAuth'),
    {'name': 'Check everything', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1100, 0],
     'parameters': {'jsCode': AUDIT.strip()}},
    {'name': 'Post result', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1320, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                    'text': '={{ $json.text }}', 'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
]
one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
connections = {
    'Every day 7:30 AM': {'main': [one('ClickUp webhooks')]},
    'ClickUp webhooks': {'main': [one('Cards to check')]},
    'Cards to check': {'main': [one('Windows')]},
    'Windows': {'main': [one('Connecteam shifts')]},
    'Connecteam shifts': {'main': [one('Failed sync runs')]},
    'Failed sync runs': {'main': [one('Check everything')]},
    'Check everything': {'main': [one('Post result')]},
}
wf = {'name': '#9b Daily check: ClickUp → Connecteam sync', 'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York'}}
out = ROOT / 'workflows/sync-audit.json'
out.write_text(json.dumps(wf, indent=2))
print(out)
