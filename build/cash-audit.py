#!/usr/bin/env python3
"""Builds workflows/cash-audit.json — #30 Cash Payment Audit (2026-11-16). Rules in lib/cash-audit.js.

Runs at 9 AM (yesterday), 1 PM, 5 PM and 9 PM (today). One day of evidence: the jobs on the Connecteam
schedule, the crew's texts on the 3 company lines, call transcripts (n8n table call_transcripts, filled by
worker/transcribe-calls.py), #invoices posts, the day's invoices, and the Cash Payments sheet month tab.
Claude lists the cash payments and the workflow only REPORTS (client 2026-11-17): nothing is ever written to the sheet.
Every hour 8:30 AM-8:30 PM (client 2026-11-18): each post that is still open gets a reminder in its thread, @mentioning
REMIND, until the payment is in the sheet or someone reacts :white_check_mark: / :no_entry_sign: on the post.

  python3 build/cash-audit.py           preview: posts to #automation-errors, the sheet is NOT changed
  python3 build/cash-audit.py --live    live: adds rows to Cash Payments, posts in #invoices
  add --hook for a webhook test copy that takes ?day=YYYY-MM-DD (never deploy it)
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE, HOOK = '--live' in sys.argv, '--hook' in sys.argv
ERRORS = 'C0C2X8B9VS9'
INVOICES = 'C0C1ZV783V1'
CHANNEL = INVOICES if LIVE else ERRORS
SHEET = '1J5uas1c-z1A9Rk9JPTMkVEMcY01A2_w1dbn3cs8D668'
SHEET_URL = f'https://docs.google.com/spreadsheets/d/{SHEET}/edit'
TABLE = (ROOT / 'workflows/call-transcripts.table').read_text().strip()
SCHEDULER_ID = 16427123
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']
REMIND = ['U0CFLEYGYTT', 'U0BJMJTMBA5']   # Lloyd Cortez, Genesis David (same people as the invoice issues)
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_N8N = {'httpHeaderAuth': {'id': 'Bc7JADy3VUTvng5V', 'name': 'n8n API (self)'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_SHEETS = {'googleSheetsOAuth2Api': {'id': 'm3PnEzDECqHcJrrF', 'name': 'Google Sheets - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = ''.join(strip((ROOT / f'lib/{f}.js').read_text()) + '\n' for f in ('parse-card', 'parse-invoice', 'cash-audit'))

DAY = LIB + f"""
// Which day: the 9 AM run looks at yesterday (late payments), the others at today.
const q = {"$('Every few hours').first().json.query?.day" if HOOK else "null"};
const now = Date.now(), t = nyToday(now);
const hour = +new Intl.DateTimeFormat('en-US', {{ timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }}).format(new Date(now));
let y = t.y, m = t.m, d = t.d;
if (q) [y, m, d] = q.split('-').map(Number);
else if (hour < 11) {{ const p = new Date(Date.UTC(y, m - 1, d) - 864e5); y = p.getUTCFullYear(); m = p.getUTCMonth() + 1; d = p.getUTCDate(); }}
const day = `${{y}}-${{String(m).padStart(2, '0')}}-${{String(d).padStart(2, '0')}}`;
const from = toEpoch(y, m, d, 0, 0), to = toEpoch(y, m, d, 23, 59) + 60;
const g = s => new Date(s * 1000), nd = new Date(Date.UTC(y, m - 1, d) + 864e5);
return [{{ json: {{ day, tab: cashTab(day), from, to, fromIso: g(from).toISOString(), toIso: g(to).toISOString(),
  gmailQ: `from:noreply@movegistics.com subject:"Invoice:" after:${{y}}/${{m}}/${{d}} before:${{nd.getUTCFullYear()}}/${{nd.getUTCMonth() + 1}}/${{nd.getUTCDate()}}`,
  filter: JSON.stringify({{ type: 'and', filters: [{{ columnName: 'startTime', condition: 'gte', value: g(from).toISOString() }},
                                                 {{ columnName: 'startTime', condition: 'lte', value: g(to).toISOString() }}] }}) }} }}];
