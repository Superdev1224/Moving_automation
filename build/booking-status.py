#!/usr/bin/env python3
"""Builds workflows/booking-status.json (items #7 and #8).

  Stripe $52 deposit  +  Movegistics/Gmail "estimate signed"  -> ClickUp card to "moves booked"
  only one of the two, 30 minutes later                       -> ClickUp card to "pending"

Card moves are silent (visible in ClickUp); only problems go to Slack #automation-errors.
  python3 build/booking-status.py          test mode: decides and reports, never changes ClickUp
  python3 build/booking-status.py --live   actually moves the cards
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

LIVE = '--live' in sys.argv
WAIT_MIN = next((int(a.split('=')[1]) for a in sys.argv if a.startswith('--wait-min=')), 30)

LIB_RAW = re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/booking.js').read_text())
LIB = LIB_RAW.replace('const WAIT_MS = 30 * 60 * 1000;', f'const WAIT_MS = {WAIT_MIN} * 60 * 1000;')
ERRORS = 'C0C2X8B9VS9'   # #automation-errors
LIST_ID = '901705949004'
MG_URL = 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1/opportunity/list/'
MG_LOCATION = '100821'

CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_STRIPE = {'stripeApi': {'id': 'bn8XQuRj56UsqRMB', 'name': 'Stripe - Express Haulers (read-only)'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}

# --- Code node sources -------------------------------------------------------
RECORD = LIB + """
// Stripe deposit or "estimate signed" email -> remember it, and book at once if both are in.
const store = $getWorkflowStaticData('global');
store.bookings = store.bookings || {};
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  const ev = depositFromStripe(j.body ?? j) || signatureFromEmail(j);
  if (!ev) continue;
  const entry = recordEvent(store.bookings, ev);
  if (!entry) continue;
  const action = decide(entry, Date.now());
  if (action) { entry.applied = action.status; out.push({ json: { ...action, entry } }); }
}
expire(store.bookings, Date.now());
return out;
"""

FIND_WAITING = LIB + """
// Every 5 minutes: customers past the 30-minute grace period.
// Deposit-only ones are checked against Movegistics (isSigned) before deciding.
const store = $getWorkflowStaticData('global');
store.bookings = store.bookings || {};
const now = Date.now();
const out = [];
const checking = new Set(needsSignatureCheck(store.bookings, now).map(e => e.key));
for (const key of checking) out.push({ json: { key, name: store.bookings[key].name, check: true } });
for (const e of Object.values(store.bookings)) {
  if (checking.has(e.key)) continue;
  const action = decide(e, now);
  if (action) { e.applied = action.status; out.push({ json: { ...action, entry: e, check: false } }); }
}
expire(store.bookings, now);
return out;
"""

AFTER_CHECK = LIB + """
// Movegistics answered: does this customer have a signed estimate?
const store = $getWorkflowStaticData('global');
const out = [];
// HTTP responses come back in the same order as the requests that were sent.
const asked = $('Check Movegistics?').all(0);
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  const rows = items[i].json.data?.rows || [];
  const entry = store.bookings?.[asked[i]?.json.key];
  if (!entry) continue;
  const wanted = norm(entry.name);
  const signed = rows.some(r => r.isSigned === true && norm(r.contactName?.name || r.title || '').includes(wanted));
  const action = decide(entry, Date.now(), signed);
  if (action) { entry.applied = action.status; out.push({ json: { ...action, entry } }); }
}
return out;
"""

MATCH = LIB + """
// Find the customer's ClickUp card among every card in "All Leads".
const LIVE = %LIVE%;   // in test mode every decision is reported to Slack and no card is moved
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const out = [];
for (const item of $('Merge decisions').all()) {
  const a = item.json;
  const m = matchCard(tasks, a.entry);
  const skip = m && SKIP_STATUSES.includes((m.task.status?.status || m.task.status || '').toLowerCase());
  out.push({ json: {
    ...a,
    taskId: m ? m.task.id : null,
    matchedBy: m ? m.how : null,
    skip: !!skip,
    channel: '%CHANNEL%',
    text: skip
      ? `:information_source: *No change*: ${a.entry.name} — ${a.reason}, but the card is already "${m.task.status?.status || m.task.status}".`
      : (LIVE ? '' : ':test_tube: _TEST MODE, no card was changed_\\n') + bookingSlack(a, m),
    needsAlert: !m || !!skip || !LIVE,
  } });
}
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
    # --- triggers ---
    {'name': 'Stripe payment', 'type': 'n8n-nodes-base.stripeTrigger', 'typeVersion': 1, 'position': [0, -160],
     'parameters': {'events': ['charge.succeeded']}, 'credentials': CRED_STRIPE},
    {'name': 'Signed estimate email', 'type': 'n8n-nodes-base.gmailTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'pollTimes': {'item': [{'mode': 'everyMinute'}]}, 'simple': False,
                    'filters': {'q': 'from:noreply@movegistics.com subject:"Has Been Signed"'}, 'options': {}},
     'credentials': {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}},
    {'name': 'Every 5 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 220],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 5}]}}},

    # --- event paths ---
    code('Record event', RECORD, [260, -80]),
    code('Needs a decision?', FIND_WAITING, [260, 220]),
    if_node('Check Movegistics?', '={{ $json.check }}', [500, 220]),
    {'name': 'Movegistics: signed?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [740, 140],
     'parameters': {'method': 'POST', 'url': MG_URL,
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': MG_LOCATION}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ filters: [], page: { limit: 10, pageId: 0 }, query: $json.name, sortBy: { direction: "DESC", property: "updatedAt" }, timeZone: "America/New_York" }) }}',
                    'options': {}},
     'credentials': CRED_MG},
    code('Decide after check', AFTER_CHECK, [980, 140]),

    # --- shared: match the card, then act ---
    {'name': 'Merge decisions', 'type': 'n8n-nodes-base.merge', 'typeVersion': 3, 'position': [1220, 60],
     'parameters': {'numberInputs': 3}},
    {'name': 'Get all cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1460, 60],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True,
                    'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 25}}},
                    'executeOnce': True},
     'credentials': CRED_CLICKUP},
    code('Match card', MATCH.replace('%CHANNEL%', ERRORS).replace('%LIVE%', 'true' if LIVE else 'false'), [1700, 60]),
    if_node('Card found?', '={{ !$json.needsAlert }}', [1940, 60]),
    {'name': 'Move card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2180, -40],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.taskId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status: $json.status }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE},
    {'name': 'Alert #automation-errors', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2180, 160],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                    'text': '={{ $json.text }}', 'otherOptions': {'includeLinkToWorkflow': False}},
     'credentials': CRED_SLACK},
]

