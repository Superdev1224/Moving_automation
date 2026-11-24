#!/usr/bin/env python3
"""Builds workflows/unresponsive.json — leads who stopped answering (client's status rules).

Every day at 9:30 AM New York: 30 days of calls and texts are matched to the open lead cards. A lead
with 5 unanswered calls or 5 texts since they last replied gets a note on the card and appears in one
#sales list, so the team can try a different number or move them to Unresponsive Leads.
The automation suggests only: it never moves a card.

  python3 build/unresponsive.py          test mode  → #automation-errors, no card comments
  python3 build/unresponsive.py --live   live       → #sales + a note on each card
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C09D8S04H62' if LIVE else 'C0C2X8B9VS9'         # #daily-digest-and-updates / #automation-errors
LIST_ID = '901705949004'
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']
COMMENT_EVERY_DAYS = 14                                     # don't repeat the same note daily

LEAD_CARDS_URL = ('https://api.clickup.com/api/v2/list/901705949004/task?include_markdown_description=true'
                  '&statuses%5B%5D=new%20leads&statuses%5B%5D=opportunities'
                  '&statuses%5B%5D=scheduled%20follow%20ups&statuses%5B%5D=realtors%20w%20customers')

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/unresponsive.js').read_text())

LINES = LIB + """
// One item per company line, so each line's texts are read.
const token = $('RingCentral token').first().json.access_token;
const since = new Date(Date.now() - LOOK_BACK_DAYS * 86400000).toISOString();
return %EXT%.map(ext => ({ json: { ext, token, since } }));
"""

FIND = LIB + f"""
// Match 30 days of calls and texts to the open lead cards.
const state = $getWorkflowStaticData('global');
const noted = state.noted || {{}};
const calls = $('Call log').all().flatMap(i => i.json.records || []);
const texts = $('Read texts').all().flatMap(i => i.json.records || []);
const tasks = $('Open lead cards').all().flatMap(i => i.json.tasks || []);
const rows = unresponsiveLeads(tasks, chaseIndex(calls, texts));
const fresh = rows.filter(r => Date.now() - (noted[r.id] || 0) > {COMMENT_EVERY_DAYS} * 86400000);
for (const r of fresh) noted[r.id] = Date.now();
for (const [k, v] of Object.entries(noted)) if (Date.now() - v > 120 * 86400000) delete noted[k];
state.noted = noted;
if (!rows.length) return [];
const out = fresh.map(r => ({{ json: {{ ...r, comment: unresponsiveComment(r) }} }}));
out.push({{ json: {{ isReport: true, channel: '{CHANNEL}', report: unresponsiveSlack(rows) }} }});
return out;
"""


def rc_page(name, pos, url, qs, extra=None):
    p = {'method': 'GET', 'url': url, 'sendQuery': True,
         'queryParameters': {'parameters': [{'name': k, 'value': v} for k, v in qs]},
         'sendHeaders': True,
         'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.token || $json.access_token }}'}]},
         'options': {'pagination': {'pagination': {
             'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount + 1 }}'}]},
             'paginationCompleteWhen': 'other',
             'completeExpression': '={{ ($response.body.records || []).length < 1000 }}',
             'limitPagesFetched': True, 'maxRequests': 6}}}}
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos, 'parameters': p}
    n.update(extra or {})
    return n


nodes = [
    {'name': 'Every day 9:30 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 30}]}}},
    {'name': 'Open lead cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     # repeated statuses[] must live in the URL: n8n keeps only the last same-named query parameter
     'parameters': {'method': 'GET',
                    'url': LEAD_CARDS_URL,
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 10}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [440, 0],
     'executeOnce': True,
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    rc_page('Call log', [660, 0], 'https://platform.ringcentral.com/restapi/v1.0/account/~/call-log',
            [('view', 'Simple'), ('perPage', '1000'),
             ('dateFrom', '={{ new Date(Date.now() - 30 * 86400000).toISOString() }}')],
            {'executeOnce': True}),
    {'name': 'Each company line', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [880, 0],
     'parameters': {'jsCode': LINES.replace('%EXT%', json.dumps(EXTENSIONS)).strip()}},
    rc_page('Read texts', [1100, 0],
            '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
            [('messageType', 'SMS'), ('dateFrom', '={{ $json.since }}'), ('perPage', '1000')]),
    {'name': 'Who stopped answering', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1320, 0],
     'parameters': {'jsCode': FIND.strip()}},
    {'name': 'A card or the report?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1540, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isReport', 'leftValue': '={{ $json.isReport }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Post the list', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1760, -120],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.report }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'Note it on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 120],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.id }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
]

chain = ['Every day 9:30 AM', 'Open lead cards', 'RingCentral token', 'Call log', 'Each company line',
         'Read texts', 'Who stopped answering', 'A card or the report?']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(chain, chain[1:])}
connections['A card or the report?'] = {'main': [[{'node': 'Post the list', 'type': 'main', 'index': 0}],
                                                 [{'node': 'Note it on the card', 'type': 'main', 'index': 0}]]}

wf = {'name': '#26 Leads not responding' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/unresponsive.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