"""

LINES = """
const token = $input.first().json.access_token;
const w = $('Which day').first().json;
return %EXT%.map(ext => ({ json: { ext, token, dateFrom: w.fromIso, dateTo: w.toIso } }));
""".replace('%EXT%', json.dumps(EXTENSIONS))

PROMPT = LIB + """
// Everything for that day, in one prompt.
const w = $('Which day').first().json;
const ten = p => String(p || '').replace(/\\D/g, '').slice(-10);
const clock = iso => new Date(iso).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });
const users = $('Crew').first().json.data?.users || [];
const byId = {}, phones = {};
for (const u of users) {
  const name = `${u.firstName || ''} ${u.lastName || ''}`.trim();
  byId[u.userId] = u.firstName || name;
  if (ten(u.phoneNumber).length === 10) phones[ten(u.phoneNumber)] = name;
}
// jobs that day (one row per crew member → one job)
const jobsMap = new Map();
for (const s of $('Schedule').first().json.data?.shifts || []) {
  const k = s.title + '|' + s.startTime;
  const j = jobsMap.get(k) || { title: s.title, at: s.startTime, crew: new Set() };
  (s.assignedUserIds || []).forEach(u => byId[u] && j.crew.add(byId[u]));
  jobsMap.set(k, j);
}
const jobs = [...jobsMap.values()].sort((a, b) => a.at - b.at)
  .map(j => ({ title: j.title, time: clock(j.at * 1000), crew: [...j.crew].join(', ') }));
// crew texts on the 3 lines
const seen = new Set(), texts = [];
for (const m of $('Crew texts').all().flatMap(i => i.json.records || [])) {
  const from = ten(m.from?.phoneNumber);
  if (!phones[from] || seen.has(m.id)) continue;
  seen.add(m.id);
  texts.push({ id: m.id, at: m.creationTime, time: clock(m.creationTime), from: phones[from], text: String(m.subject || '').slice(0, 800) });
}
texts.sort((a, b) => a.at.localeCompare(b.at));
// calls: the crew's, or any that talk about cash
const calls = ($('Call transcripts').first().json.data || [])
  .filter(c => phones[ten(c.fromNumber)] || phones[ten(c.toNumber)] || /\\bcash\\b/i.test(c.transcript || ''))
  .sort((a, b) => String(a.startTime).localeCompare(String(b.startTime)))
  .map(c => ({ id: c.callId, time: clock(c.startTime),
    who: phones[ten(c.fromNumber)] ? `crew ${phones[ten(c.fromNumber)]} → office` : phones[ten(c.toNumber)] ? `office → crew ${phones[ten(c.toNumber)]}`
      : `${c.direction} ${c.fromName || c.fromNumber} → ${c.toName || c.toNumber}`,
    text: String(c.transcript || '').replace(/\\s+/g, ' ').slice(0, 2500) }));
// #invoices posts by people
const slack = ($('Slack posts').first().json.messages || []).filter(m => !m.bot_id && !m.subtype && m.text && !/Sent using|U0C1LC0PLPL/.test(m.text))   // not ChatGPT's old posts
  .sort((a, b) => +a.ts - +b.ts).map(m => ({ id: m.ts, time: clock(+m.ts * 1000), text: String(m.text).replace(/<@\\w+>/g, '@someone').slice(0, 800) }));
// the day's invoices (latest version of each number)
const inv = {};
for (const it of $('Read invoices').all()) {
  const p = parseInvoice(it.json.text || '');
  if (!p.number) continue;
  inv[p.number] = { number: p.number, customer: p.customer, lineTotal: p.lineTotal, fee: p.fee, balance: p.balance ?? p.grandTotal,
    deposit: (p.deposits || []).reduce((a, b) => a + b, 0), cashLine: (p.lines || []).some(l => /cash/i.test(l.desc)) };
}
const sheet = cashRows($('Cash Payments sheet').first().json.values || []);
const day = w.day, md = mdOf(day);
const nearRows = sheet.filter(r => Math.abs((+r.day.split('/')[1] || 0) - +day.slice(8, 10)) <= 2);
const enough = texts.length + calls.length + slack.length > 0;
return [{ json: { enough, day, tab: w.tab, sheetRows: sheet.length, counts: { jobs: jobs.length, texts: texts.length, calls: calls.length, slack: slack.length, invoices: Object.keys(inv).length },
  body: cashBody({ day, jobs, texts, calls, slack, invoices: Object.values(inv), sheet: nearRows }) } }];