def to(*names, index=0):
    return [[{'node': n, 'type': 'main', 'index': index}] for n in names]

connections = {
    'Stripe payment': {'main': to('Record event')},
    'Signed estimate email': {'main': to('Record event')},
    'Every 5 minutes': {'main': to('Needs a decision?')},
    'Record event': {'main': [[{'node': 'Merge decisions', 'type': 'main', 'index': 0}]]},
    'Needs a decision?': {'main': to('Check Movegistics?')},
    'Check Movegistics?': {'main': [
        [{'node': 'Movegistics: signed?', 'type': 'main', 'index': 0}],
        [{'node': 'Merge decisions', 'type': 'main', 'index': 1}]]},
    'Movegistics: signed?': {'main': to('Decide after check')},
    'Decide after check': {'main': [[{'node': 'Merge decisions', 'type': 'main', 'index': 2}]]},
    'Merge decisions': {'main': to('Get all cards')},
    'Get all cards': {'main': to('Match card')},
    'Match card': {'main': to('Card found?')},
    'Card found?': {'main': [
        [{'node': 'Move card', 'type': 'main', 'index': 0}],
        [{'node': 'Alert #automation-errors', 'type': 'main', 'index': 0}]]},
}

wf = {'name': '#7/#8 Booking status: deposit + signature → Moves Booked / Pending' + ('' if LIVE else ' [TEST MODE - no card changes]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/booking-status.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
