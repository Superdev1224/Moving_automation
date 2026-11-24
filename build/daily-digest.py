#!/usr/bin/env python3
"""Builds workflows/daily-digest.json — the 8 AM "yesterday in review" post.

Reads yesterday straight from the source systems (ClickUp, Stripe, RingCentral, Connecteam)
plus what the other automations did (from n8n's own execution history), then Claude writes
the digest and posts it to Slack #daily-digest-and-updates.

  python3 build/daily-digest.py          test mode: posts to #automation-errors
  python3 build/daily-digest.py --live   posts to #daily-digest-and-updates
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                                  # #automation-errors
DIGEST = 'C09D8S04H62'                                  # #daily-digest-and-updates
CHANNEL = DIGEST if LIVE else ERRORS
LIST_ID = '901705949004'
CLAIMS_LIST = '901705953745'   # the "Damage Claims" space, list "List"
SCHEDULER = '16427123'
CONV_WF = 'lRo5fyjETYPQNJgu'                            # conversation AI (lead scores)

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_STRIPE = {'stripeApi': {'id': 'bn8XQuRj56UsqRMB', 'name': 'Stripe - Express Haulers (read-only)'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
CRED_N8N = {'httpHeaderAuth': {'id': 'Bc7JADy3VUTvng5V', 'name': 'n8n API (self)'}}

RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

WINDOW = """
// Yesterday, 00:00-23:59 New York time, expressed the way each API wants it.
const TZ = 'America/New_York';
function tzOffsetMin(utcMs) {
  const p = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hourCycle: 'h23', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .formatToParts(new Date(utcMs)).reduce((o, x) => (o[x.type] = x.value, o), {});
  return (Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - utcMs) / 60000;
}
const toEpoch = (y, m, d, hh, mm) => {
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  return guess - tzOffsetMin(guess - tzOffsetMin(guess) * 60000) * 60000;
};
const now = Date.now();
const [m, d, y] = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric', month: 'numeric', day: 'numeric' })
  .format(new Date(now)).split('/').map(Number);