"""

DECIDE = LIB + f"""
// What to add and what to flag. Every item is said once.
const p = $('Build the prompt').first().json;
const state = $getWorkflowStaticData('global');
state.said = state.said || {{}};
const LIVE = {json.dumps(LIVE)};
const res = parseCash($input.first().json);
if (res.error || $input.first().json.error) return [{{ json: {{ kind: 'msg', channel: '{ERRORS}', text: `:warning: Cash audit for ${{p.day}}: the AI step failed (${{res.error || $input.first().json.error?.message || ''}}). Nothing was changed.` }} }}];
const rows = cashRows($('Cash Payments sheet').first().json.values || []);
// preview remembers separately, so going live re-checks everything
const said = LIVE ? state.said : (state.previewSaid = state.previewSaid || {{}});
const acts = cashActions(res, rows, p.day, said);
const texts = cashMessages(acts, p.day, LIVE, '{SHEET_URL}');
const tracked = cashTracked(acts, p.day);
return texts.map((t, i) => ({{ json: {{ kind: 'msg', channel: '{CHANNEL}', text: t, track: LIVE ? tracked[i] : null }} }}));   // report only
"""

REMEMBER = """
// Keep what was posted, so the hourly run can remind until it is done.
const state = $getWorkflowStaticData('global');
state.open = state.open || {};
const sent = $('Decide').all();
$input.all().forEach((r, i) => {
  const t = sent[i]?.json?.track, ts = r.json.ts || r.json.message?.ts;
  if (t && t.key && ts) state.open[t.key] = { ...t, channel: r.json.channel || sent[i].json.channel, ts, postedAt: Date.now(), last: Date.now(), n: 0 };
});
return [{ json: { open: Object.keys(state.open).length } }];
"""

OPEN = """
const state = $getWorkflowStaticData('global');
return Object.values(state.open || {}).filter(o => o.ts).map(o => ({ json: o }));
"""

STILL = LIB + f"""
// Still open after an hour? Remind in the thread. Done? Say so once and stop.
const state = $getWorkflowStaticData('global');
state.open = state.open || {{}};
const who = {json.dumps(REMIND)}.map(u => `<@${{u}}>`).join(' and ');
const items = $('Open items').all(), reacts = $('Reactions').all(), sheets = $('Sheet now').all();
const out = [];
items.forEach((it, i) => {{
  const o = it.json, r = reacts[i]?.json || {{}}, sh = sheets[i]?.json || {{}};
  if (r.ok === false && /thread_not_found|message_not_found|channel_not_found/.test(r.error || '')) {{ delete state.open[o.key]; return; }}   // post deleted
  if (r.ok === false || sh.error || !Array.isArray(sh.values)) return;                       // could not check: try next hour
  const d = cashDone(o, (r.messages || [])[0]?.reactions || [], cashRows(sh.values));
  if (d.done) {{
    delete state.open[o.key];
    out.push({{ json: {{ channel: o.channel, thread: o.ts, text: `:white_check_mark: Closed - ${{d.how}}. No more reminders.` }} }});
    return;
  }}
  if (Date.now() - (o.last || o.postedAt) < 55 * 60e3) return;
  const n = (o.n || 0) + 1;
  state.open[o.key] = {{ ...o, n, last: Date.now() }};
  out.push({{ json: {{ channel: o.channel, thread: o.ts, text: cashReminder(o, n, who) }} }});
}});
return out;
"""


def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': {'jsCode': js.strip()}}


def if_node(name, left, pos):
    return {'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': pos,
            'parameters': {'conditions': {
                'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                'combinator': 'and',
                'conditions': [{'id': name, 'leftValue': left, 'rightValue': '',
                                'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                'options': {}}}


def http(name, pos, url, cred, query=None, method='GET', body=None, headers=None, once=True):
    p = {'method': method, 'url': url, 'options': {}}
    kind = next(iter(cred))
    if kind in ('httpHeaderAuth', 'httpBasicAuth'):
        p.update({'authentication': 'genericCredentialType', 'genericAuthType': kind})
    else:
        p.update({'authentication': 'predefinedCredentialType', 'nodeCredentialType': kind})
    if query:
        p.update({'sendQuery': True, 'queryParameters': {'parameters': [{'name': k, 'value': v} for k, v in query]}})
    if headers:
        p.update({'sendHeaders': True, 'headerParameters': {'parameters': [{'name': k, 'value': v} for k, v in headers]}})
    if body is not None:
        p.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos, 'parameters': p,
         'credentials': cred, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 5000,
         'alwaysOutputData': True, 'onError': 'continueRegularOutput'}
    if once:
        n['executeOnce'] = True
    return n


W = "$('Which day').first().json"
if HOOK:
    trigger = {'name': 'Every few hours', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [0, 0],
               'webhookId': 'cash-audit-test', 'parameters': {'path': 'cash-audit-test', 'httpMethod': 'GET', 'options': {}}}
else:
    trigger = {'name': 'Every few hours', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
               'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '0 9,13,17,21 * * *'}]}}}

nodes = [
    trigger,
    code('Which day', DAY, [200, 0]),
    http('Schedule', [400, 0], f'=https://api.connecteam.com/scheduler/v1/schedulers/{SCHEDULER_ID}/shifts'
         f'?startTime={{{{ {W}.from }}}}&endTime={{{{ {W}.to }}}}&limit=500', CRED_CONNECTEAM),
    http('Crew', [600, 0], 'https://api.connecteam.com/users/v1/users?limit=500', CRED_CONNECTEAM),
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [800, 0],
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC, 'executeOnce': True, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 20000},
    code('Each company line', LINES, [1000, 0]),
    {'name': 'Crew texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1200, 0],
     'parameters': {'method': 'GET',
                    'url': '=https://platform.ringcentral.com/restapi/v1.0/account/~/extension/{{ $json.ext }}/message-store',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'messageType', 'value': 'SMS'}, {'name': 'direction', 'value': 'Inbound'},
                        {'name': 'dateFrom', 'value': '={{ $json.dateFrom }}'}, {'name': 'dateTo', 'value': '={{ $json.dateTo }}'},
                        {'name': 'perPage', 'value': '1000'}]},
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'Authorization', 'value': '=Bearer {{ $json.token }}'}]},
                    'options': {}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    http('Call transcripts', [1400, 0], f'https://n8n.expresshaulers.com/api/v1/data-tables/{TABLE}/rows', CRED_N8N,
         query=[('filter', f'={{{{ {W}.filter }}}}'), ('limit', '250')]),
    http('Slack posts', [1600, 0], 'https://slack.com/api/conversations.history', CRED_SLACK,
         query=[('channel', INVOICES), ('oldest', f'={{{{ {W}.from }}}}'), ('latest', f'={{{{ {W}.to + 6 * 3600 }}}}'), ('limit', '300')]),
    {'name': 'Invoices that day', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [1800, 0],
     'parameters': {'resource': 'message', 'operation': 'getAll', 'returnAll': False, 'limit': 40, 'simple': False,
                    'filters': {'q': f'={{{{ {W}.gmailQ }}}}'}, 'options': {'downloadAttachments': True}},
     'credentials': CRED_GMAIL, 'executeOnce': True, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Read invoices', 'type': 'n8n-nodes-base.extractFromFile', 'typeVersion': 1, 'position': [2000, 0],
     'parameters': {'operation': 'pdf', 'binaryPropertyName': 'attachment_0', 'options': {}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    http('Cash Payments sheet', [2200, 0],
         f"=https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/{{{{ encodeURIComponent(\"'\" + {W}.tab + \"'!A1:G400\") }}}}",
         CRED_SHEETS),
    code('Build the prompt', PROMPT, [2400, 0]),
    if_node('Anything to read?', '={{ $json.enough }}', [2600, 0]),
    {'name': 'Find the cash payments', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2800, -100],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.body) }}',
                    'options': {'timeout': 300000}},
     'credentials': CRED_ANTHROPIC, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 30000,
     'onError': 'continueRegularOutput'},
    code('Decide', DECIDE, [3000, -100]),
    if_node('Add a row?', "={{ $json.kind === 'add' }}", [3200, -100]),
    {'name': 'Post', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [3640, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]
one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
order = ['Every few hours', 'Which day', 'Schedule', 'Crew', 'RingCentral token', 'Each company line', 'Crew texts',
         'Call transcripts', 'Slack posts', 'Invoices that day', 'Read invoices', 'Cash Payments sheet', 'Build the prompt',
         'Anything to read?']
connections = {a: {'main': [one(b)]} for a, b in zip(order, order[1:])}
connections['Anything to read?'] = {'main': [one('Find the cash payments'), []]}
connections['Find the cash payments'] = {'main': [one('Decide')]}
connections['Decide'] = {'main': [one('Add a row?')]}
if False:   # report only (client, 2026-11-17): never write to Cash Payments
    nodes.append(http('Add to the sheet', [3420, -200],
                      f"=https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/{{{{ encodeURIComponent(\"'\" + $json.tab + \"'!A:G\") }}}}:append",
                      CRED_SHEETS, query=[('valueInputOption', 'USER_ENTERED')], method='POST',
                      body='={{ JSON.stringify({ values: [$json.row] }) }}', once=False))
    nodes.append(code('Added?', f"""
