#!/usr/bin/env python3
"""Builds workflows/claim-card.json — #6b claim card in the team's "Damage Claims" space.

Email claims (#6) and claims raised on calls or texts (#12) hand every claim over to this workflow.
It finds the customer's move card (date, addresses, contact details), the crew from that day's
Connecteam shift, and an open claim from the same customer. Then it either creates an "Open Claim"
card with the team's Damage / Loss Claim Form fields filled in, or adds a note to the open one, and
alerts #sales-issues-and-damages with a link to the claim card.

  python3 build/claim-card.py
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAIMS_LIST = '901705953745'           # space "Damage Claims", list "List" - the team's claims section
LEADS_LIST = '901705949004'            # All Leads (move cards)
SCHEDULER_ID = 16427123
ISSUES = 'C08PUTG37RD'                 # #sales-issues-and-damages

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
FIELDS = json.loads((ROOT / 'lib/claim-fields.json').read_text())
LIB = (strip((ROOT / 'lib/parse-card.js').read_text())
       + f"\nconst CLAIM_FIELD_IDS = {json.dumps(FIELDS)};\n"
       + strip((ROOT / 'lib/claim-card.js').read_text()))

MATCH = LIB + """
// Each claim: its move card (for what the customer did not say) and an open claim card, if any.
const claimCards = $('Claims section').all().flatMap(i => i.json.tasks || []);
const moveCards = $input.all().flatMap(i => i.json.tasks || []);
const now = Date.now();
return $('Claim handed over').all().map(item => {
  const c = { ...item.json };
  const move = findCard(moveCards, c, false);
  const m = fromMoveCard(move, now);
  const said = c.moveDateText ? findDate(String(c.moveDateText), now) : null;
  const out = { ...c,
    email: c.email || m.email, phone: c.phone || m.phone,
    origin: c.origin || m.origin, destination: c.destination || m.destination,
    moveDate: said ? pastMoveDate(said, now) : (m.moveDate || null),
    moveCardUrl: m.moveCardUrl || '', moveCardName: m.moveCardName || '',
    reported: c.reported || now };
  const open = findClaim(claimCards, out, now);
  out.existingId = open ? open.id : null;
  out.existingUrl = open ? open.url : null;
  out.existingStatus = open ? (open.status?.status || '') : '';
  out.shiftFrom = out.moveDate ? Math.floor(out.moveDate / 1000) : 0;
  out.shiftTo = out.moveDate ? Math.floor(out.moveDate / 1000) + 86400 : 1;
  return { json: out };
});
"""

FILL = LIB + """
// Fill the form: the crew from that day's shift, then the card (or the note on the open claim).
const users = $input.first()?.json?.data?.users || [];
const shifts = $('Shifts that day').all();
return $('Match the claim').all().map((item, i) => {
  const c = { ...item.json };
  c.crew = crewFor(shifts[i]?.json?.data?.shifts || [], users, c.customerName);
  const isNew = !c.existingId;
  return { json: { ...c, isNew,
    cardBody: { name: claimTitle(c), markdown_content: claimDescription(c), status: 'open claim',
                custom_fields: claimFields(c) },
    comment: claimComment(c) } };
});
"""

ALERT = LIB + """
// The alert, with the link to the claim card itself.
const c = $('Fill the form').item.json;
const url = c.existingUrl || $json.url || '';
return { json: { text: claimAlert(c, url, c.isNew) } };
"""


def code(name, js, pos, per_item=False):
    p = {'jsCode': js.strip()}
    if per_item:
        p['mode'] = 'runOnceForEachItem'
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': p}


def clickup_get(name, url, pos, pages=1, closed=False):
    qs = [{'name': 'include_markdown_description', 'value': 'true'}]
    if closed:      # "complete" is a closed status: without this, finished claims were never seen
        qs.append({'name': 'include_closed', 'value': 'true'})
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos, 'executeOnce': True,
         'parameters': {'method': 'GET', 'url': url,
                        'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                        'sendQuery': True, 'queryParameters': {'parameters': qs},
                        'options': {}},
         'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'}
    if pages > 1:
        n['parameters']['options'] = {'pagination': {'pagination': {
            'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
            'paginationCompleteWhen': 'other',
            'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
            'limitPagesFetched': True, 'maxRequests': pages}}}
    return n


def with_retry(nodes, names):
    # a passing ClickUp/API hiccup is retried before anything decides a card does not exist
    for n in nodes:
        if n['name'] in names:
            n.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000})
    return nodes

nodes = [
    {'name': 'Claim handed over', 'type': 'n8n-nodes-base.executeWorkflowTrigger', 'typeVersion': 1.1,
     'position': [0, 0], 'parameters': {'inputSource': 'passthrough'}},
    clickup_get('Claims section', f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task', [220, 0], pages=3, closed=True),
    clickup_get('Move cards', f'https://api.clickup.com/api/v2/list/{LEADS_LIST}/task', [440, 0], pages=45),
    code('Match the claim', MATCH, [660, 0]),
    {'name': 'Shifts that day', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [880, 0],
     'parameters': {'method': 'GET',
                    'url': f'=https://api.connecteam.com/scheduler/v1/schedulers/{SCHEDULER_ID}/shifts'
                           '?startTime={{ $json.shiftFrom }}&endTime={{ $json.shiftTo }}&limit=200',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'options': {}},
     'credentials': CRED_CONNECTEAM, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Crew names', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': 'https://api.connecteam.com/users/v1/users?limit=500',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'options': {}},
     'credentials': CRED_CONNECTEAM, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Fill the form', FILL, [1320, 0]),
    {'name': 'New claim?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1540, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isNew', 'leftValue': '={{ $json.isNew }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Create the claim card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, -100],
     'parameters': {'method': 'POST', 'url': f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.cardBody) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
    {'name': 'Note on the open claim', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 100],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.existingId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
    code('Alert', ALERT, [1980, 0], per_item=True),
    {'name': 'Post the alert', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2200, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ISSUES, 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

nodes = with_retry(nodes, ['Claims section', 'Move cards'])
one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
connections = {
    'Claim handed over': {'main': [one('Claims section')]},
    'Claims section': {'main': [one('Move cards')]},
    'Move cards': {'main': [one('Match the claim')]},
    'Match the claim': {'main': [one('Shifts that day')]},
    'Shifts that day': {'main': [one('Crew names')]},
    'Crew names': {'main': [one('Fill the form')]},
    'Fill the form': {'main': [one('New claim?')]},
    'New claim?': {'main': [one('Create the claim card'), one('Note on the open claim')]},
    'Create the claim card': {'main': [one('Alert')]},
    'Note on the open claim': {'main': [one('Alert')]},
    'Alert': {'main': [one('Post the alert')]},
}

wf = {'name': '#6b Claim card → Damage Claims space', 'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}
(ROOT / 'workflows/claim-card.json').write_text(json.dumps(wf, indent=2))
print(ROOT / 'workflows/claim-card.json')
