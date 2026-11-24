#!/usr/bin/env python3
"""Builds workflows/claims.json (item #6).

Customer emails about damage -> Claude fills the client's MOVING COMPLAINT INTAKE FORM ->
ClickUp card in "Claims Intake" + alert in Slack #sales-issues-and-damages.
Later emails in the same thread become comments on the existing card.

  python3 build/claims.py           test mode: posts to #automation-errors, no ClickUp card
  python3 build/claims.py --live    creates cards and posts to #sales-issues-and-damages
  python3 build/claims.py --probe   webhook-triggered copy for replaying past emails
"""
import json, pathlib, re, secrets, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/claims.js').read_text())

LIVE, PROBE = '--live' in sys.argv, '--probe' in sys.argv
ERRORS = 'C0C2X8B9VS9'                        # #automation-errors
CLAIMS_CHANNEL = 'C08PUTG37RD'                # #sales-issues-and-damages
CHANNEL = CLAIMS_CHANNEL if LIVE else ERRORS
CLAIMS_LIST = '901708004216'   # ClickUp list "Damage Claims" (the team's claims section; was "Claims Intake" until 2026-09-30)

CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}

# Automated senders that never send claims.
NOT_CUSTOMERS = ('-from:noreply@movegistics.com -from:mailer@shopify.com -from:yelp.com '
                 '-from:messaging.yelp.com -from:mail.yelp.com -from:thumbtack.com '
                 '-from:pro.thumbtack.com -from:ringcentral.com -from:stripe.com '
                 '-from:google.com -in:sent -in:chats')

FILTER = LIB + """
// Keep unseen customer emails that mention damage; remember which thread AND which customer already
// has a card. Customers often send photos in new emails with new subjects (new Gmail threads), so the
// same sender within CLAIM_DAYS is the same claim: those become comments, not new cards.
const CLAIM_DAYS = 60;
const store = $getWorkflowStaticData('global');
store.claimThreads = store.claimThreads || {};     // threadId -> { taskId, url, name }
store.claimCustomers = store.claimCustomers || {}; // sender email -> { taskId, url, name, at }
store.seenEmails = store.seenEmails || {};
const customerKey = from => (/@expresshaulers(moving)?\\.com$/i.test(from) ? '' : from.toLowerCase());
const now = Date.now();
const out = [];
for (const item of $input.all()) {
  const m = item.json;
  const id = String(m.id || '');
  if (!id || store.seenEmails[id]) continue;
  store.seenEmails[id] = now;
  if (!isClaimCandidate(m)) continue;
  const from = (m.from && m.from.value && m.from.value[0] && m.from.value[0].address) || '';
  const byCustomer = store.claimCustomers[customerKey(from)];
  const thread = store.claimThreads[String(m.threadId || '')]
    || (byCustomer && now - byCustomer.at < CLAIM_DAYS * 86400000 ? byCustomer : null);
  // several new emails from the same customer in one batch: one card, the rest ride along
  const sameBatch = !thread && customerKey(from) && out.find(o => !o.json.knownThread && customerKey(o.json.from) === customerKey(from));
  if (sameBatch) {
    const j = sameBatch.json;
    j.text = `${j.text}\\n\\n--- Another email: ${m.subject || ''} ---\\n${(m.text || '').slice(0, 2000)}`.slice(0, 6000);
    j.attachmentCount += (m.attachments || []).length;
    j.claudeBody = claudeBody({ ...m, subject: j.subject, text: j.text });
    continue;
  }
  out.push({ json: {
    gmailId: id,
    threadId: String(m.threadId || ''),
    subject: m.subject || '',
    from,
    date: m.date,
    text: (m.text || '').slice(0, 6000),
    attachmentCount: (m.attachments || []).length,
    knownThread: false,        // follow-ups are matched against the claims section itself (#6b)
    existingTaskId: thread ? thread.taskId : null,
    existingUrl: thread ? thread.url : null,
    claudeBody: claudeBody({ ...m, text: (m.text || '').slice(0, 6000) }),
  } });
}
for (const [k, t] of Object.entries(store.seenEmails)) if (now - t > 30 * 86400000) delete store.seenEmails[k];
return out;
"""

BUILD_CARD = LIB + f"""
// Claude answered: build the intake form for real claims (same order as the requests).
const asked = $('Existing claim thread?').all(1);
const items = $input.all();
const out = [];
for (let i = 0; i < items.length; i++) {{
  const msg = asked[i].json;
  const f = parseClaude(items[i].json);
  if (!f.claim) continue;
  out.push({{ json: {{
    ...msg,
    fields: f,
    cardName: cardName(f, msg),
    intake: renderIntake(f, {{ ...msg, from: {{ value: [{{ address: msg.from }}] }} }}),
    listId: '{CLAIMS_LIST}',
  }} }});
}}
return out;
"""