// post the 🟢 note only when the row really went in
return $input.all().map((r, i) => {{
  const a = $('Add a row?').all()[i]?.json || {{}};
  return r.json.updates ? {{ json: {{ channel: a.channel, text: a.text }} }}
    : {{ json: {{ channel: '{ERRORS}', text: `:warning: Cash audit could not add a row for ${{(a.row || [])[0]}} to ${{a.tab}}: ${{r.json.error?.message || 'no reply'}}` }} }};
}});
""", [3530, -200]))
    connections['Add a row?'] = {'main': [one('Add to the sheet'), one('Post')]}
    connections['Add to the sheet'] = {'main': [one('Added?')]}
    connections['Added?'] = {'main': [one('Post')]}
else:
    connections['Add a row?'] = {'main': [one('Post'), one('Post')]}
if 'Add to the sheet' not in [n['name'] for n in nodes]:
    connections['Add a row?'] = {'main': [one('Post'), one('Post')]}
connections['Post'] = {'main': [one('Remember')]}

# ---- the hourly reminders -------------------------------------------------------------------------
# at :30 so they never run at the same moment as the audit (:00) - both write the workflow's memory
nodes += [
    code('Remember', REMEMBER, [3860, 0]),
    {'name': 'Every hour', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 400],
     'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '30 8-20 * * *'}]}}},
    code('Open items', OPEN, [200, 400]),
    # the post with its reactions (reactions.get needs a scope the bot does not have)
    http('Reactions', [400, 400], 'https://slack.com/api/conversations.replies', CRED_SLACK,
         query=[('channel', '={{ $json.channel }}'), ('ts', '={{ $json.ts }}'), ('limit', '1')], once=False),
    http('Sheet now', [600, 400],
         f"=https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/{{{{ encodeURIComponent(\"'\" + $('Open items').item.json.tab + \"'!A1:G400\") }}}}",
         CRED_SHEETS, once=False),
    code('Still open?', STILL, [800, 400]),
    {'name': 'Remind', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1000, 400],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False,
                                     'thread_ts': {'replyValues': {'thread_ts': '={{ $json.thread }}'}}}},
     'credentials': CRED_SLACK, 'onError': 'continueRegularOutput'},
]
for a, b in [('Every hour', 'Open items'), ('Open items', 'Reactions'), ('Reactions', 'Sheet now'),
             ('Sheet now', 'Still open?'), ('Still open?', 'Remind')]:
    connections[a] = {'main': [one(b)]}

wf = {'name': '#30 Cash payment audit' + ('' if LIVE else ' [PREVIEW - sheet not changed]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}
out = ROOT / ('workflows/cash-audit-hook.json' if HOOK else 'workflows/cash-audit.json')
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'PREVIEW', '(webhook test copy)' if HOOK else '')
