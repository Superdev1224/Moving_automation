#!/usr/bin/env python3
"""Builds workflows/pre-move.json — confirmation 24 h before the move (Additional Integrations #11).

Every day at 9 AM: tomorrow's jobs, read from the Connecteam schedule. The crews work from that
schedule, so a move exists when it is on it — the ClickUp board often lags behind (6 shifts for
2026-09-29 against 4 booked cards) and the cards frequently have no start time, while every shift
does. The whole card is pasted into the shift note, so the email, phone and both addresses come
from the shift itself; the ClickUp card is matched to it for the link and for anything missing.

  python3 build/pre-move.py            approval mode: one summary posted, nothing sent
  python3 build/pre-move.py --live     live: the email and text really go out
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
APPROVALS = 'C09D8S04H62'              # #daily-digest-and-updates
LIST_ID = '901705949004'               # All Leads
SCHEDULER_ID = 16427123                # the crew schedule in Connecteam
SMS_FROM = '+18629304630'
SMS_EXT = '607020048'

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = (strip((ROOT / 'lib/parse-card.js').read_text())
       + strip((ROOT / 'lib/board.js').read_text())
       + strip((ROOT / 'lib/pre-move.js').read_text()))

WINDOW = LIB + f"""
// Midnight to midnight tomorrow, New York, as Connecteam wants it (epoch seconds).
const dayKey = nyDay(Date.now() + 86400000);
const [y, m, d] = dayKey.split('-').map(Number);
const windowStart = toEpoch(y, m, d, 0, 0);
return [{{ json: {{ dayKey, schedulerId: {SCHEDULER_ID}, windowStart, windowEnd: windowStart + 86400 }} }}];
"""

MOVES = LIB + """
// One move per job on tomorrow's schedule, matched back to its ClickUp card.
const shifts = $('Tomorrow on the schedule').first().json.data?.shifts
  || $('Connecteam shifts').first().json.data?.shifts || [];
// All Leads plus the influencer board: a booked influencer waits there in "influencers content wait"
const cards = [...$('Cards on the board').all(), ...$input.all()].flatMap(i => i.json.tasks || []);
const dayKey = $('Tomorrow on the schedule').first().json.dayKey;
return movesFromShifts(shifts, dayKey, cards, Date.now()).map(m => ({ json: m }));
"""

DECIDE = LIB + f"""
// Skip anyone the team has already been in touch with today, then write the messages.
const state = $getWorkflowStaticData('global');
const done = state.confirmed || {{}};
for (const [k, v] of Object.entries(done)) if (Date.now() - v > 30 * 86400000) delete done[k];
const moves = $('Moves for tomorrow').all().map(i => i.json);
const texts = $('Read texts').all().flatMap(i => i.json.records || []);
const mail = $input.all();
const out = [], skipped = [], handled = [];
for (let i = 0; i < moves.length; i++) {{
  const m = moves[i];
  if (done[m.key]) continue;
  if (m.skipReason) {{ skipped.push(m); continue; }}      // no way to reach them: the team is told
  if (confirmedByHand(m, texts, (mail[i]?.json?.resultSizeEstimate) || 0)) {{ done[m.key] = Date.now(); handled.push(m); continue; }}
  const sendEmail = !!m.email, sendText = m.phone.length === 10;
  done[m.key] = Date.now();
  out.push({{ json: {{ ...m, sendEmail, sendText,
    subject: PRE_MOVE_SUBJECT(m), body: preMoveEmail(m), sms: preMoveText(m), smsFrom: '{SMS_FROM}' }} }});
}}
state.confirmed = done;
if (!out.length && !skipped.length && !handled.length) return [];
out.push({{ json: {{ isSummary: true, channel: '{APPROVALS}',
  summary: preMoveSummary(out.map(o => o.json), {json.dumps(LIVE)}, skipped, handled) }} }});
