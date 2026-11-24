#!/usr/bin/env python3
"""Builds workflows/stale-leads.json — new lead with nobody on it yet (Additional Integrations #5).

Every 5 minutes: ClickUp cards created in the last 6 hours. If nobody has called, texted or emailed
the lead within 10 minutes, one alert goes to #sales. Business hours 8 AM - 8 PM New York; leads that
arrive overnight are alerted at 8 AM.

  python3 build/stale-leads.py          test mode  → #automation-errors
  python3 build/stale-leads.py --live   live       → #sales
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C09D8S04H62' if LIVE else 'C0C2X8B9VS9'         # #daily-digest-and-updates / #automation-errors
LIST_ID = '901705949004'                                   # All Leads
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/stale-leads.js').read_text()) + strip((ROOT / 'lib/missed-calls.js').read_text())

LEADS = LIB + """
// New cards that still need a first touch (already-alerted ones are remembered).
const state = $getWorkflowStaticData('global');
const seen = state.alerted || {};
for (const [k, v] of Object.entries(seen)) if (Date.now() - v > 7 * 86400000) delete seen[k];
state.alerted = seen;
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const leads = newLeads(tasks, Date.now()).filter(l => !seen[l.taskId]);
return leads.map(l => ({ json: l }));
"""

LINES = LIB + """
// One item per company line so each line's texts are read (the leads are read again later).
const token = $('RingCentral token').first().json.access_token;
const since = new Date(Date.now() - (LEAD_LOOKBACK_H + CONTACT_BEFORE_H) * 3600000).toISOString();
return %EXT%.map(ext => ({ json: { ext, token, since } }));
"""

BACK_TO_LEADS = """
// Back to one item per lead, so the Gmail check below runs once per lead.
return $('New leads').all().map(i => ({ json: i.json }));
"""

DECIDE = LIB + f"""
// Who has still not been contacted? One alert per lead, business hours only.
const state = $getWorkflowStaticData('global');
state.alerted = state.alerted || {{}};
const leads = $('New leads').all().map(i => i.json);
const calls = $('Call log').first().json.records || [];
const activeNumbers = ($('Calls happening right now').first()?.json?.records || [])
  .flatMap(c => [c.from?.phoneNumber, c.to?.phoneNumber]).filter(Boolean);
const texts = $('Read texts').all().flatMap(i => i.json.records || []);
const sent = $input.all();                    // one Gmail search per lead, same order
const now = Date.now();
const stale = [];
for (let i = 0; i < leads.length; i++) {{
  const l = leads[i];
  const reached = reachedOut(l, calls, texts, (sent[i]?.json?.resultSizeEstimate) || 0, activeNumbers);
  if (reached === 'on a call now') continue;                      // look again once the call ends
  if (reached) {{ state.alerted[l.taskId] = now; continue; }}      // handled: never alert about it
  if (now - l.createdAt < STALE_MIN * 60000) continue;             // still inside the grace period
  if (!isOpen(now)) continue;                                       // after hours: hold until 8 AM
  stale.push({{ ...l, overnight: !isOpen(l.createdAt) }});
}}
if (!stale.length) return [];
for (const l of stale) state.alerted[l.taskId] = now;
return [{{ json: {{ channel: '{CHANNEL}', text: staleLeadSlack(stale, now), count: stale.length }} }}];
"""


def code(name, js, pos, once=False):
    # NOTE: never set executeOnce on a Code node - it would drop every input item after the first,
    # and these nodes read $input.all(). Code nodes already run once for all items.
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}


nodes = [
    {'name': 'Every 5 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 5}]}}},
    {'name': 'Cards created recently', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'date_created_gt', 'value': '={{ Date.now() - 6 * 3600000 }}'},
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'order_by', 'value': 'created'}]},
                    'options': {}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('New leads', LEADS, [440, 0], once=True),
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
                        {'name': 'dateFrom', 'value': '={{ new Date(Date.now() - 12 * 3600000).toISOString() }}'}]},
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
    # a call in progress is not in the call log yet - Derek was on a 22-minute call when we alerted
    {'name': 'Calls happening right now', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1430, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': 'https://platform.ringcentral.com/restapi/v1.0/account/~/active-calls',
                    'sendQuery': True, 'queryParameters': {'parameters': [{'name': 'perPage', 'value': '100'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': "=Bearer {{ $('RingCentral token').first().json.access_token }}"}]},
                    'options': {}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('One item per lead', BACK_TO_LEADS, [1540, 0], once=True),
    # Gmail's own search is the cheapest way to ask "did we write to this person today?"
    {'name': 'Did we email them?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 0],
     'parameters': {'method': 'GET', 'url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        # by the email on the card or the customer's full name (lib/stale-leads.js sentMailQuery)
                        {'name': 'q', 'value': '={{ $json.mailQuery }}'},
                        {'name': 'maxResults', 'value': '2'}]},
                    'options': {}},
     'credentials': CRED_GMAIL, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Nobody on it yet?', DECIDE, [1980, 0], once=True),
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2200, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

order = [n['name'] for n in nodes]
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}

wf = {'name': '#21 New lead with nobody on it' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}   # kept so an alert can be traced (2026-10-06)

out = ROOT / 'workflows/stale-leads.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
