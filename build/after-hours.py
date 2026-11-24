#!/usr/bin/env python3
"""Builds workflows/after-hours.json — after-hours reply to new leads (Additional Integrations #9).

Every 10 minutes: new lead cards written by the lead automations - Yelp at any hour, website and
Thumbtack only when they arrived between 8 PM and 8 AM. If nobody has
answered the lead yet, they get the client's approved email (and a text when we have a mobile number).
Once per lead. Customers we are already talking to are never touched.

  python3 build/after-hours.py          test mode  → preview in #automation-errors, nothing sent
  python3 build/after-hours.py --live   live       → the email and text really go out
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                                     # #automation-errors
LIST_ID = '901705949004'                                   # All Leads
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']
SMS_FROM = '+18629304630'                                  # the line the team texts customers from
SMS_EXT = '607020048'                                      # ...and the extension that owns it

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/after-hours.js').read_text())

LEADS = LIB + """
// New lead cards that arrived while we were closed and have not been replied to yet.
const state = $getWorkflowStaticData('global');
const done = state.replied || {};
for (const [k, v] of Object.entries(done)) if (Date.now() - v > 30 * 86400000) delete done[k];
state.replied = done;
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const leads = afterHoursLeads(tasks, Date.now()).filter(l => !done[l.taskId]);
return leads.map(l => ({ json: l }));
"""

LINES = LIB + """
// One item per company line so each line's texts are read.
const token = $('RingCentral token').first().json.access_token;
const since = new Date(Date.now() - 24 * 3600000).toISOString();
return %EXT% .map(ext => ({ json: { ext, token, since } }));
"""

BACK = """
// Back to one item per lead for the Gmail check.
return $('New overnight leads').all().map(i => ({ json: i.json }));
"""

DECIDE = LIB + f"""
// Who still needs the after-hours reply? Build the exact messages the client approved.
const state = $getWorkflowStaticData('global');
state.replied = state.replied || {{}};
const leads = $('New overnight leads').all().map(i => i.json);
const calls = $('Call log').first().json.records || [];
const texts = $('Read texts').all().flatMap(i => i.json.records || []);
const sent = $input.all();
const out = [];
for (let i = 0; i < leads.length; i++) {{
  const l = leads[i];
  if (alreadyAnswered(l, calls, texts, (sent[i]?.json?.resultSizeEstimate) || 0)) {{
    state.replied[l.taskId] = Date.now();                 // someone got there first
    continue;
  }}
  if (!isClosed(Date.now())) continue;                    // we are open again: the team calls instead
  const sendEmail = !!l.email, sendText = l.phone.length === 10;
  if (!sendEmail && !sendText) continue;
  state.replied[l.taskId] = Date.now();
  out.push({{ json: {{ ...l, sendEmail, sendText,
    subject: EMAIL_SUBJECT, body: afterHoursEmail(l), sms: afterHoursText(l),
    smsFrom: '{SMS_FROM}', smsExt: '{SMS_EXT}',
    channel: '{ERRORS}', preview: afterHoursPreview(l, sendEmail, sendText)
      + `\\n>${{afterHoursEmail(l).replace(/\\n/g, '\\n>')}}`,
  }} }});
}}
return out;
"""


def code(name, js, pos, once=False):
    # NOTE: never set executeOnce on a Code node - it would drop every input item after the first,
    # and these nodes read $input.all(). Code nodes already run once for all items.
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
    {'name': 'Every 10 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '*/10 * * * *'}]}}},
    {'name': 'Cards created recently', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'date_created_gt', 'value': '={{ Date.now() - 12 * 3600000 }}'},
                        {'name': 'include_markdown_description', 'value': 'true'}]},
                    'options': {}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('New overnight leads', LEADS, [440, 0], once=True),
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
                        {'name': 'dateFrom', 'value': '={{ new Date(Date.now() - 24 * 3600000).toISOString() }}'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.access_token }}'}]},
                    'options': {}}},
    code('Each company line', LINES.replace('%EXT%', json.dumps(EXTENSIONS)), [1100, 0], once=True),
    {'name': 'Read texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 0],
     'parameters': {'method': 'GET',
                    'url': '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'messageType', 'value': 'SMS'}, {'name': 'direction', 'value': 'Outbound'},
                        {'name': 'dateFrom', 'value': '={{ $json.since }}'}, {'name': 'perPage', 'value': '500'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.token }}'}]},
                    'options': {}}},
    code('One item per lead', BACK, [1540, 0], once=True),
    {'name': 'Did we email them?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 0],
     'parameters': {'method': 'GET', 'url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'q', 'value': '=in:sent newer_than:1d to:{{ $json.email || "nobody@invalid.invalid" }}'},
                        {'name': 'maxResults', 'value': '2'}]},
                    'options': {}},
     'credentials': CRED_GMAIL, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Write the reply', DECIDE, [1980, 0], once=True),
]

if LIVE:
    nodes += [
        if_node('Has an email?', '={{ $json.sendEmail }}', [2200, -120]),
        {'name': 'Send the email', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [2420, -120],
         'parameters': {'resource': 'message', 'operation': 'send',
                        'sendTo': '={{ $json.email }}', 'subject': '={{ $json.subject }}',
                        'emailType': 'text', 'message': '={{ $json.body }}',
                        'options': {'appendAttribution': False}},
         'credentials': CRED_GMAIL, 'onError': 'continueRegularOutput'},
        if_node('Has a mobile?', '={{ $json.sendText }}', [2200, 120]),
        {'name': 'Send the text', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2420, 120],
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
else:
    nodes += [
        {'name': 'Preview in Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2200, 0],
         'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                        'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                        'text': '={{ $json.preview }}',
                        'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
         'credentials': CRED_SLACK},
    ]

chain = ['Every 10 minutes', 'Cards created recently', 'New overnight leads', 'RingCentral token',
         'Call log', 'Each company line', 'Read texts', 'One item per lead', 'Did we email them?', 'Write the reply']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(chain, chain[1:])}
if LIVE:
    connections['Write the reply'] = {'main': [[{'node': 'Has an email?', 'type': 'main', 'index': 0},
                                                {'node': 'Has a mobile?', 'type': 'main', 'index': 0}]]}
    connections['Has an email?'] = {'main': [[{'node': 'Send the email', 'type': 'main', 'index': 0}], []]}
    connections['Has a mobile?'] = {'main': [[{'node': 'Send the text', 'type': 'main', 'index': 0}], []]}
else:
    connections['Write the reply'] = {'main': [[{'node': 'Preview in Slack', 'type': 'main', 'index': 0}]]}

wf = {'name': '#23 After-hours reply to new leads' + ('' if LIVE else ' [TEST MODE - nothing sent]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/after-hours.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