SAME_CLAIM = LIB + f"""
// The same claim often arrives twice: the customer writes again from another address, or both
// spouses write. Match the board, not just the sender, before opening a second card.
const tasks = $('Claims already on the board').all().flatMap(i => i.json.tasks || []);
// the claims come from the intake step - $input here is the list of cards on the board (the bug that
// stopped every new claim from 2026-09-25 to 2026-09-30)
return $('Build intake form').all().map(item => {{
  const j = item.json;
  const msg = {{ ...j, from: {{ value: [{{ address: j.from }}] }} }};
  const same = matchClaimOnBoard(tasks, j.fields, msg);
  const when = new Date().toLocaleString('en-US', {{ timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' }});
  return {{ json: {{ ...j,
    isNewClaim: !same,
    sameClaimId: same ? same.id : null,
    sameClaimUrl: same ? same.url : null,
    sameClaimComment: `\U0001F4E7 Another email about this claim from ${{j.from}} (${{when}}) - ${{j.subject}}:\\n\\n`
      + (j.text || '').slice(0, 1500),
    channel: '{CHANNEL}',
    text: same ? `:email: *Another email on an existing claim*: <${{same.url}}|${{same.name}}>\\nFrom ${{j.from}} - _${{j.subject}}_` : '',
  }} }};
}});
"""

AFTER_CREATE = LIB + f"""
// Card created: remember the thread so follow-up emails become comments, and write the Slack alert.
const store = $getWorkflowStaticData('global');
store.claimThreads = store.claimThreads || {{}};
const made = $input.all();
const src = $('Build intake form').all();
const out = [];
for (let i = 0; i < made.length; i++) {{
  const msg = src[i].json;
  const task = made[i].json;
  const url = task.url || (task.id ? 'https://app.clickup.com/t/' + task.id : null);
  if (task.id) {{
    store.claimThreads[msg.threadId] = {{ taskId: task.id, url, name: msg.cardName }};
    const key = /@expresshaulers(moving)?\\.com$/i.test(msg.from) ? '' : msg.from.toLowerCase();
    if (key) (store.claimCustomers = store.claimCustomers || {{}})[key] = {{ taskId: task.id, url, name: msg.cardName, at: Date.now() }};
  }}
  out.push({{ json: {{
    channel: '{CHANNEL}',
    text: {'' if LIVE else "':test_tube: _TEST MODE, no ClickUp card was created_\\n' + "}slackText(msg.fields, {{ ...msg, from: {{ value: [{{ address: msg.from }}] }} }}, url),
  }} }});
}}
return out;
"""