return out;
"""


def code(name, js, pos):
    # never set executeOnce on a Code node - it drops every input item after the first
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
    {'name': 'Every day 9 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 0}]}}},
    code('Tomorrow on the schedule', WINDOW, [220, 0]),
    {'name': 'Connecteam shifts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [440, 0],
     'parameters': {'method': 'GET',
                    'url': '=https://api.connecteam.com/scheduler/v1/schedulers/{{ $json.schedulerId }}/shifts'
                           '?startTime={{ $json.windowStart }}&endTime={{ $json.windowEnd }}&limit=500',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'options': {}},
     'credentials': CRED_CONNECTEAM},
    # the whole board, so a shift can be matched to its card whatever status the card sits in
    {'name': 'Cards on the board', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [660, 0],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 50}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Influencer board', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [770, 0],
     'parameters': {'method': 'GET', 'url': 'https://api.clickup.com/api/v2/list/901705958022/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 10}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput', 'executeOnce': True},
    code('Moves for tomorrow', MOVES, [880, 0]),
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, 0],
     'executeOnce': True,
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    {'name': 'Read texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET',
                    'url': f'https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{SMS_EXT}/message-store',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'messageType', 'value': 'SMS'}, {'name': 'direction', 'value': 'Outbound'},
                        {'name': 'dateFrom', 'value': '={{ new Date(Date.now() - 24 * 3600000).toISOString() }}'},
                        {'name': 'perPage', 'value': '500'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.access_token }}'}]},
                    'options': {}}},
    code('One item per move', "return $('Moves for tomorrow').all().map(i => ({ json: i.json }));", [1540, 0]),
    {'name': 'Did we email them?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 0],
     'parameters': {'method': 'GET', 'url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'q', 'value': '=in:sent newer_than:1d to:{{ $json.email || "nobody@invalid.invalid" }}'},
                        {'name': 'maxResults', 'value': '2'}]},
                    'options': {}},
     'credentials': CRED_GMAIL, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Write the confirmation', DECIDE, [1980, 0]),
    {'name': 'Summary or a message?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [2200, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isSummary', 'leftValue': '={{ $json.isSummary }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Post the summary', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2420, -140],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.summary }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

order = ['Every day 9 AM', 'Tomorrow on the schedule', 'Connecteam shifts', 'Cards on the board',
         'Influencer board', 'Moves for tomorrow', 'RingCentral token', 'Read texts', 'One item per move',
         'Did we email them?', 'Write the confirmation', 'Summary or a message?']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}

if LIVE:
    nodes += [
        if_node('Has an email?', '={{ $json.sendEmail }}', [2420, 60]),
        {'name': 'Send the email', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [2640, 60],
         'parameters': {'resource': 'message', 'operation': 'send',
                        'sendTo': '={{ $json.email }}', 'subject': '={{ $json.subject }}',
                        'emailType': 'text', 'message': '={{ $json.body }}',
                        'options': {'appendAttribution': False}},
         'credentials': CRED_GMAIL, 'onError': 'continueRegularOutput'},
        if_node('Has a mobile?', '={{ $json.sendText }}', [2420, 220]),
        {'name': 'Send the text', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2640, 220],
         'parameters': {'method': 'POST',
                        'url': f'https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{SMS_EXT}/sms',
                        'sendHeaders': True,
                        'headerParameters': {'parameters': [
                            {'name': 'Authorization', 'value': "=Bearer {{ $('RingCentral token').first().json.access_token }}"}]},
                        'sendBody': True, 'specifyBody': 'json',
                        'jsonBody': '={{ JSON.stringify({ from: { phoneNumber: $json.smsFrom },'
                                    ' to: [{ phoneNumber: "+1" + $json.phone }], text: $json.sms }) }}',
                        'options': {}},
         'onError': 'continueRegularOutput'},
    ]
    connections['Summary or a message?'] = {'main': [
        [{'node': 'Post the summary', 'type': 'main', 'index': 0}],
        [{'node': 'Has an email?', 'type': 'main', 'index': 0}, {'node': 'Has a mobile?', 'type': 'main', 'index': 0}]]}
    connections['Has an email?'] = {'main': [[{'node': 'Send the email', 'type': 'main', 'index': 0}], []]}
    connections['Has a mobile?'] = {'main': [[{'node': 'Send the text', 'type': 'main', 'index': 0}], []]}
else:
    connections['Summary or a message?'] = {'main': [[{'node': 'Post the summary', 'type': 'main', 'index': 0}], []]}

wf = {'name': '#24 Move confirmation 24 h before' + ('' if LIVE else ' [APPROVAL MODE - nothing sent]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

(ROOT / 'workflows/pre-move.json').write_text(json.dumps(wf, indent=2))
print(ROOT / 'workflows/pre-move.json', 'LIVE' if LIVE else 'APPROVAL MODE')
