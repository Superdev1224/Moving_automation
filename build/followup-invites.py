#!/usr/bin/env python3
"""Builds workflows/followup-invites.json — #29 Customer Follow-Up Scheduler (2026-11-16).

Client PDF "AI Automations", item 2; replaces the client's other AI tool (10/6-11/15). Every 5 minutes:
the All Leads cards in "Scheduled Follow Ups" + contact@'s main calendar. A card that ENTERS the status
(or whose date/time on the card changes) gets a Google Calendar invite with the customer as a guest
(rules in lib/followup-invites.js), and #sales gets a one-line note. Cards already sitting there when
it was first switched on are only recorded (no burst of invites for old cards).

  python3 build/followup-invites.py          preview: notes in #sales say what it WOULD book; no calendar change
  python3 build/followup-invites.py --live   live: creates/moves the calendar invites (customers get them)
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
SALES = 'C08PL61A32L'                                  # #sales
ERRORS = 'C0C2X8B9VS9'                                 # #automation-errors
CAL = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
CRED_GOOGLE = {'googleOAuth2Api': {'id': (ROOT / 'workflows/google-cal-drive.credential').read_text().strip(),
                                   'name': 'Google - Drive read + Calendar (contact@)'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = ''.join(strip((ROOT / f'lib/{f}.js').read_text()) + '\n' for f in ('parse-card', 'followups', 'followup-invites'))

PLAN = LIB + f"""
// What to book. The first run only records the cards already there.
const LIVE = {json.dumps(LIVE)};
const state = $getWorkflowStaticData('global');
const tasks = $('Follow-up cards').all().flatMap(i => i.json.tasks || []);
const cal = $input.first().json;
if (!Array.isArray(cal.items)) return [{{ json: {{ note: true, channel: '{ERRORS}', text: `:warning: Follow-up invites: could not read the Google Calendar (${{cal.error?.message || 'no events list'}}). Nothing was booked.` }} }}];
const now = Date.now();
if (!state.baseline) {{
  state.baseline = {{}};
  for (const a of planInvites(tasks, cal.items, {{}}, now)) state.baseline[a.cardId] = a.sig;
  state.startedAt = new Date(now).toISOString();
  return [];
}}
const key = LIVE ? 'live' : 'preview';
const seen = state[key] = state[key] || {{ ...state.baseline }};
const out = [];
for (const a of planInvites(tasks, cal.items, seen, now)) {{
  if (a.action === 'already') {{ seen[a.cardId] = a.sig; continue; }}
  if (a.action === 'skip') {{
    seen[a.cardId] = a.sig;
    out.push({{ json: {{ note: true, channel: '{SALES}', text: `:grey_question: No follow-up invite for *${{a.name}}*: ${{a.reason}} (date on the card). <${{a.url}}|ClickUp>` }} }});
    continue;
  }}
  if (!LIVE) {{
    seen[a.cardId] = a.sig;
    out.push({{ json: {{ note: true, channel: '{SALES}', text: fiNote(a, false) + ' _(preview - nothing sent yet)_' }} }});
    continue;
  }}
  const move = a.action === 'move';
  out.push({{ json: {{ ...a, method: move ? 'PATCH' : 'POST',
    url: '{CAL}' + (move ? '/' + encodeURIComponent(a.eventId) : '') + '?sendUpdates=all',
    payload: move ? {{ start: a.body.start, end: a.body.end, extendedProperties: a.body.extendedProperties }} : a.body }} }});
}}
// tidy: forget cards that left Scheduled Follow Ups
const here = new Set(tasks.map(t => t.id));
for (const k of Object.keys(seen)) if (!here.has(k)) delete seen[k];
return out;
"""

RECORD = f"""
// Remember what was booked; say so in #sales. A failure is reported once and tried again next run.
const state = $getWorkflowStaticData('global');
const seen = state.live = state.live || {{}};
state.failedSaid = state.failedSaid || {{}};
const planned = $('Calendar change?').all().map(i => i.json);
const out = [];
$input.all().forEach((res, i) => {{
  const a = planned[i];
  if (!a) return;
  const ok = res.json && res.json.id && !res.json.error;
  if (ok) {{
    seen[a.cardId] = a.sig;
    out.push({{ json: {{ channel: '{SALES}', text: fiNote(a, true) }} }});
  }} else if (!state.failedSaid[a.cardId + a.sig]) {{
    state.failedSaid[a.cardId + a.sig] = Date.now();
    out.push({{ json: {{ channel: '{ERRORS}', text: `:warning: Follow-up invite FAILED for ${{a.name}} (${{a.url}}): ${{res.json?.error?.message || JSON.stringify(res.json).slice(0, 200)}}. Will keep trying every 5 minutes.` }} }});
  }}
}});
for (const [k, v] of Object.entries(state.failedSaid)) if (Date.now() - v > 7 * 86400000) delete state.failedSaid[k];
return out;
"""


def if_node(name, left, pos):
    return {'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': pos,
            'parameters': {'conditions': {
                'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                'combinator': 'and',
                'conditions': [{'id': name, 'leftValue': left, 'rightValue': '',
                                'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                'options': {}}}


def slack(name, pos):
    return {'name': name, 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': pos,
            'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                           'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                           'text': '={{ $json.text }}',
                           'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
            'credentials': CRED_SLACK}


nodes = [
    {'name': 'Every 5 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 5}]}}},
    {'name': 'Follow-up cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'GET', 'url': 'https://api.clickup.com/api/v2/list/901705949004/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'statuses[]', 'value': 'scheduled follow ups'},
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'subtasks', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other', 'completeExpression': '={{ $response.body.last_page !== false }}',
                        'limitPagesFetched': True, 'maxRequests': 10}}}},
     'credentials': CRED_CLICKUP, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000},
    {'name': 'Calendar', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [440, 0],
     'parameters': {'method': 'GET', 'url': CAL,
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'googleOAuth2Api',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'timeMin', 'value': '={{ new Date(Date.now() - 86400000).toISOString() }}'},
                        {'name': 'timeMax', 'value': '={{ new Date(Date.now() + 500 * 86400000).toISOString() }}'},
                        {'name': 'singleEvents', 'value': 'true'},
                        {'name': 'maxResults', 'value': '2500'}]},
                    'options': {}},
     'credentials': CRED_GOOGLE, 'executeOnce': True, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000,
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Plan', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [660, 0],
     'parameters': {'jsCode': PLAN.strip()}},
    if_node('Calendar change?', '={{ !$json.note }}', [880, 0]),
    slack('Post note', [1540, 120]),
]
connections = {
    'Every 5 minutes': {'main': [[{'node': 'Follow-up cards', 'type': 'main', 'index': 0}]]},
    'Follow-up cards': {'main': [[{'node': 'Calendar', 'type': 'main', 'index': 0}]]},
    'Calendar': {'main': [[{'node': 'Plan', 'type': 'main', 'index': 0}]]},
    'Plan': {'main': [[{'node': 'Calendar change?', 'type': 'main', 'index': 0}]]},
    'Calendar change?': {'main': [[], [{'node': 'Post note', 'type': 'main', 'index': 0}]]},
}
if LIVE:
    nodes += [
        {'name': 'Send the invite', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, -100],
         'parameters': {'method': '={{ $json.method }}', 'url': '={{ $json.url }}',
                        'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'googleOAuth2Api',
                        'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.payload) }}',
                        'options': {}},
         'credentials': CRED_GOOGLE, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 5000,
         'onError': 'continueRegularOutput'},
        {'name': 'Record', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1320, -100],
         'parameters': {'jsCode': (LIB + RECORD).strip()}},
    ]
    connections['Calendar change?']['main'][0] = [{'node': 'Send the invite', 'type': 'main', 'index': 0}]
    connections['Send the invite'] = {'main': [[{'node': 'Record', 'type': 'main', 'index': 0}]]}
    connections['Record'] = {'main': [[{'node': 'Post note', 'type': 'main', 'index': 0}]]}

wf = {'name': '#29 Follow-up calendar invites' + ('' if LIVE else ' [PREVIEW - nothing sent]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all'}}
out = ROOT / 'workflows/followup-invites.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'PREVIEW')