const todayStart = toEpoch(y, m, d, 0, 0);
const start = todayStart - 86400000;          // yesterday 00:00 ET
const end = todayStart;                       // today 00:00 ET
const iso = ms => new Date(ms).toISOString().replace(/\\.\\d+Z$/, 'Z');
const dayName = new Date(start).toLocaleDateString('en-US', { timeZone: TZ, weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
return [{ json: {
  start, end, dayName,
  startIso: iso(start), endIso: iso(end),
  startSec: Math.floor(start / 1000), endSec: Math.floor(end / 1000),
  todayEndSec: Math.floor((todayStart + 2 * 86400000) / 1000),   // shifts for today + tomorrow
  todayStartSec: Math.floor(todayStart / 1000),
} }];
"""


SLACK_WORKSPACE = 'https://expresshaulersmoving.slack.com'

CHANNELS_TO_READ = """
// Every public channel the bot is in (the digest channel itself is skipped).
const w = $('Yesterday window').first().json;
const chans = ($input.first().json.channels || []).filter(c => c.is_member && c.name !== 'daily-digest-and-updates');
return chans.map(c => ({ json: { id: c.id, name: c.name, oldest: w.startSec, latest: w.endSec } }));
"""

SLACK_MESSAGES = """
// Yesterday's Slack messages, with who wrote them and a link back to each one.
const users = {};
try { for (const u of $('Slack: people').first().json.members || []) users[u.id] = u.real_name || u.name; } catch (e) {}
const asked = $('Channels to read').all();
const out = [];
$input.all().forEach((item, i) => {
  const ch = asked[i]?.json; if (!ch) return;
  for (const m of item.json.messages || []) {
    if (m.subtype && !['bot_message', 'thread_broadcast', 'file_share'].includes(m.subtype)) continue;
    const text = String(m.text || '').replace(/<(https?:[^|>]+)\\|?[^>]*>/g, '$1').replace(/\\s+/g, ' ').trim();
    if (!text || /^:sunny: \\*Daily digest/.test(text)) continue;
    out.push({ channel: ch.name, who: users[m.user] || m.username || (m.bot_id ? 'automation' : 'someone'),
               text: text.slice(0, 350),
               link: `%WS%/archives/${ch.id}/p${String(m.ts).replace('.', '')}` });
  }
});
return [{ json: { messages: out.slice(0, 300), count: out.length, readable: $input.all().some(i => i.json.ok) } }];
""".replace('%WS%', SLACK_WORKSPACE)

ASSEMBLE = """
// Pull the pieces together into one compact brief for Claude.
const w = $('Yesterday window').first().json;
const get = (node, path, dflt) => { try { const v = $(node).all(); return path(v); } catch (e) { return dflt; } };

// --- ClickUp cards touched yesterday ---
const cards = get('ClickUp: cards touched', a => a.flatMap(i => i.json.tasks || []), []);
const byStatus = {};
for (const t of cards) byStatus[t.status?.status || '?'] = (byStatus[t.status?.status || '?'] || 0) + 1;
const newCards = cards.filter(t => +t.date_created >= w.start && +t.date_created < w.end);
const short = t => ({ name: (t.name || '').replace(/\\s+/g, ' ').slice(0, 70), status: t.status?.status, url: t.url });

// --- claims ---
const claims = get('ClickUp: claims', a => a.flatMap(i => i.json.tasks || []), [])
  .filter(t => +t.date_created >= w.start && +t.date_created < w.end).map(short);

// --- money ---
const charges = get('Stripe: payments', a => a.flatMap(i => i.json.data || []), [])
  .filter(c => c.created * 1000 >= w.start && c.created * 1000 < w.end && c.paid);
const deposits = charges.filter(c => c.amount === 5200);
const otherPayments = charges.filter(c => c.amount !== 5200);
const sum = list => Math.round(list.reduce((a, c) => a + c.amount, 0)) / 100;

// --- phone activity ---
const calls = get('RingCentral: calls', a => a.flatMap(i => i.json.records || []), []);
const inbound = calls.filter(c => c.direction === 'Inbound');
const missed = inbound.filter(c => ['Missed', 'Voicemail', 'Rejected'].includes(c.result));
const talkMin = Math.round(calls.reduce((a, c) => a + (c.duration || 0), 0) / 60);

// --- what the automations did (from n8n's own history) ---
const runs = get('n8n: yesterday runs', a => a.map(i => i.json), []);
const scores = runs.flatMap(r => r.scores || []);
const heat = { hot: 0, warm: 0, cold: 0 };
for (const s of scores) if (heat[s.temperature] !== undefined) heat[s.temperature]++;

// --- crew schedule for today ---
const shifts = get('Connecteam: upcoming shifts', a => a.flatMap(i => i.json.data?.shifts || []), [])
  .filter(s => s.startTime >= w.todayStartSec && s.startTime < w.todayStartSec + 86400)
  .map(s => ({ title: (s.title || '').slice(0, 70), start: new Date(s.startTime * 1000).toLocaleString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) }));

// --- moves actually BOOKED yesterday ---
// ClickUp has no status-change history, so we remember each card's status and compare. A card counts
// as booked yesterday only if we last saw it in another status (or it is a brand-new card).
const store = $getWorkflowStaticData('global');
const seenStatus = store.cardStatus || {};
const isBooked = t => t.status?.status === 'moves booked';
const bornRecently = t => +t.date_created >= w.start - 14 * 86400000;
const bookedYesterday = cards.filter(t => isBooked(t) &&
  (seenStatus[t.id] ? seenStatus[t.id].s !== 'moves booked' : bornRecently(t)));
for (const t of cards) seenStatus[t.id] = { s: t.status?.status || '', at: w.end };
for (const [id, v] of Object.entries(seenStatus)) if (w.end - (v.at || 0) > 120 * 86400000) delete seenStatus[id];
store.cardStatus = seenStatus;

const brief = {
  day: w.dayName,
  clickup: { touched: cards.length, byStatus, newCards: newCards.map(short).slice(0, 12),
             movesBookedYesterday: bookedYesterday.length,
             bookedYesterday: bookedYesterday.map(short).slice(0, 10),
             bookedCardsTouched: cards.filter(isBooked).length,
             pending: cards.filter(t => t.status?.status === 'pending').map(short).slice(0, 8) },
  claims,
  money: { depositCount: deposits.length, depositTotal: sum(deposits),
           otherPaymentCount: otherPayments.length, otherPaymentTotal: sum(otherPayments) },
  phone: { total: calls.length, inbound: inbound.length, missed: missed.length, talkMinutes: talkMin },
  conversations: { analysed: scores.length, heat,
                   hotLeads: scores.filter(s => s.temperature === 'hot').slice(0, 10),
                   warmLeads: scores.filter(s => s.temperature === 'warm').slice(0, 10) },
  automationProblems: runs.filter(r => r.failed).length,
  todayShifts: shifts,
};
const slack = get('Slack messages', a => a[0].json, { messages: [], readable: false });

const system = [
  'You write the morning digest for Express Haulers Moving (New Jersey) about YESTERDAY, for the owner and team.',
  'You get two things: SYSTEM DATA (counts from ClickUp, Stripe, RingCentral, Connecteam and the automations) and',
  'SLACK MESSAGES from yesterday, each with a link. Use both. Never invent facts, names, amounts or links.',
  'Format for Slack: *bold* headings, bullets starting with "• ". Link to sources with <link|Source>, or with the',
  'customer name as the link text (e.g. <link|Natalia>) when a bullet mentions several people. Only use links you were given.',
  'The team said the digest was far too long: it must be readable in 20 seconds. Use exactly this format:',
  'Line 1: "*Yesterday:* " then key numbers joined by " · " (calls with missed in brackets, moves booked, money in, hot leads).',
  'For "moves booked" use clickup.movesBookedYesterday: the moves that BECAME booked yesterday. Never use',
  'clickup.bookedCardsTouched (already-booked cards that were merely edited) or the count of cards in any status.',
  '*Top 3* : exactly 3 bullets, the most important things only, each under 18 words, each with one link.',
  '*Urgent* : only if there are claims, complaints or problems needing action today; at most 2 bullets. Leave it out otherwise.',
  '*Today* : at most 3 lines starting "• [ ] ", the most important actions for today.',
  'No other sections. Do not list every customer, every booking or every lead; pick what matters most.',
  'Never write "owner and due date not stated". Hard limit: 120 words for the whole post.',
].join(' ');

return [{ json: { channel: '%CHANNEL%', brief, claudeBody: {
  model: 'claude-opus-5', max_tokens: 3000, system,
  messages: [{ role: 'user', content: 'Yesterday: ' + w.dayName
    + '\\n\\nSYSTEM DATA:\\n' + JSON.stringify(brief, null, 1).slice(0, 40000)
    + '\\n\\nSLACK MESSAGES (' + (slack.readable ? slack.messages.length : 'not available: the Slack app cannot read messages yet') + '):\\n'
    + slack.messages.map(m => `[#${m.channel}] ${m.who}: ${m.text} (${m.link})`).join('\\n').slice(0, 60000) }],
} } }];
"""

RUNS = """
// Yesterday's conversation analyses + failed runs, read from n8n's own execution history.
const w = $('Yesterday window').first().json;
const execs = $input.all().flatMap(i => i.json.data || []);
const mine = execs.filter(e => {
  const t = Date.parse(e.startedAt || '');
  return t >= w.start && t < w.end;
});
return [{ json: {
  failed: mine.filter(e => e.status === 'error').length,
  ids: mine.filter(e => e.status !== 'error').map(e => e.id).slice(0, 60),
} }];
"""

SCORES = """
// Pull the lead scores out of each saved run.
const out = [];
for (const item of $input.all()) {
  const run = item.json?.data?.resultData?.runData?.['Score and summarise'];
  for (const entry of run || []) {
    for (const i of entry.data?.main?.[0] || []) {
      const a = i.json?.analysis;
      if (!a) continue;
      out.push({ temperature: a.temperature, name: a.customerName || i.json.customerNumber,
                 what: a.description, kind: i.json.kind, url: i.json.taskId ? 'https://app.clickup.com/t/' + i.json.taskId : null });
    }
  }
}
return [{ json: { scores: out, failed: $('Yesterday runs list').first().json.failed } }];
"""

def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}

def http(name, pos, url, cred=None, generic=None, predefined=None, qs=None, headers=None,
         method='GET', body=None, paginate=False, once=True):
    p = {'method': method, 'url': url, 'options': {}}
    if predefined:
        p['authentication'] = 'predefinedCredentialType'; p['nodeCredentialType'] = predefined
    elif generic:
        p['authentication'] = 'genericCredentialType'; p['genericAuthType'] = generic
    if qs:
        p['sendQuery'] = True
        p['queryParameters'] = {'parameters': [{'name': k, 'value': v} for k, v in qs]}
    if headers:
        p['sendHeaders'] = True
        p['headerParameters'] = {'parameters': [{'name': k, 'value': v} for k, v in headers]}
    if body is not None:
        p['sendBody'] = True; p['specifyBody'] = 'json'; p['jsonBody'] = body
    if paginate:
        p['options']['pagination'] = {'pagination': {
            'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
            'paginationCompleteWhen': 'other',
            'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
            'limitPagesFetched': True, 'maxRequests': 25}}
    node = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
            'parameters': p, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'}
    if once:
        node['parameters']['executeOnce'] = True
    if cred:
        node['credentials'] = cred
    return node

nodes = [
    {'name': 'Every day 8:00 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 8, 'triggerAtMinute': 0}]}}},
    code('Yesterday window', WINDOW, [220, 0]),

    http('ClickUp: cards touched', [460, -300],
         f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
         CRED_CLICKUP, predefined='clickUpApi', paginate=True,
         qs=[('date_updated_gt', '={{ $json.start }}'), ('include_closed', 'true'),
             ('include_markdown_description', 'false')]),
    http('ClickUp: claims', [460, -160],
         f'https://api.clickup.com/api/v2/list/{CLAIMS_LIST}/task',
         CRED_CLICKUP, predefined='clickUpApi',
         qs=[('date_created_gt', '={{ $json.start }}'), ('include_closed', 'true')]),
    http('Stripe: payments', [460, -20],
         'https://api.stripe.com/v1/charges', CRED_STRIPE, predefined='stripeApi',
         qs=[('created[gte]', '={{ $json.startSec }}'), ('created[lt]', '={{ $json.endSec }}'), ('limit', '100')]),
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [460, 120],
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}, 'executeOnce': True},
     'credentials': CRED_RC_BASIC},
    http('RingCentral: calls', [680, 120],
         'https://platform.ringcentral.com/restapi/v1.0/account/~/call-log',
         qs=[('dateFrom', "={{ $('Yesterday window').first().json.startIso }}"),
             ('dateTo', "={{ $('Yesterday window').first().json.endIso }}"),
             ('perPage', '250'), ('view', 'Simple')],
         headers=[('Authorization', '=Bearer {{ $json.access_token }}')]),
    http('Connecteam: upcoming shifts', [460, 260],
         f'https://api.connecteam.com/scheduler/v1/schedulers/{SCHEDULER}/shifts',
         CRED_CONNECTEAM, generic='httpHeaderAuth',
         qs=[('startTime', '={{ $json.todayStartSec }}'), ('endTime', '={{ $json.todayEndSec }}'), ('limit', '500')]),
    http('n8n: conversation runs', [460, 400],
         f'https://n8n.expresshaulers.com/api/v1/executions?workflowId={CONV_WF}&limit=250',
         CRED_N8N, generic='httpHeaderAuth'),
    code('Yesterday runs list', RUNS, [680, 400]),
    {'name': 'Split run ids', 'type': 'n8n-nodes-base.splitOut', 'typeVersion': 1, 'position': [900, 400],
     'parameters': {'fieldToSplitOut': 'ids', 'options': {}},
     'alwaysOutputData': True},
    http('n8n: run detail', [1120, 400],
         '=https://n8n.expresshaulers.com/api/v1/executions/{{ $json.ids }}?includeData=true',
         CRED_N8N, generic='httpHeaderAuth', once=False),
    code('n8n: yesterday runs', SCORES, [1340, 400]),

    http('Slack: people', [1560, 400], 'https://slack.com/api/users.list?limit=500', CRED_SLACK, predefined='slackApi'),
    http('Slack: channels', [1780, 400],
         'https://slack.com/api/conversations.list?types=public_channel&exclude_archived=true&limit=200',
         CRED_SLACK, predefined='slackApi'),
    code('Channels to read', CHANNELS_TO_READ, [2000, 400]),
    http('Slack: history', [2220, 400],
         '=https://slack.com/api/conversations.history?channel={{ $json.id }}&oldest={{ $json.oldest }}&latest={{ $json.latest }}&limit=200',
         CRED_SLACK, predefined='slackApi', once=False),
    code('Slack messages', SLACK_MESSAGES, [2440, 400]),
    code('Build the brief', ASSEMBLE.replace('%CHANNEL%', CHANNEL), [1560, 0]),
    {'name': 'Claude writes it', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1780, 0],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.claudeBody) }}',
                    'options': {'timeout': 180000}},
     'credentials': CRED_ANTHROPIC},
    code('Format for Slack', """
const w = $('Yesterday window').first().json;
const res = $input.first().json;
const text = (res.content || []).filter(c => c.type === 'text').map(c => c.text).join('\\n').trim();
return [{ json: { channel: $('Build the brief').first().json.channel,
                  text: `:sunny: *Daily digest — ${w.dayName}*\\n\\n${text}` } }];
""", [2000, 0]),
    {'name': 'Post digest', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2220, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
]

one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
connections = {
    'Every day 8:00 AM': {'main': [one('Yesterday window')]},
    'Yesterday window': {'main': [[{'node': n, 'type': 'main', 'index': 0} for n in
                                   ['ClickUp: cards touched', 'ClickUp: claims', 'Stripe: payments',
                                    'RingCentral token', 'Connecteam: upcoming shifts', 'n8n: conversation runs']]]},
    'RingCentral token': {'main': [one('RingCentral: calls')]},
    'n8n: conversation runs': {'main': [one('Yesterday runs list')]},
    'Yesterday runs list': {'main': [one('Split run ids')]},
    'Split run ids': {'main': [one('n8n: run detail')]},
    'n8n: run detail': {'main': [one('n8n: yesterday runs')]},
    # the brief waits for the slowest branch (the run details)
    'n8n: yesterday runs': {'main': [one('Slack: people')]},
    'Slack: people': {'main': [one('Slack: channels')]},
    'Slack: channels': {'main': [one('Channels to read')]},
    'Channels to read': {'main': [one('Slack: history')]},
    'Slack: history': {'main': [one('Slack messages')]},
    'Slack messages': {'main': [one('Build the brief')]},
    'Build the brief': {'main': [one('Claude writes it')]},
    'Claude writes it': {'main': [one('Format for Slack')]},
    'Format for Slack': {'main': [one('Post digest')]},
}

wf = {'name': '#15 Daily digest → Slack' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/daily-digest.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
