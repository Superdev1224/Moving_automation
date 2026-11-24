#!/usr/bin/env python3
"""Builds workflows/upsell.json (item #9).

Every 2 minutes: read outbound RingCentral texts from the 3 company lines, keep the ones that
look like an upsell offer, let Claude confirm, then move that customer's ClickUp card to
"upsell in progess" (ClickUp's own spelling).

  python3 build/upsell.py          test mode: reports to #automation-errors, never moves a card
  python3 build/upsell.py --live   moves the cards
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
strip = lambda s: re.sub(r"\nif \(typeof module.*\n?$", "\n", s)
LIB = strip((ROOT / 'lib/upsell.js').read_text()) + strip((ROOT / 'lib/booking.js').read_text())

LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'          # #automation-errors
LIST_ID = '901705949004'
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']   # lines 101, 102, 103, 104 (104 added 2026-11-19)
LOOKBACK_MIN = next((int(a.split('=')[1]) for a in sys.argv if a.startswith('--lookback-min=')), 20)  # duplicates are filtered out

CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}

RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

FANOUT = f"""
// One request per company line, carrying the access token we just fetched.
const token = $json.access_token;
const since = new Date(Date.now() - {LOOKBACK_MIN} * 60000).toISOString().replace(/\\.\\d+Z$/, 'Z');
return {json.dumps(EXTENSIONS)}.map(ext => ({{ json: {{ ext, token, since }} }}));
"""

FILTER = LIB + """
// Keep unseen outbound texts that look like an upsell offer.
const store = $getWorkflowStaticData('global');
store.seenMessages = store.seenMessages || {};
const now = Date.now();
const out = [];
for (const item of $input.all()) {
  for (const m of item.json.records || []) {
    const id = String(m.id);
    if (store.seenMessages[id]) continue;
    store.seenMessages[id] = now;                       // remember every message, upsell or not
    const text = m.subject || '';
    if (!isUpsellCandidate(text)) continue;
    out.push({ json: {
      messageId: id,
      to: (m.to && m.to[0] && m.to[0].phoneNumber) || '',
      sentAt: m.creationTime,
      text,
      needsAI: !isCertainUpsell(text),
      why: isCertainUpsell(text) ? 'standard upsell template' : '',
      claudeBody: claudeBody(text),
    } });
  }
}
for (const [k, t] of Object.entries(store.seenMessages)) if (now - t > 3 * 86400000) delete store.seenMessages[k];
return out;
"""

AFTER_AI = LIB + """
// Claude's verdict for each candidate it was asked about (same order as the requests).
const asked = $('Needs Claude?').all(0);
const items = $input.all();
const out = [];
for (let i = 0; i < items.length; i++) {
  const verdict = parseClaude(items[i].json);
  if (!verdict.upsell) continue;
  out.push({ json: { ...asked[i].json, why: verdict.why } });
}
return out;
"""

MATCH = LIB + f"""
// Find the customer's card by phone number and decide whether it needs moving.
const LIVE = {'true' if LIVE else 'false'};
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const out = [];
for (const item of $('Merge upsells').all()) {{
  const msg = item.json;
  const m = matchCard(tasks, {{ phone: msg.to, name: '', email: '' }});
  const status = (m && (m.task.status?.status || m.task.status) || '').toLowerCase();
  const already = status === UPSELL_STATUS || SKIP_STATUSES.includes(status);
  out.push({{ json: {{
    ...msg,
    taskId: m ? m.task.id : null,
    currentStatus: status || null,
    status: UPSELL_STATUS,
    skip: already,
    text: already
      ? `:information_source: *No change*: upsell text sent to ${{msg.to}}, card is already "${{status}}".`
      : (LIVE ? '' : ':test_tube: _TEST MODE, no card was changed_\\n') + upsellSlack(msg, m && m.task, msg.why),
    needsAlert: !m || already || !LIVE,
    canMove: !!m && !already,
  }} }});
}}
return out;
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
    {'name': 'Every 2 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 2}]}}},
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    code('Each company line', FANOUT, [440, 0]),
    {'name': 'Read sent texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [660, 0],
     'parameters': {'method': 'GET',
                    'url': '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
                    'sendQuery': True,
                    'queryParameters': {'parameters': [
                        {'name': 'messageType', 'value': 'SMS'},
                        {'name': 'direction', 'value': 'Outbound'},
                        {'name': 'dateFrom', 'value': '={{ $json.since }}'},
                        {'name': 'perPage', 'value': '100'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.token }}'}]},
                    'options': {}}},
    code('Keep upsell candidates', FILTER, [880, 0]),
    if_node('Needs Claude?', '={{ $json.needsAI }}', [1100, 0]),
    {'name': 'Ask Claude', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, -100],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.claudeBody) }}', 'options': {}},
     'credentials': CRED_ANTHROPIC},
    code('Claude said upsell?', AFTER_AI, [1540, -100]),
    {'name': 'Merge upsells', 'type': 'n8n-nodes-base.merge', 'typeVersion': 3, 'position': [1760, 20],
     'parameters': {'numberInputs': 2}},
    {'name': 'Get all cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1980, 20],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True,
                    'queryParameters': {'parameters': [{'name': 'include_markdown_description', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 25}}},
                    'executeOnce': True},
     'credentials': CRED_CLICKUP},
    code('Match customer card', MATCH, [2200, 20]),
    if_node('Report it?', '={{ $json.needsAlert }}', [2420, 20]),
    if_node('Move the card?', '={{ $json.canMove }}', [2640, 120]),
    {'name': 'Move to Upsell In Progess', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2860, 60],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.taskId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status: $json.status }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE},
    {'name': 'Report to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2640, -80],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                    'text': '={{ $json.text }}', 'otherOptions': {'includeLinkToWorkflow': False}},
     'credentials': CRED_SLACK},
]

one = lambda n, i=0: [{'node': n, 'type': 'main', 'index': i}]
connections = {
    'Every 2 minutes': {'main': [one('RingCentral token')]},
    'RingCentral token': {'main': [one('Each company line')]},
    'Each company line': {'main': [one('Read sent texts')]},
    'Read sent texts': {'main': [one('Keep upsell candidates')]},
    'Keep upsell candidates': {'main': [one('Needs Claude?')]},
    'Needs Claude?': {'main': [one('Ask Claude'), one('Merge upsells', 1)]},
    'Ask Claude': {'main': [one('Claude said upsell?')]},
    'Claude said upsell?': {'main': [one('Merge upsells')]},
    'Merge upsells': {'main': [one('Get all cards')]},
    'Get all cards': {'main': [one('Match customer card')]},
    'Match customer card': {'main': [one('Report it?')]},
    'Report it?': {'main': [one('Report to Slack'), one('Move the card?')]},
    'Move the card?': {'main': [one('Move to Upsell In Progess'), []]},
}

wf = {'name': '#9 Upsell text → ClickUp "Upsell In Progess"' + ('' if LIVE else ' [TEST MODE - no card changes]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/upsell.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
