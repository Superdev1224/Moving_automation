#!/usr/bin/env python3
"""Builds workflows/conversations.json (items #1 and #3).

Two sources, one analysis:
  * phone calls  - worker/transcribe-calls.py posts a transcript to this workflow's webhook
  * text threads - polled from RingCentral every 10 minutes (only threads the customer replied in)
Claude rates the lead hot/warm/cold with tips (Slack #lead-scores) and writes a
"where we left off + next steps" note as a ClickUp comment on the customer's card.

  python3 build/conversations.py          test mode: Slack -> #automation-errors, no ClickUp comment
  python3 build/conversations.py --live   Slack -> #lead-scores, comments on the card
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
strip = lambda s: re.sub(r"\nif \(typeof module.*\n?$", "\n", s)
LIB = (strip((ROOT / 'lib/conversation.js').read_text()) + strip((ROOT / 'lib/booking.js').read_text())
       + strip((ROOT / 'lib/identity.js').read_text()))

LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                          # #automation-errors
SCORES = 'C0C343MQBLH'                          # #lead-scores
ISSUES = 'C08PUTG37RD'                          # #sales-issues-and-damages
CHANNEL = SCORES if LIVE else ERRORS
ISSUE_CHANNEL = ISSUES if LIVE else ERRORS
LIST_ID = '901705949004'
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']
OUR_NUMBERS = ['+18623799113', '+12012552545', '+18629304630', '+18623329066', '+18629999390']
SMS_WINDOW_MIN = 180        # how much of a text thread to read for context
QUIET_MIN = 45              # only analyse a thread once it has been quiet this long

CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CLAIMS_LIST = '901708004216'   # ClickUp list "Damage Claims" (the team's claims section; was "Claims Intake" until 2026-09-30)
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
CRED_WEBHOOK = {'httpHeaderAuth': {'id': 'zkiO8no2g5sBpxfp', 'name': 'Transcript worker token'}}

RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]
WEBHOOK_PATH = 'call-transcript'

CALL_IN = LIB + """
// A transcript arrived from the server-side worker.
const out = [];
for (const item of $input.all()) {
  const b = item.json.body ?? item.json;
  if (!b.transcript || b.transcript.length < 80) continue;      // too short to be worth reading
  const ours = %OURS%.map(n => n.replace(/\\D/g, ''));
  const from = String(b.fromNumber || ''), to = String(b.toNumber || '');
  const customerNumber = ours.includes(from.replace(/\\D/g, '')) ? to : from;
  const callerName = ours.includes(from.replace(/\\D/g, '')) ? (b.toName || '') : (b.fromName || '');
  const ourNumber = ours.includes(from.replace(/\\D/g, '')) ? from : to;
  const conv = { kind: 'call', customerNumber, callerName, ourNumber, direction: b.direction, duration: b.duration,
                 startTime: b.startTime, text: b.transcript, sourceId: b.callId };
  out.push({ json: conv });
}
return out;
"""

FANOUT = f"""
const token = $json.access_token;
const since = new Date(Date.now() - {SMS_WINDOW_MIN} * 60000).toISOString().replace(/\\.\\d+Z$/, 'Z');
return {json.dumps(EXTENSIONS)}.map(ext => ({{ json: {{ ext, token, since }} }}));
"""

SMS_IN = LIB + f"""
// Build one thread per customer number; analyse it once it has been quiet for a while,
// and only when the customer actually replied at least once.
const store = $getWorkflowStaticData('global');
store.threadSeen = store.threadSeen || {{}};      // customer number -> timestamp of last analysed message
const now = Date.now();
const records = $input.all().flatMap(i => i.json.records || []);
const out = [];
for (const t of threadsFromMessages(records, {json.dumps(OUR_NUMBERS)})) {{
  if (!t.hasInbound) continue;
  const last = Date.parse(t.lastAt || '') || 0;
  if (now - last < {QUIET_MIN} * 60000) continue;                 // still going, wait for it to settle
  if ((store.threadSeen[t.customerNumber] || 0) >= last) continue; // nothing new since last time
  const seenBefore = store.threadSeen[t.customerNumber] || 0;
  store.threadSeen[t.customerNumber] = last;
  const fresh = t.messages.filter(m => (Date.parse(m.at) || 0) > seenBefore);
  const text = fresh.map(m => `[${{String(m.at).slice(0, 16).replace('T', ' ')}}] ${{m.who}}: ${{m.text}}`).join('\\n');
  const conv = {{ kind: 'sms', customerNumber: t.customerNumber, ourNumber: t.ourNumber, startTime: t.lastAt,
                 text, sourceId: 'sms:' + t.customerNumber + ':' + last }};
  out.push({{ json: conv }});
}}
for (const [k, v] of Object.entries(store.threadSeen)) if (now - v > 30 * 86400000) delete store.threadSeen[k];
return out;
"""

MATCH_CARD = (LIB + """
// Look the customer up in ClickUp first, so Claude knows whether this move is already booked,
// and attach the last summary we wrote for them so only what's new gets written.
// every board (All Leads, Influencers, Realtors), lost and completed cards included
const pages = [...$('Get all cards').all(), ...$('Other boards').all()];
const allCards = pages.flatMap(i => i.json.tasks || []);
const boardOk = !pages.some(p => p.json && p.json.error) && $('Get all cards').all().flatMap(i => i.json.tasks || []).length >= 1500;
const memory = ($getWorkflowStaticData('global').lastSummary) || {};
// Crew and office staff (Connecteam, current and former) and our own lines are not customers: their
// calls and texts are skipped before the AI reads them (Carlos, Junior, David and Edin were being
// scored as hot leads until 2026-10-02).
const ten = p => String(p || '').replace(/\\D/g, '').slice(-10);
const internal = new Set([...($('Staff numbers').first().json.data?.users || []).map(u => ten(u.phoneNumber)),
                          ...%OURS%.map(ten)].filter(n => n.length === 10));