FOLLOW_UP = f"""
// A later email in a thread that already has a claim card.
return $input.all().map(item => ({{ json: {{
  ...item.json,
  comment: `New email from ${{item.json.from}} on this claim (${{item.json.subject}})`
    + (item.json.attachmentCount ? ` with ${{item.json.attachmentCount}} attachment(s), see Gmail` : '') + ':\\n\\n'
    + ((item.json.text || '').trim() ? (item.json.text || '').slice(0, 2000) : '(no text in the email)'),
  channel: '{CHANNEL}',
  text: `:speech_balloon: *Follow-up on an existing claim*: <${{item.json.existingUrl}}|open card>\\nNew email from ${{item.json.from}} — _${{item.json.subject}}_`
    + (item.json.attachmentCount ? ` (${{item.json.attachmentCount}} attachment(s))` : ''),
}} }}));
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

def slack(name, pos, src=None):
    # after a ClickUp call $json is ClickUp's reply, so a note posted after one reads the step that
    # wrote it (the follow-up notes failed with "invalid_arguments" until 2026-09-30)
    ref = f"$('{src}').item.json" if src else '$json'
    return {'name': name, 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': pos,
            'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                           'channelId': {'__rl': True, 'value': f'={{{{ {ref}.channel }}}}', 'mode': 'id'},
                           'text': f'={{{{ {ref}.text }}}}',
                           'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
            'credentials': CRED_SLACK}

PROBE_PATH = 'claims-probe-' + secrets.token_hex(10)
if PROBE:
    trigger = [
        {'name': 'Probe', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [-240, 0], 'webhookId': PROBE_PATH,
         'parameters': {'path': PROBE_PATH, 'httpMethod': 'GET', 'responseMode': 'onReceived', 'options': {}}},
        {'name': 'Customer email', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [0, 0],
         'parameters': {'resource': 'message', 'operation': 'getAll', 'returnAll': False,
                        'limit': '={{ Number($json.query.limit || 3) }}', 'simple': False,
                        'filters': {'q': '={{ $json.query.q }}'}, 'options': {}},
         'credentials': CRED_GMAIL},
    ]
else:
    trigger = [
        {'name': 'Customer email', 'type': 'n8n-nodes-base.gmailTrigger', 'typeVersion': 1.2, 'position': [0, 0],
         'parameters': {'pollTimes': {'item': [{'mode': 'everyMinute'}]}, 'simple': False,
                        'filters': {'q': NOT_CUSTOMERS}, 'options': {}},
         'credentials': CRED_GMAIL},
    ]

nodes = trigger + [
    code('Mentions damage?', FILTER, [240, 0]),
    if_node('Existing claim thread?', '={{ $json.knownThread }}', [460, 0]),
    code('Follow-up email', FOLLOW_UP, [680, -160]),
    {'name': 'Comment on card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [900, -160],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.existingTaskId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE},
    {'name': 'Ask Claude', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [680, 120],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.claudeBody) }}', 'options': {}},
     'credentials': CRED_ANTHROPIC},
    code('Build intake form', BUILD_CARD, [900, 120]),
    {'name': 'Claims already on the board', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
     'position': [1010, 120],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task'
                                            '?include_markdown_description=true',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('A new claim, or the same one?', SAME_CLAIM, [1120, 120]),
    if_node('Is it new?', '={{ $json.isNewClaim }}', [1230, 120]),
    {'name': 'Create claim card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1120, 120],
     'parameters': {'method': 'POST', 'url': f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ name: $json.cardName, markdown_content: $json.intake }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE},
    {'name': 'Note on the existing claim', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
     'position': [1340, 300],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.sameClaimId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.sameClaimComment, notify_all: false }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    slack('Note the other email', [1560, 300], 'A new claim, or the same one?'),
    code('Remember thread', AFTER_CREATE, [1340, 120]),
    slack('Alert the team', [1560, 120]),
    slack('Note the follow-up', [1120, -160], 'Follow-up email'),
]

CLAIM_CARD_WF = (ROOT / 'workflows/claim-card.id').read_text().strip()
HANDOVER = """
// What #6b needs to fill the team's claim form.
return $input.all().map(item => {
  const j = item.json, f = j.fields || {};
  const items = (f.items || []).filter(i => i && i.item).map(i => i.item);
  return { json: {
    sourceLabel: 'Email', subject: j.subject, reported: j.date ? Date.parse(j.date) : Date.now(),
    customerName: f.customerName || '', email: f.email || j.from || '', phone: f.phone || '',
    moveDateText: f.moveDate || '', origin: f.movingFrom || '', destination: f.movingTo || '',
    item: f.itemDamaged || items.join(', ') || f.issueCategory || '',
    whatHappened: f.whatHappened || f.summary || '', summary: f.summary || '',
    estimatedValue: f.totalValue || null, condition: f.conditionBeforeMove || '', request: f.requestType || '',
    insurance: f.hasInsurance || '', coverage: f.coveragePurchased || '',
    original: (j.text || '').slice(0, 1500) + (j.attachmentCount ? `\n\n(${j.attachmentCount} attachment(s) - photos are in the email in Gmail)` : ''),
  } };
});
"""

one = lambda n, i=0: [{'node': n, 'type': 'main', 'index': i}]
connections = {
    'Customer email': {'main': [one('Mentions damage?')]},
    'Mentions damage?': {'main': [one('Existing claim thread?')]},
    'Existing claim thread?': {'main': [one('Follow-up email'), one('Ask Claude')]},
    'Follow-up email': {'main': [one('Comment on card')]},
    'Comment on card': {'main': [one('Note the follow-up')]},
    'Ask Claude': {'main': [one('Build intake form')]},
    'Build intake form': {'main': [one('Claims already on the board')]},
    'Claims already on the board': {'main': [one('A new claim, or the same one?')]},
    'A new claim, or the same one?': {'main': [one('Is it new?')]},
    'Is it new?': {'main': [one('Create claim card'), one('Note on the existing claim')]},
    'Note on the existing claim': {'main': [one('Note the other email')]},
    'Create claim card': {'main': [one('Remember thread')]},
    'Remember thread': {'main': [one('Alert the team')]},
}
# the card, the duplicate check and the alert now live in #6b (the team's own claims section)
DROP = {'Claims already on the board', 'A new claim, or the same one?', 'Is it new?', 'Create claim card',
        'Note on the existing claim', 'Note the other email', 'Remember thread', 'Alert the team'}
nodes = [n for n in nodes if n['name'] not in DROP] + [
    code('For the claims section', HANDOVER, [1120, 120]),
    {'name': 'Hand over the claim', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2, 'position': [1340, 120],
     'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': CLAIM_CARD_WF, 'mode': 'id'},
                    'mode': 'once', 'options': {'waitForSubWorkflow': False}},
     'disabled': not LIVE}]
connections = {k: v for k, v in connections.items() if k not in DROP}
connections['Build intake form'] = {'main': [one('For the claims section')]}
connections['For the claims section'] = {'main': [one('Hand over the claim')]}
if PROBE:
    connections['Probe'] = {'main': [one('Customer email')]}

name = '#6 Damage claims → ClickUp + Slack'
name += ' [PROBE - delete me]' if PROBE else ('' if LIVE else ' [TEST MODE - no ClickUp card]')
wf = {'name': name, 'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York'}}

out = ROOT / ('workflows/claims-probe.json' if PROBE else 'workflows/claims.json')
out.write_text(json.dumps(wf, indent=2))
print(out, 'PROBE' if PROBE else ('LIVE' if LIVE else 'TEST MODE'))
