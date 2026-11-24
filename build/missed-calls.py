#!/usr/bin/env python3
"""Builds workflows/missed-calls.json — missed call rescue (Additional Integrations #8).

Every 2 minutes: RingCentral call log (last 14 h) + outbound texts. A customer's missed call or
voicemail with no call or text back within 5 minutes → one alert with their ClickUp card.
Business hours 8 AM - 8 PM New York; overnight calls still unanswered are alerted at 8 AM.

  python3 build/missed-calls.py          test mode  → #automation-errors
  python3 build/missed-calls.py --live   live       → #sales
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C08PL61A32L' if LIVE else 'C0C2X8B9VS9'    # #sales / #automation-errors
# missed calls stay in #sales: a caller who is not rung back goes to another mover, and after the
# spam-ring and in-progress fixes this is only about one alert a day.
LIST_ID = '901705949004'                              # All Leads
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']
OUR_NUMBERS = ['+18623799113', '+12012552545', '+18629304630', '+18623329066', '+18629999390']

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/missed-calls.js').read_text()) + strip((ROOT / 'lib/booking.js').read_text())

LINES = """
// One item per company line, so each line's texts are read.
const token = $('RingCentral token').first().json.access_token;
const since = new Date(Date.now() - LOOKBACK_H * 3600000).toISOString();
return %EXT%.map(ext => ({ json: { ext, token, since } }));
"""

FIND = LIB + """
// Which missed calls still have no call or text back? Remember each call once (staticData).
const state = $getWorkflowStaticData('global');
const seen = pruneSeen(state.seen || {}, Date.now());
const seenNumbers = pruneSeen(state.seenNumbers || {}, Date.now());
const calls = $('Call log').first().json.records || [];
const texts = $('Read texts').all().flatMap(i => i.json.records || []);
const staffPhones = ($('Connecteam staff').first().json.data?.users || []).filter(u => !u.isArchived).map(u => u.phoneNumber);
const activeNumbers = ($('Calls happening right now').first().json.records || [])
  .flatMap(c => [c.from?.phoneNumber, c.to?.phoneNumber]).filter(Boolean);
const { due, handled } = findUnanswered({ calls, texts, staffPhones, ourNumbers: %OURS%, activeNumbers, seen, seenNumbers, nowMs: Date.now() });
for (const id of handled) seen[id] = Date.now();
for (const d of due) {
  for (const c of d.calls) seen[c.id] = Date.now();
  seenNumbers[d.number] = Date.now();            // one alert per number per day
}
state.seen = seen;
state.seenNumbers = seenNumbers;
return due.map(d => ({ json: d }));
"""

ALERT = LIB + """
// Match each caller to their ClickUp card by phone number and write the alert.
const tasks = $input.all().flatMap(i => i.json.tasks || []);
return $('Find unanswered').all().map(item => {
  const d = item.json;
  const match = matchCard(tasks, { phone: d.number });
  return { json: { channel: '%CHANNEL%', number: d.number, text: missedCallSlack(d, match, Date.now()) } };
});
"""

NOT_POSTED = """
// Last check before posting: has this number already had an alert in the last 24 hours? Two runs can
// overlap (looking up the card takes ~2 minutes and the check runs every 2), and the second one does
// not know what the first just sent - Annemarie Dorazio got two alerts 2 minutes apart on 2026-10-02.
const recent = $input.all().map(i => i.json)
  .filter(m => /Missed call/.test(m.text || '') && Date.now() / 1000 - +m.ts < 24 * 3600)
  .map(m => String(m.text || '').replace(/\\D/g, ''));
const out = [], sent = new Set();
for (const item of $('Write the alert').all()) {
  const n = item.json.number;
  if (!n || sent.has(n) || recent.some(d => d.includes(n))) continue;
  sent.add(n);
  out.push({ json: item.json });
}
return out;
"""


def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': {'jsCode': js.strip()}}


def rc_get(name, pos, url, qs, once=True, token='={{ $json.token || $json.access_token }}'):
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
         'parameters': {'method': 'GET', 'url': url, 'sendQuery': True,
                        'queryParameters': {'parameters': [{'name': k, 'value': v} for k, v in qs]},
                        'sendHeaders': True,
                        'headerParameters': {'parameters': [{'name': 'Authorization', 'value': 'Bearer ' + token if not token.startswith('=') else '=Bearer ' + token[1:]}]},
                        'options': {}}}
    if once:
        n['executeOnce'] = True
    return n


def with_retry(nodes, names):
    # a passing ClickUp/API hiccup is retried before anything decides a card does not exist
    for n in nodes:
        if n['name'] in names:
            n.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000})
    return nodes

nodes = [
    {'name': 'Every 2 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 2}]}}},
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [200, 0],
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    rc_get('Call log', [400, 0], 'https://platform.ringcentral.com/restapi/v1.0/account/~/call-log',
           [('view', 'Detailed'), ('perPage', '1000'),
            ('dateFrom', '={{ new Date(Date.now() - 14 * 3600000).toISOString() }}')]),
    {'name': 'Connecteam staff', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [600, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': 'https://api.connecteam.com/users/v1/users',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendQuery': True, 'queryParameters': {'parameters': [{'name': 'limit', 'value': '500'}]},
                    'options': {}},
     'credentials': CRED_CONNECTEAM},
    code('Each company line', LIB + LINES.replace('%EXT%', json.dumps(EXTENSIONS)), [800, 0]),
    rc_get('Read texts', [1000, 0], '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
           [('messageType', 'SMS'), ('direction', 'Outbound'), ('dateFrom', '={{ $json.since }}'), ('perPage', '1000')],
           once=False),
    # a call that is ringing or in progress still reads as "Missed" in the log, so check live calls
    # after "Read texts" $json has no token (every run failed with "Invalid Authorization header"
    # from 2026-09-24 to 2026-09-30), so take it from the token step
    rc_get('Calls happening right now', [1150, 0],
           'https://platform.ringcentral.com/restapi/v1.0/account/~/active-calls', [('perPage', '100')],
           token="={{ $('RingCentral token').first().json.access_token }}"),
    code('Find unanswered', FIND.replace('%OURS%', json.dumps(OUR_NUMBERS)), [1250, 0]),
    {'name': 'Their ClickUp cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1400, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 50}}}},   # ~3,000 cards = 31 pages
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Write the alert', ALERT.replace('%CHANNEL%', CHANNEL), [1600, 0]),
    {'name': 'Alerts already sent', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1700, 0],
     'executeOnce': True,
     'parameters': {'authentication': 'accessToken', 'resource': 'channel', 'operation': 'history',
                    'channelId': {'__rl': True, 'value': CHANNEL, 'mode': 'id'},
                    'returnAll': False, 'limit': 100, 'filters': {}},
     'credentials': CRED_SLACK, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Not posted yet?', NOT_POSTED, [1750, 0]),
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1800, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
]

nodes = with_retry(nodes, ['Their ClickUp cards'])
order = [n['name'] for n in nodes]
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}

wf = {'name': '#18 Missed call rescue' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none' if LIVE else 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/missed-calls.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
