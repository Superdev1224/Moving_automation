#!/usr/bin/env python3
"""Builds workflows/booked-note.json — "you're booked with us" (asked for on 2026-09-29).

Driven by Movegistics since 2026-10-27 ("the source of truth is when the Movegistics estimate is
signed and paid, or when it is changed but signed and paid"). Every 5 minutes: the recently updated
estimates. One that is Signed with its deposit paid is a booking, and the customer gets one short note
with the date and addresses FROM THAT ESTIMATE. A later signed-and-paid estimate for the same work
request with a different date sends an "updated" note. No arrival time is promised - the crew calls.
The ClickUp card is not used at all: its date was often stale (Bruce Pugh, 10/26).

The first run only records what is already signed and paid, so switching it on writes to nobody.

  python3 build/booked-note.py          approval mode: one preview posted, nothing sent
  python3 build/booked-note.py --live   live: the email really goes out
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C09D8S04H62' if LIVE else 'C0C2X8B9VS9'     # #daily-digest-and-updates / #automation-errors
ERRORS = 'C0C2X8B9VS9'
MG_URL = 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1'

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/booked-note.js').read_text())

PICK = LIB + """
// Which signed-and-paid estimates are new or changed? The first run only records the present.
const state = $getWorkflowStaticData('global');
state.est = state.est || {};
state.opp = state.opp || {};
const list = $input.first().json?.data?.list || [];
if (!state.floor) {
  state.floor = Date.now();
  for (const e of list.filter(isSignedAndPaid)) state.est[e._id] = { u: +e.updatedAt || 0, day: '' };
  return [];
}
return estimatesToCheck(list, state).map(e => ({ json: e }));
"""

WRITE = LIB + f"""
// Read each estimate's own date and addresses, then write the note (or say why not).
const state = $getWorkflowStaticData('global');
const picked = $('New or changed').all().map(i => i.json);
const notes = [], skipped = [];
$input.all().forEach((res, i) => {{
  const full = res.json?.data;
  if (!full) return;                                     // could not read it: tried again next run
  const r = noteFromEstimate(full, picked[i], state, Date.now());
  if (r.skip) skipped.push(r); else notes.push(r);
}});
const out = notes.map(m => ({{ json: {{ ...m, subject: BOOKED_SUBJECT(m), body: bookedEmail(m) }} }}));
const summary = bookedSummary(notes, skipped, {json.dumps(LIVE)});
if (summary) out.push({{ json: {{ isSummary: true, channel: '{CHANNEL}', summary }} }});
return out;
"""


def code(name, js, pos, each=False):
    p = {'jsCode': js.strip()}
    if each:
        p['mode'] = 'runOnceForEachItem'
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': p}


def mg_http(name, pos, method, url, body=None):
    p = {'method': method, 'url': url, 'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
         'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': '100821'}]},
         'options': {}}
    if body is not None:
        p.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    return {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos, 'parameters': p,
            'credentials': CRED_MG, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000,
            'alwaysOutputData': True, 'onError': 'continueRegularOutput'}


nodes = [
    {'name': 'Every 5 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 5}]}}},
    mg_http('Movegistics estimates', [220, 0], 'POST', f'{MG_URL}/estimate/list/',
            json.dumps({'filters': [], 'page': {'limit': 100, 'pageId': 0},
                        'sortBy': {'direction': 'DESC', 'property': 'updatedAt'}, 'timeZone': 'America/New_York'})),
    code('New or changed', PICK, [440, 0]),
    mg_http('Read the estimate', [660, 0], 'GET', f'={MG_URL}/estimate/{{{{ $json._id }}}}'),
    code('Write the note', WRITE, [880, 0]),
    {'name': 'Summary or a message?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1100, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isSummary', 'leftValue': '={{ $json.isSummary }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Post the summary', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1320, -140],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.summary }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

order = ['Every 5 minutes', 'Movegistics estimates', 'New or changed', 'Read the estimate', 'Write the note',
         'Summary or a message?']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}

if LIVE:
    nodes += [
        {'name': 'Send the note', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [1320, 80],
         'parameters': {'resource': 'message', 'operation': 'send',
                        'sendTo': '={{ $json.email }}', 'subject': '={{ $json.subject }}',
                        'emailType': 'text', 'message': '={{ $json.body }}',
                        'options': {'appendAttribution': False}},
         'credentials': CRED_GMAIL, 'onError': 'continueRegularOutput'},
        {'name': 'Did it send?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1540, 80],
         'parameters': {'conditions': {
             'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
             'combinator': 'and',
             'conditions': [{'id': 'failed', 'leftValue': '={{ !!$json.error }}', 'rightValue': '',
                             'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
             'options': {}}},
        {'name': 'Tell us it failed', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1760, 80],
         'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                        'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                        'text': "=:warning: Booking note did NOT send to {{ $('Write the note').item.json.name }}"
                                " ({{ $('Write the note').item.json.email }}, {{ $('Write the note').item.json.estimate }})",
                        'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
         'credentials': CRED_SLACK},
    ]
    connections['Summary or a message?'] = {'main': [
        [{'node': 'Post the summary', 'type': 'main', 'index': 0}],
        [{'node': 'Send the note', 'type': 'main', 'index': 0}]]}
    connections['Send the note'] = {'main': [[{'node': 'Did it send?', 'type': 'main', 'index': 0}]]}
    connections['Did it send?'] = {'main': [[{'node': 'Tell us it failed', 'type': 'main', 'index': 0}], []]}
else:
    connections['Summary or a message?'] = {'main': [[{'node': 'Post the summary', 'type': 'main', 'index': 0}], []]}

wf = {'name': '#25 Booking note when a job is booked' + ('' if LIVE else ' [APPROVAL MODE - nothing sent]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

(ROOT / 'workflows/booked-note.json').write_text(json.dumps(wf, indent=2))
print(ROOT / 'workflows/booked-note.json', 'LIVE' if LIVE else 'APPROVAL MODE')
