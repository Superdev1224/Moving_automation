#!/usr/bin/env python3
"""Builds workflows/estimate-followups.json — estimates with no reply (Additional Integrations #6).

Every morning at 8:05 (after the digest): Movegistics estimates sent more than 24 h ago that are
still unsigned, where the customer has not called, texted or emailed since. One list in #sales.

  python3 build/estimate-followups.py          test mode  → #automation-errors
  python3 build/estimate-followups.py --live   live       → #sales
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C08PL61A32L' if LIVE else 'C0C2X8B9VS9'         # #sales / #automation-errors
LIST_ID = '901705949004'                                   # All Leads
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = (strip((ROOT / 'lib/estimate-followups.js').read_text())
       + strip((ROOT / 'lib/estimates.js').read_text())
       + strip((ROOT / 'lib/booking.js').read_text()))

QUIET = LIB + """
// Estimates that have been quiet for more than a day.
const rows = $input.first().json.data?.list || [];
const quiet = quietEstimates(rows, Date.now());
return quiet.map(q => ({ json: q }));
"""

LINES = LIB + """
// One item per company line, so each line's texts are read.
const token = $('RingCentral token').first().json.access_token;
const since = new Date(Date.now() - CHASE_DAYS * 86400000).toISOString();
return %EXT%.map(ext => ({ json: { ext, token, since } }));
"""

BACK = """
// Back to one item per estimate for the Gmail check.
return $('Quiet estimates').all().map(i => ({ json: i.json }));
"""

DECIDE = LIB + f"""
// Who never came back? Match each one to their ClickUp card and write the morning list.
const rows = $('Quiet estimates').all().map(i => i.json);
const calls = $('Call log').first().json.records || [];
const texts = $('Read texts').all().flatMap(i => i.json.records || []);
const mail = $('Did they email us?').all();
const tasks = $('Their ClickUp cards').all().flatMap(i => i.json.tasks || []);
const now = Date.now();
// Already booked (or lost) customers are not chased, and one line per customer: the newest estimate.
const DONE = ['moves booked', 'moves complete', 'moves complete not paid', 'moves complete + paid',
              'moves lost', 'upsold customers', 'upsell in progess'];
const best = new Map();
for (let i = 0; i < rows.length; i++) {{
  const r = rows[i];
  if (heardBack(r, calls, texts, (mail[i]?.json?.resultSizeEstimate) || 0)) continue;
  const card = matchCard(tasks, {{ email: r.email, phone: r.phone, name: r.name }});
  const status = String(card?.task?.status?.status || '').toLowerCase();
  if (DONE.includes(status)) continue;
  const key = (r.name || '').toLowerCase().trim() || r.email || r.phone;   // same person, several estimates
  const row = {{ ...r, cardUrl: card ? card.task.url : '' }};
  if (!best.has(key) || best.get(key).sentAt < r.sentAt) best.set(key, row);
}}
const quiet = [...best.values()];
if (!quiet.length) return [];
return [{{ json: {{ channel: '{CHANNEL}', text: quietEstimateSlack(quiet, now), count: quiet.length }} }}];
"""


def code(name, js, pos, once=False):
    # never set executeOnce on a Code node: it drops every input item after the first,
    # and these nodes read $input.all(). Code nodes already run once for all items.
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}


nodes = [
    {'name': 'Every day 8:05 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 8, 'triggerAtMinute': 5}]}}},
    {'name': 'Movegistics estimates', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'POST', 'url': 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1/estimate/list/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': '100821'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': json.dumps({'filters': [], 'page': {'limit': 100, 'pageId': 0},
                                            'sortBy': {'direction': 'DESC', 'property': 'updatedAt'},
                                            'timeZone': 'America/New_York'}),
                    'options': {}},
     'credentials': CRED_MG},
    code('Quiet estimates', QUIET, [440, 0], once=True),
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [660, 0],
     'executeOnce': True,
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    {'name': 'Call log', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [880, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': 'https://platform.ringcentral.com/restapi/v1.0/account/~/call-log',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'view', 'value': 'Simple'}, {'name': 'perPage', 'value': '1000'},
                        {'name': 'dateFrom', 'value': '={{ new Date(Date.now() - 7 * 86400000).toISOString() }}'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.access_token }}'}]},
                    'options': {}}},
    code('Each company line', LINES.replace('%EXT%', json.dumps(EXTENSIONS)), [1100, 0], once=True),
    {'name': 'Read texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 0],
     'parameters': {'method': 'GET',
                    'url': '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'messageType', 'value': 'SMS'}, {'name': 'dateFrom', 'value': '={{ $json.since }}'},
                        {'name': 'perPage', 'value': '1000'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.token }}'}]},
                    'options': {}}},
    code('One item per estimate', BACK, [1540, 0], once=True),
    {'name': 'Did they email us?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 0],
     'parameters': {'method': 'GET', 'url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'q', 'value': '=newer_than:7d -in:sent from:{{ $json.email || "nobody@invalid.invalid" }}'},
                        {'name': 'maxResults', 'value': '2'}]},
                    'options': {}},
     'credentials': CRED_GMAIL, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Their ClickUp cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1980, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 50}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Still no reply', DECIDE, [2200, 0], once=True),
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2420, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

order = [n['name'] for n in nodes]
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}

wf = {'name': '#22 Estimates with no reply' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/estimate-followups.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