const out = [];
for (const item of $('Merge conversations').all()) {
  const conv = item.json;
  if (internal.has(ten(conv.customerNumber))) continue;
  // who is this? number first, then the caller-ID name; the AI's name is tried again after it reads
  const id = resolveIdentity({ number: conv.customerNumber, names: [conv.callerName], cards: allCards, boardOk });
  const m = id.state === 'matched' ? id.card : null;
  const withCard = { ...conv, identity: id,
    taskId: m ? m.id : null,
    cardName: m ? m.name : null,
    cardUrl: m ? m.url : null,
    cardStatus: m ? m.status : null,
    hasCard: !!m };
  const prev = memory[conv.customerNumber];
  if (prev && Date.now() - prev.at < 14 * 86400000) withCard.previous = prev;
  out.push({ json: { ...withCard, claudeBody: claudeBody(withCard) } });
}
return out;
""").replace('%OURS%', json.dumps(OUR_NUMBERS))

AFTER_AI = LIB + """
// Claude's analysis, paired with the conversation it came from.
const asked = $('Find the card').all();
const items = $input.all();
const out = [];
for (let i = 0; i < items.length; i++) {
  const conv = asked[i].json;
  const a = parseClaude(items[i].json);
  if (!a || a.isCustomerConversation === false) continue;      // internal/admin calls are skipped
  if (['employee', 'vendor'].includes(a.contactType)) continue;  // staff we do not know of yet, suppliers, pitches
  out.push({ json: { ...conv, analysis: a } });
}
return out;
"""

DELIVER = LIB + f"""
// Decide what each conversation produces, once we know who it is:
//   a new lead (or a lost lead coming back hot/warm) -> ONE post per person in #lead-scores, updated
//   anyone raising a claim/billing/complaint         -> alert in #sales-issues-and-damages
//   any real conversation with a card                -> summary comment on the ClickUp card
const LIVE = {'true' if LIVE else 'false'};
const tag = LIVE ? '' : ':test_tube: _TEST MODE_\\n';
const store = $getWorkflowStaticData('global');
store.lastSummary = store.lastSummary || {{}};
const pages = [...$('Get all cards').all(), ...$('Other boards').all()];
const allCards = pages.flatMap(i => i.json.tasks || []);
const boardOk = !pages.some(p => p.json && p.json.error) && $('Get all cards').all().flatMap(i => i.json.tasks || []).length >= 1500;
const byId = Object.fromEntries(allCards.map(t => [t.id, t]));
// lead posts from the last 7 days: a person already posted gets that post updated, not a new one
const recentPosts = $input.all().map(i => i.json).filter(m => Date.now() / 1000 - +m.ts < 7 * 86400 && /HOT|WARM|COLD/.test(m.text || ''));
const nowLabel = new Date().toLocaleString('en-US', {{ timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' }});
const out = [];
for (const item of $('Claude analysis').all()) {{
  let conv = item.json;
  const a = conv.analysis;
  // second chance to find the card: the name they gave in the conversation
  let id = conv.identity || {{ state: 'none' }};
  if (id.state !== 'matched' && id.state !== 'unknown' && a.customerName) {{
    const again = resolveIdentity({{ number: conv.customerNumber, names: [a.customerName, conv.callerName], cards: allCards, boardOk }});
    if (again.state === 'matched' || (again.state === 'possible' && id.state === 'none')) id = again;
  }}
  if (id.state === 'matched' && !conv.hasCard) conv = {{ ...conv, taskId: id.card.id, cardName: id.card.name, cardUrl: id.card.url, cardStatus: id.card.status, hasCard: true }};
  // matched by name: save the number on the card so the next call matches by phone
  let learnMd = null;
  if (id.state === 'matched' && id.card.how === 'name' && byId[id.card.id]) {{
    const md = byId[id.card.id].markdown_description || '';
    const n = conv.customerNumber;
    learnMd = /^\\*\\*Phone Number -\\*\\*\\s*$/m.test(md) ? md.replace(/^(\\*\\*Phone Number -\\*\\*)\\s*$/m, `$1 ${{n}}`)
      : `${{md.trim()}}\\n\\n**Also calls from -** ${{n}}`;
    id = {{ ...id, learned: true }};
  }}
  const status = String(conv.cardStatus || '').toLowerCase();
  const group = stageGroup(conv.cardStatus, conv.cardName);
  const lost = status === 'moves lost';
  const real = a.substantive !== false && a.newInfo !== false;   // nothing said, or nothing new: stay quiet
  if (real && a.summary) store.lastSummary[conv.customerNumber] = {{
    at: Date.now(), summary: a.summary,
    when: new Date().toLocaleString('en-US', {{ timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }}) }};
  const card = conv.hasCard ? {{ name: conv.cardName, url: conv.cardUrl, status: conv.cardStatus }} : null;
  const temp = a.temperature;
  // a lost lead only comes back into #lead-scores when they are hot or warm again
  const postScore = real && group === 'lead' && isLeadContact(a) && ['hot', 'warm', 'cold'].includes(temp)
    && (!lost || ['hot', 'warm'].includes(temp));
  // one post per person: same number, or the same card
  const prev = recentPosts.filter(m => (m.text || '').includes(`(${{conv.customerNumber}})`) || (card && (m.text || '').includes(card.url)))
    .sort((x, y) => +y.ts - +x.ts)[0];
  const count = prev ? (+(((prev.text || '').match(/· (\\d+) conversations/) || [])[1]) || 1) + 1 : 1;
  const who = a.customerName || (card && card.name.split('|')[0].trim()) || conv.customerNumber;
  const what = conv.kind === 'call' ? `${{conv.direction === 'Inbound' ? 'Inbound' : 'Outbound'}} call, ${{Math.max(1, Math.round((conv.duration || 0) / 60))}} min` : 'Text conversation';
  const head = `${{HEAT[temp] || ':grey_question:'}} — *${{who}}* (${{conv.customerNumber}})`
    + (lost ? ' · :recycle: _was Moves Lost_' : '')
    + (count > 1 ? ` · ${{count}} conversations, latest ${{nowLabel}}` : ` · _${{what}}_`);
  let leadText = `${{head}}\\n${{firstSentences(a.description, 1)}}\\n`;
  if (asList(a.tips).length) leadText += `• ${{asList(a.tips)[0]}}\\n`;
  // 2026-11-20 client: the salesperson and the phone line on the lead score (not on the coaching)
  const line = lineLabel(conv.ourNumber);
  const repLine = `:bust_in_silhouette: *Sales:* ${{a.salesRep || '_name not said_'}}${{line ? `  ·  :telephone_receiver: *Line:* ${{line}}` : ''}}`;
  leadText += repLine + '\\n';
  leadText += identityLine(id, conv.customerNumber);
  out.push({{ json: {{
    ...conv, identity: id, group,
    channel: '{CHANNEL}',
    issueChannel: '{ISSUE_CHANNEL}',
    postScore,
    leadTs: postScore && prev ? prev.ts : '',
    text: tag + leadText,
    threadText: `_${{what}} · ${{nowLabel}}_ · ${{HEAT[temp] || ''}} ${{a.summary || a.description || ''}}\\n${{repLine}}`,
    learnMd, learnCardId: learnMd ? id.card.id : null,
    postIssue: real && !!a.issue && !['claim', 'damage'].includes(String(a.issue || '').toLowerCase()),
    postComment: real && conv.hasCard,
    moveTo: real && conv.hasCard ? aiStatusMove(a, conv) : null,     // #3: the AI moves the card
    issueText: tag + issueSlack(a, conv, card),
    comment: clickupComment(a, conv),
    isClaim: real && ['claim', 'damage'].includes(String(a.issue || '').toLowerCase()),
    claimBody: claimCard(a, conv),
    analysis: a,
  }} }});
}}
return out;
"""


CLAIM_DECIDE = LIB + f"""
// Claims Intake already has a card for this customer? Then add a note instead of a second card.
const tasks = $input.all().flatMap(i => i.json.tasks || []);
return $('Score and summarise').all().filter(i => i.json.isClaim).map(item => {{
  const j = item.json;
  const existing = matchClaimCard(tasks, j, j.analysis);
  const when = new Date().toLocaleString('en-US', {{ timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' }});
  return {{ json: {{ ...j,
    claimsListId: '{CLAIMS_LIST}',
    claimTaskId: existing ? existing.id : null,
    claimUrl: existing ? existing.url : null,
    newClaim: !existing,
    claimComment: `:telephone_receiver: Raised again on a ${{j.kind === 'call' ? 'phone call' : 'text'}} (${{when}}): `
      + (j.analysis.summary || j.analysis.description || ''),
  }} }};
}});
"""

CLAIM_ALERT = """
// The alert for a claim raised on a call or text, posted once its claim card exists - so it links
// to the claim card, not the move card (client, 2026-09-30: "it's just sending us to their move card").
const j = $('New claim or a note?').item.json;
const url = j.claimUrl || $json.url || '';
const lines = String(j.issueText || '').split('\\n');
const last = lines[lines.length - 1];
const moveLink = /\\|Open card in ClickUp>/.test(last) ? last.replace('Open card in ClickUp', 'move card') : '';
const head = j.newClaim ? '' : '_Raised again - added to the open claim card._\\n';
return { json: { channel: j.issueChannel,
  text: head + lines.slice(0, -1).join('\\n') + '\\n'
    + (url ? `<${url}|Open the claim card in ClickUp>` : '_The claim card could not be created - please add it by hand._')
    + (moveLink ? ` · ${moveLink}` : '') } };
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

def with_retry(nodes, names):
    # a passing ClickUp/API hiccup is retried before anything decides a card does not exist
    for n in nodes:
        if n['name'] in names:
            n.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000})
    return nodes

nodes = [
    # --- source 1: call transcripts pushed by the worker ---
    {'name': 'Call transcript', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [0, -180],
     'webhookId': WEBHOOK_PATH,
     'parameters': {'path': WEBHOOK_PATH, 'httpMethod': 'POST', 'responseMode': 'onReceived',
                    'authentication': 'headerAuth', 'options': {}},
     'credentials': CRED_WEBHOOK},
    code('Read transcript', CALL_IN.replace('%OURS%', json.dumps(OUR_NUMBERS)), [240, -180]),

    # --- source 2: text threads ---
    {'name': 'Every 10 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 120],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 10}]}}},
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [240, 120],
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    code('Each company line', FANOUT, [460, 120]),
    {'name': 'Read texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [680, 120],
     'parameters': {'method': 'GET',
                    'url': '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
                    'sendQuery': True,
                    'queryParameters': {'parameters': [
                        {'name': 'messageType', 'value': 'SMS'},
                        {'name': 'dateFrom', 'value': '={{ $json.since }}'},
                        {'name': 'perPage', 'value': '250'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.token }}'}]},
                    'options': {}}},
    code('Build text threads', SMS_IN, [900, 120]),

    # --- shared: analyse, match, deliver ---
    {'name': 'Merge conversations', 'type': 'n8n-nodes-base.merge', 'typeVersion': 3, 'position': [1140, -40],
     'parameters': {'numberInputs': 2}},
    {'name': 'Ask Claude', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1360, -40],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.claudeBody) }}',
                    'options': {'timeout': 180000}},
     'credentials': CRED_ANTHROPIC},
    code('Claude analysis', AFTER_AI, [1800, -40]),
    {'name': 'Get all cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1250, -40],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True,
                    # closed cards too: a past customer calling was scored as a new lead without them
                    'queryParameters': {'parameters': [{'name': 'include_markdown_description', 'value': 'true'},
                                                       {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 50}}}},   # ~3,100 cards = 31 pages
     'executeOnce': True, 'credentials': CRED_CLICKUP},
    {'name': 'Staff numbers', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1200, -140],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': 'https://api.connecteam.com/users/v1/users?limit=500',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'options': {}},
     'credentials': CRED_CONNECTEAM, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    # the other two boards, so influencers and realtors are found by their number too
    {'name': 'Which other boards', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1300, 60],
     'parameters': {'jsCode': 'return ["901705958022", "901712515969"].map(list => ({ json: { list } }));'}},
    {'name': 'Other boards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1360, 60],
     'parameters': {'method': 'GET', 'url': '=https://api.clickup.com/api/v2/list/{{ $json.list }}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'}, {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 10}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput',
     'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000},
    # the lead posts of the last days: one post per person, updated rather than repeated
    {'name': 'Recent lead posts', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1910, -40],
     'executeOnce': True,
     'parameters': {'authentication': 'accessToken', 'resource': 'channel', 'operation': 'history',
                    'channelId': {'__rl': True, 'value': CHANNEL, 'mode': 'id'},
                    'returnAll': False, 'limit': 300, 'filters': {}},
     'credentials': CRED_SLACK, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    if_node('New or update?', "={{ !!$json.leadTs }}", [2350, -160]),
    {'name': 'Update the lead post', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2460, -260],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'update',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'ts': '={{ $json.leadTs }}', 'text': '={{ $json.text }}', 'updateFields': {}},
     'credentials': CRED_SLACK, 'onError': 'continueRegularOutput'},
    {'name': 'Add to its thread', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2680, -260],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': "={{ $('New or update?').item.json.channel }}", 'mode': 'id'},
                    'text': "={{ $('New or update?').item.json.threadText }}",
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False,
                                     'thread_ts': {'replyValues': {'thread_ts': "={{ $('New or update?').item.json.leadTs }}"}}}},
     'credentials': CRED_SLACK, 'onError': 'continueRegularOutput'},
    if_node('Save the number?', "={{ !!$json.learnMd }}", [2240, 700]),
    {'name': 'Save the number on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2460, 700],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.learnCardId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ markdown_content: $json.learnMd }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    code('Find the card', MATCH_CARD, [1420, -40]),
    code('Score and summarise', DELIVER, [2020, -40]),
    if_node('Lead score?', '={{ $json.postScore }}', [2240, -160]),
    if_node('Customer has a card?', '={{ $json.postComment }}', [2240, 60]),
    if_node('Issue raised?', '={{ $json.postIssue }}', [2240, 260]),
    {'name': 'Post lead score', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2460, -180],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'Post customer issue', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2460, 260],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.issueChannel }}', 'mode': 'id'},
                    'text': '={{ $json.issueText }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'Comment on card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2460, 20],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.taskId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP,
     'disabled': not LIVE,
     'alwaysOutputData': False},

    # --- #12: claims raised on calls/texts get a card in Claims Intake ---
    if_node('Damage claim?', '={{ $json.isClaim }}', [2240, 480]),
    {'name': 'Claims Intake cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2460, 480],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'include_closed', 'value': 'true'}]},
                    'options': {}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('New claim or a note?', CLAIM_DECIDE, [2680, 480]),
    if_node('Claim already on the board?', '={{ $json.newClaim }}', [2900, 480]),
    {'name': 'Create claim card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [3120, 400],
     'parameters': {'method': 'POST', 'url': f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.claimBody) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Note on the claim card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [3120, 560],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.claimTaskId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.claimComment, notify_all: false }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    if_node('AI moves the card?', '={{ !!$json.moveTo }}', [2240, 700]),
    {'name': 'Move the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2460, 700],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.taskId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status: $json.moveTo }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Claim alert', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [3340, 480],
     'parameters': {'mode': 'runOnceForEachItem', 'jsCode': CLAIM_ALERT.strip()}},
    {'name': 'Post the claim', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [3560, 480],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

CLAIM_CARD_WF = (ROOT / 'workflows/claim-card.id').read_text().strip()
CALL_HANDOVER = """
// What #6b needs to fill the team's claim form, from a call or a text thread.
return $input.all().map(item => {
  const j = item.json, a = j.analysis || {};
  const name = a.customerName || String(j.cardName || '').split('|')[0].trim();
  return { json: {
    sourceLabel: j.kind === 'call' ? `Phone call (${j.direction === 'Inbound' ? 'inbound' : 'outbound'}, ${Math.max(1, Math.round((j.duration || 0) / 60))} min)` : 'Text messages',
    reported: j.startTime ? Date.parse(j.startTime) : Date.now(),
    customerName: name, phone: j.customerNumber || '', email: '',
    item: a.description || '', whatHappened: a.summary || a.description || '', summary: a.summary || '',
    original: (Array.isArray(a.nextSteps) && a.nextSteps.length ? 'Next: ' + a.nextSteps.join(' · ') : ''),
  } };
});
"""

nodes = with_retry(nodes, ['Get all cards'])
one = lambda n, i=0: [{'node': n, 'type': 'main', 'index': i}]
connections = {
    'Call transcript': {'main': [one('Read transcript')]},
    'Read transcript': {'main': [one('Merge conversations')]},
    'Every 10 minutes': {'main': [one('RingCentral token')]},
    'RingCentral token': {'main': [one('Each company line')]},
    'Each company line': {'main': [one('Read texts')]},
    'Read texts': {'main': [one('Build text threads')]},
    'Build text threads': {'main': [one('Merge conversations', 1)]},
    'Merge conversations': {'main': [one('Staff numbers')]},
    'Staff numbers': {'main': [one('Get all cards')]},
    'Get all cards': {'main': [one('Which other boards')]},
    'Which other boards': {'main': [one('Other boards')]},
    'Other boards': {'main': [one('Find the card')]},
    'Find the card': {'main': [one('Ask Claude')]},
    'Ask Claude': {'main': [one('Claude analysis')]},
    'Claude analysis': {'main': [one('Recent lead posts')]},
    'Recent lead posts': {'main': [one('Score and summarise')]},
    'Score and summarise': {'main': [[{'node': 'Lead score?', 'type': 'main', 'index': 0},
                                      {'node': 'Customer has a card?', 'type': 'main', 'index': 0},
                                      {'node': 'Issue raised?', 'type': 'main', 'index': 0},
                                      {'node': 'Damage claim?', 'type': 'main', 'index': 0},
                                      {'node': 'AI moves the card?', 'type': 'main', 'index': 0},
                                      {'node': 'Save the number?', 'type': 'main', 'index': 0}]]},
    'Save the number?': {'main': [one('Save the number on the card'), []]},
    'AI moves the card?': {'main': [one('Move the card'), []]},
    'Lead score?': {'main': [[{'node': 'New or update?', 'type': 'main', 'index': 0}], []]},
    'New or update?': {'main': [one('Update the lead post'), one('Post lead score')]},
    'Update the lead post': {'main': [one('Add to its thread')]},
    'Issue raised?': {'main': [[{'node': 'Post customer issue', 'type': 'main', 'index': 0}], []]},
    'Damage claim?': {'main': [one('Claims Intake cards'), []]},
    'Claims Intake cards': {'main': [one('New claim or a note?')]},
    'New claim or a note?': {'main': [one('Claim already on the board?')]},
    'Claim already on the board?': {'main': [one('Create claim card'), one('Note on the claim card')]},
    'Create claim card': {'main': [one('Claim alert')]},
    'Note on the claim card': {'main': [one('Claim alert')]},
    'Claim alert': {'main': [one('Post the claim')]},
    # no ClickUp card for that number -> the Slack post is the only output
    'Customer has a card?': {'main': [[{'node': 'Comment on card', 'type': 'main', 'index': 0}], []]},
}

# claim cards now come from #6b (the team's own claims section), with the form's fields filled in
DROP = {'Claims Intake cards', 'New claim or a note?', 'Claim already on the board?', 'Create claim card',
        'Note on the claim card', 'Claim alert', 'Post the claim'}
nodes = [n for n in nodes if n['name'] not in DROP] + [
    code('For the claims section', CALL_HANDOVER, [2460, 480]),
    {'name': 'Hand over the claim', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2, 'position': [2680, 480],
     'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': CLAIM_CARD_WF, 'mode': 'id'},
                    'mode': 'once', 'options': {'waitForSubWorkflow': False}},
     'disabled': not LIVE}]
connections = {k: v for k, v in connections.items() if k not in DROP}
connections['Damage claim?'] = {'main': [one('For the claims section'), []]}
connections['For the claims section'] = {'main': [one('Hand over the claim')]}

wf = {'name': '#1/#3 Conversation AI: lead score + call summary' + ('' if LIVE else ' [TEST MODE - no ClickUp comment]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/conversations.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
