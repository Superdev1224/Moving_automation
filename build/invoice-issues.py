#!/usr/bin/env python3
"""Builds workflows/invoice-issues.json — #32 Invoice issues: threads, assignee, 3 h escalation, sheet, daily report.

Client spec "Invoice Auditor | Complete Developer Specification" (2026-11-16), sections 8-12. #5 saves one audit
per invoice (version) in the n8n table invoice_audits; this workflow, every 5 minutes:
  - posts ONE thread per new issue (only problems; Passed invoices stay silent), updates it when a revised
    invoice changes the finding, closes it when the revision fixes it;
  - no claiming (client 2026-11-17): every issue is assigned to ASSIGNEES (Lloyd Cortez + Genesis David). :white_check_mark: = Resolved,
    :no_entry_sign: = Dismissed; the latest reply in the thread is the resolution note;
  - any issue still open 3 hours after it was posted is escalated to Diego (any amount);
  - mirrors invoice_audits / audit_issues / audit_log into the Google Sheet "Invoice Audit";
  - every day at 7:45 PM: report of the past 24 hours (since the last report, never twice a day).
Every change is logged in audit_log.

  python3 build/invoice-issues.py          preview: threads in #automation-errors, nobody is @mentioned
  python3 build/invoice-issues.py --live   live: threads in #invoices, the assignee and Diego are @mentioned
  --assign=<Slack user id>                  who every issue is assigned to (default Lloyd Cortez + Genesis David)
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
INVOICES, ERRORS = 'C0C1ZV783V1', 'C0C2X8B9VS9'
CHANNEL = INVOICES if LIVE else ERRORS
# client 2026-11-17: every issue is assigned to Lloyd Cortez and Genesis David (override with --assign=U1,U2)
ASSIGNEES = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--assign=')), 'U0CFLEYGYTT,U0BJMJTMBA5').split(',')
T = json.loads((ROOT / 'workflows/audit-tables.json').read_text())
SHEET = (ROOT / 'workflows/invoice-audit-sheet.id').read_text().strip()
API = 'https://n8n.expresshaulers.com/api/v1/data-tables'
CRED_N8N = {'httpHeaderAuth': {'id': 'Bc7JADy3VUTvng5V', 'name': 'n8n API (self)'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_SHEETS = {'googleSheetsOAuth2Api': {'id': 'm3PnEzDECqHcJrrF', 'name': 'Google Sheets - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/invoice-issues.js').read_text())

OPEN = """
// one item per open issue thread (to read its reactions and replies); a placeholder when there is none
const rows = $('All issues').all().flatMap(i => i.json.data || []);
const open = rows.filter(r => r.slackTs && !['Resolved', 'Dismissed'].includes(r.status));
const closedNoNote = rows.filter(r => r.slackTs && ['Resolved', 'Dismissed'].includes(r.status) && !r.resolution
  && Date.now() - Date.parse(r.resolvedAt || 0) < 7 * 864e5);
const all = [...open, ...closedNoNote];
return all.length ? all.map(r => ({ json: { issueId: r.issueId, ts: r.slackTs } })) : [{ json: { issueId: '', ts: '0' } }];
"""

PLAN = LIB + f"""
const LIVE = {json.dumps(LIVE)}, CH = '{CHANNEL}';
const REVIEWERS = {{ U0CG2KX8946: 'Joshua Socias', U0CFLEYGYTT: 'Lloyd Cortez', U0BJMJTMBA5: 'Genesis David', U08PL615DPS: 'Diego' }};
const OWNER = 'U08PL615DPS', ASSIGNEES = {json.dumps([a for a in ASSIGNEES if a])}, ESCALATE_H = 3;
const at = u => LIVE ? `<@${{u}}>` : (REVIEWERS[u] || 'a teammate');
const nameOf = u => REVIEWERS[u] || (LIVE ? `<@${{u}}>` : 'a teammate');
const assignedName = ASSIGNEES.map(u => REVIEWERS[u] || u).join(' and ');
const now = Date.now(), nowIso = new Date(now).toISOString();
const closed = s => ['Resolved', 'Dismissed'].includes(s);
const state = $getWorkflowStaticData('global');
const P = state.pending = {{ rows: {{}}, logs: [], handledBefore: nowIso, changed: false }};
const out = [];
const log = (event, r, who, details) => P.logs.push({{ time: nowIso, event, jobId: r.jobId || '', issueId: r.issueId || '', who: who || 'automation', details: String(details || '').slice(0, 500) }});
const say = (thread, text, row, ev) => {{ out.push({{ json: {{ slack: {{ channel: CH, text, ...(thread ? {{ thread_ts: thread }} : {{}}), unfurl_links: false }}, row }} }}); if (ev) log(...ev); }};
const keep = row => {{ P.rows[row.issueId] = row; P.changed = true; }};
const issues = {{}};
for (const r of $('All issues').all().flatMap(i => i.json.data || [])) issues[r.issueId] = r;

// 1) new audits (oldest first): open, update or close issue threads
const audits = $('New audits').all().flatMap(i => i.json.data || []).sort((a, b) => String(a.auditedAt).localeCompare(String(b.auditedAt)));
for (const a of audits) {{
  P.changed = true;
  log('audited', a, '', `${{a.invoice}} ${{a.customer}}: ${{a.outcome}}${{a.missing ? ` (missing: ${{a.missing}})` : ''}}`);
  let list = []; try {{ list = JSON.parse(a.issues || '[]'); }} catch {{}}
  const ids = new Set(list.map(i => i.id));
  const lines = (() => {{ try {{ return JSON.parse(a.notes || '{{}}').lines || []; }} catch {{ return []; }} }})();
  for (const i of list) {{
    const sig = `${{i.cat}}|${{i.impact}}|${{i.text}}`.slice(0, 400);
    const ex = issues[i.id];
    const row = {{ issueId: i.id, jobId: a.jobId, invoice: a.invoice, customer: a.customer, category: i.cat, kind: i.kind,
      text: i.text.slice(0, 1000), impact: i.impact ?? 0, direction: i.dir || '', confidence: i.confidence,
      evidence: a.gmailId ? `https://mail.google.com/mail/u/?authuser=contact%40expresshaulers.com#all/${{a.gmailId}}` : '', sig, lastUpdate: nowIso }};
    if (!ex || (closed(ex.status) && ex.sig !== sig)) {{
      const r = {{ ...row, status: 'Open', claimant: assignedName, claimedAt: assignedName ? nowIso : '', resolution: '', reminded: '', escalated: '', closedBy: '', firstSeen: nowIso }};
      say('', issueText(i, {{ ...a, lines }}, LIVE, ASSIGNEES.map(at).join(' and ')), r, ['issue opened', r, '', i.text]);
    }} else if (!closed(ex.status) && ex.sig !== sig) {{
      const r = {{ ...ex, ...row }};
      say(ex.slackTs, `:repeat: *Revised invoice ${{a.invoice}}* - updated finding: ${{i.text}}${{i.impact != null ? ` (impact $${{i.impact}})` : ''}}`, r, ['issue updated', r, '', i.text]);
      issues[i.id] = r;
    }}
  }}
  for (const ex of Object.values(issues)) {{
    if (ex.invoice !== a.invoice || closed(ex.status) || ids.has(ex.issueId) || !ex.slackTs) continue;
    const r = {{ ...ex, status: 'Resolved', resolution: 'No longer found in the revised invoice', resolvedAt: nowIso, closedBy: 'automation', lastUpdate: nowIso }};
    say(ex.slackTs, ':white_check_mark: Not found in the revised invoice any more - marked *Resolved*.', r, ['resolved by revision', r, '', a.invoice]);
    issues[ex.issueId] = r;
  }}
}}

// 2) statuses, notes and the 3-hour escalation - from the open threads' reactions and replies (no claiming)
const threads = $('Open threads').all().map(x => x.json);
// 2026-11-18: reactions.get needs a Slack scope the bot does not have (missing_scope), so no ✅ was ever seen;
// the thread's first message (conversations.replies) carries its reactions too
const reacts = $('Thread replies').all().map(x => (x.json?.messages || [])[0]?.reactions || []);
const replies = $('Thread replies').all().map(x => (x.json?.messages || []).slice(1).filter(m => !m.bot_id && m.user));
threads.forEach((t, k) => {{
  const ex = issues[t.issueId];
  if (!ex) return;
  const users = (...names) => reacts[k].filter(r => names.includes(r.name)).flatMap(r => r.users || []);
  const note = (replies[k] || []).slice(-1)[0];
  let r = {{ ...ex }};
  const done = users('white_check_mark', 'heavy_check_mark'), no = users('no_entry_sign', 'x');
  if (!closed(r.status) && (done.length || no.length)) {{
    const status = done.length ? 'Resolved' : 'Dismissed', who = nameOf(done[0] || no[0]);
    r = {{ ...r, status, resolution: note ? String(note.text).slice(0, 500) : '', resolvedAt: nowIso, closedBy: who, lastUpdate: nowIso }};
    say(ex.slackTs, `${{done.length ? ':white_check_mark:' : ':no_entry_sign:'}} *${{status}}* by ${{who}}${{note ? '' : ' - please reply in this thread with a short note on what was done (it goes in the audit record)'}}`,
      r, [status.toLowerCase(), r, who, note ? note.text : '']);
  }} else if (closed(r.status) && !r.resolution && note) {{
    r = {{ ...r, resolution: String(note.text).slice(0, 500), lastUpdate: nowIso }};
    keep(r); log('resolution note', r, nameOf(note.user), note.text);
  }}
  // client 2026-11-17: still open 3 hours after it was posted -> escalate to Diego, any amount
  if (!closed(r.status) && !r.escalated && now - Date.parse(r.firstSeen || nowIso) > ESCALATE_H * 3600e3) {{
    r = {{ ...r, status: 'Escalated', escalated: 'yes', lastUpdate: nowIso }};
    say(ex.slackTs, `:warning: *Escalated:* still open ${{ESCALATE_H}} hours after it was posted - ${{at(OWNER)}} please take a look`, r, ['escalated', r, '', `open ${{ESCALATE_H}} h`]);
  }}
}});

// 3) daily report at 7:45 PM (client 2026-11-17): the past 24 hours, since the last report, once a day
const ny = new Date(new Date(now).toLocaleString('en-US', {{ timeZone: 'America/New_York' }}));
const dayKey = `${{ny.getFullYear()}}-${{ny.getMonth() + 1}}-${{ny.getDate()}}`;
if (ny.getHours() * 60 + ny.getMinutes() >= 19 * 60 + 45 && state.daily !== dayKey) {{
  P.daily = {{ since: new Date(Math.max(now - 24 * 3600e3, Date.parse(state.dailyAt || 0))).toISOString() }};
  state.daily = dayKey; state.dailyAt = nowIso;
}}
if (!out.length) out.push({{ json: {{ noop: true }} }});
return out;
"""

COLLECT = f"""
// Save what was sent (thread ts), the quiet row changes, the log; mark the audits handled.
const state = $getWorkflowStaticData('global');
const P = state.pending || {{ rows: {{}}, logs: [] }};
const plan = $('Plan').all().map(x => x.json);
const sent = $('Anything to send?').isExecuted && $('Send to Slack').isExecuted ? $('Send to Slack').all().map(x => x.json) : [];
plan.filter(p => p.slack).forEach((p, i) => {{
  const res = sent[i] || {{}};
  if (!p.row) return;
  const row = {{ ...p.row }};
  if (!row.slackTs && res.ok && res.ts) row.slackTs = res.ts;
  if (!res.ok) P.logs.push({{ time: new Date().toISOString(), event: 'slack error', jobId: row.jobId, issueId: row.issueId, who: 'automation', details: res.error || 'no reply' }});
  if (row.slackTs || res.ok) P.rows[row.issueId] = row;
}});
const rows = Object.values(P.rows).map(r => {{ const {{ id, createdAt, updatedAt, ...rest }} = r; return rest; }});
return [{{ json: {{ rows, logs: P.logs, handledBefore: P.handledBefore, daily: P.daily || null, changed: !!P.changed || rows.length > 0,
  filters: rows.map(r => ({{ type: 'and', filters: [{{ columnName: 'issueId', condition: 'eq', value: r.issueId }}] }})) }} }}];
"""

SPLIT = """
const c = $('Collect').first().json;
return c.rows.map((row, i) => ({ json: { body: { filter: c.filters[i], data: row } } }));
"""

SHEETDATA = f"""
// The Google Sheet mirrors the three tables (one row per job, per issue, per event).
const audits = $('Sheet: audits').all().flatMap(i => i.json.data || []);
const issues = $('Sheet: issues').all().flatMap(i => i.json.data || []);
const logs = $('Sheet: log').all().flatMap(i => i.json.data || []);
const latest = {{}};
for (const a of audits.sort((x, y) => String(x.auditedAt).localeCompare(String(y.auditedAt)))) latest[a.jobId] = a;
const t = d => d ? new Date(d).toLocaleString('en-US', {{ timeZone: 'America/New_York' }}) : '';
const link = ts => ts ? `https://expresshaulersmoving.slack.com/archives/{CHANNEL}/p${{String(ts).replace('.', '')}}` : '';
const rec = Object.values(latest).sort((x, y) => String(y.auditedAt).localeCompare(String(x.auditedAt))).map(a => [
  a.jobId, a.invoice, a.customer, a.email, a.invoiceDate, a.jobDay, a.estimate, a.rcLines, a.lineTotal, a.grandTotal, a.balance, a.fee, a.deposit,
  '', '', '', '', a.outcome, a.confidence, a.issueCount, a.missing, t(a.auditedAt)]);
const dis = issues.sort((x, y) => String(y.firstSeen).localeCompare(String(x.firstSeen))).map(r => [
  r.issueId, r.jobId, r.invoice, r.customer, r.category, r.kind === 'error' ? 'Billing error' : 'Needs review', r.text, r.impact, r.direction,
  r.confidence, r.evidence, link(r.slackTs), r.status, r.claimant, t(r.claimedAt), r.resolution, t(r.resolvedAt), r.correctedAmount, r.recovered,
  t(r.firstSeen), t(r.lastUpdate)]);
const lg = logs.sort((x, y) => String(y.time).localeCompare(String(x.time))).slice(0, 5000).map(l => [t(l.time), l.event, l.jobId, l.issueId, l.who, l.details]);
return [{{ json: {{
  clear: {{ ranges: ["'Invoice Audit Records'!A2:Z", "'Audit Discrepancies'!A2:Z", "'Audit Activity Log'!A2:Z"] }},
  write: {{ valueInputOption: 'RAW', data: [
    {{ range: "'Invoice Audit Records'!A2", values: rec.length ? rec : [['']] }},
    {{ range: "'Audit Discrepancies'!A1", values: [['Issue ID', 'Job ID', 'Invoice', 'Customer', 'Category', 'Type', 'Finding', 'Impact ($)',
      'Direction', 'Confidence', 'Evidence', 'Slack Thread', 'Status', 'Assigned To', 'Assigned At', 'Resolution Note', 'Resolved At',
      'Corrected Invoice Amount', 'Money Recovered', 'First Seen', 'Last Update']] }},
    {{ range: "'Audit Discrepancies'!A2", values: dis.length ? dis : [['']] }},
    {{ range: "'Audit Activity Log'!A2", values: lg.length ? lg : [['']] }}] }},
  daily: (() => {{
    const d = $('Collect').first().json.daily;
    if (!d) return null;
    const since = Date.parse(d.since), inWin = x => Date.parse(x || 0) >= since;
    const jobs = Object.values(Object.fromEntries(audits.filter(a => inWin(a.auditedAt)).sort((x, y) => String(x.auditedAt).localeCompare(String(y.auditedAt))).map(a => [a.jobId, a])));
    const n = o => jobs.filter(a => a.outcome === o).length;
    const I = issues.filter(r => inWin(r.firstSeen)), C = issues.filter(r => inWin(r.resolvedAt));
    const open = issues.filter(r => !['Resolved', 'Dismissed'].includes(r.status));
    const money = (list, dir) => '$' + list.filter(r => r.direction === dir).reduce((s, r) => s + (+r.impact || 0), 0).toFixed(2);
    const line = r => `• ${{r.invoice}} ${{r.customer}} — ${{String(r.text).split(' · ')[0].slice(0, 140)}}${{+r.impact ? ` (*$${{(+r.impact).toFixed(2)}}* ${{r.direction === 'over' ? 'overbilled' : 'underbilled'}})` : ''}} — ${{r.status}}${{r.slackTs ? ` <${{link(r.slackTs)}}|thread>` : ''}}`;
    const inc = jobs.filter(a => a.outcome === 'Incomplete');
    return [`:bar_chart: *Invoice audit - daily report* (${{t(d.since)}} to now)`,
      `*Invoices audited:* ${{jobs.length}} · Passed ${{n('Passed')}} · Needs Review ${{n('Needs Review')}} · Incomplete ${{n('Incomplete')}}`,
      ...(inc.length ? [`*Incomplete (could not check everything):* ${{inc.map(a => `${{a.invoice}} ${{a.customer}} (${{a.missing}})`).join('; ')}}`] : []),
      `*New issues:* ${{I.length}}${{I.length ? ` · potential underbilling ${{money(I, 'under')}} · overbilling ${{money(I, 'over')}}` : ''}}`,
      ...I.slice(0, 15).map(line), ...(I.length > 15 ? [`…and ${{I.length - 15}} more in the sheet`] : []),
      `*Closed:* ${{C.filter(r => r.status === 'Resolved').length}} resolved · ${{C.filter(r => r.status === 'Dismissed').length}} dismissed`,
      `*Still open:* ${{open.length}}${{open.length ? ` (escalated ${{open.filter(r => r.status === 'Escalated').length}})` : ''}}`,
      ...open.filter(r => !inWin(r.firstSeen)).slice(0, 10).map(line),
      `<https://docs.google.com/spreadsheets/d/{SHEET}/edit|Invoice Audit sheet>`].join('\\n');
  }})() }} }}];
"""


def http(name, pos, method, url, cred, body=None, query=None, once=True, paginate=False, extra=None):
    kind = next(iter(cred))
    p = {'method': method, 'url': url, 'options': {}}
    p.update({'authentication': 'genericCredentialType', 'genericAuthType': kind} if kind == 'httpHeaderAuth'
             else {'authentication': 'predefinedCredentialType', 'nodeCredentialType': kind})
    if query:
        p.update({'sendQuery': True, 'queryParameters': {'parameters': [{'name': k, 'value': v} for k, v in query]}})
    if body is not None:
        p.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    if paginate:
        p['options']['pagination'] = {'pagination': {
            'parameters': {'parameters': [{'type': 'qs', 'name': 'cursor', 'value': '={{ $response.body.nextCursor }}'}]},
            'paginationCompleteWhen': 'other', 'completeExpression': '={{ !$response.body.nextCursor }}',
            'limitPagesFetched': True, 'maxRequests': 50}}
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos, 'parameters': p,
         'credentials': cred, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 5000,
         'alwaysOutputData': True, 'onError': 'continueRegularOutput'}
    if once:
        n['executeOnce'] = True
    n.update(extra or {})
    return n


def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': {'jsCode': js.strip()}}


def if_node(name, left, pos):
    return {'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': pos,
            'parameters': {'conditions': {'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                                          'combinator': 'and',
                                          'conditions': [{'id': name, 'leftValue': left, 'rightValue': '',
                                                          'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                           'options': {}}}


UNHANDLED = json.dumps({'type': 'and', 'filters': [{'columnName': 'handled', 'condition': 'eq', 'value': ''}]})
nodes = [
    {'name': 'Every 5 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 5}]}}},
    http('New audits', [200, 0], 'GET', f"{API}/{T['invoice_audits']}/rows", CRED_N8N,
         query=[('filter', UNHANDLED), ('limit', '100')]),
    http('All issues', [400, 0], 'GET', f"{API}/{T['audit_issues']}/rows", CRED_N8N, query=[('limit', '250')], paginate=True),
    code('Open threads', OPEN, [600, 0]),
    http('Thread replies', [1000, 0], 'GET', 'https://slack.com/api/conversations.replies', CRED_SLACK,
         query=[('channel', CHANNEL), ('ts', "={{ $('Open threads').item.json.ts }}"), ('limit', '50')], once=False),
    code('Plan', PLAN, [1200, 0]),
    if_node('Anything to send?', '={{ !!$json.slack }}', [1400, 0]),
    http('Send to Slack', [1600, -100], 'POST', 'https://slack.com/api/chat.postMessage', CRED_SLACK,
         body='={{ JSON.stringify($json.slack) }}', once=False, extra={'retryOnFail': False}),
    code('Collect', COLLECT, [1800, 0]),
    code('One row each', SPLIT, [2000, 0]),
    http('Save issues', [2200, 0], 'POST', f"{API}/{T['audit_issues']}/rows/upsert", CRED_N8N,
         body='={{ JSON.stringify($json.body) }}', once=False),
    http('Save the log', [2400, 0], 'POST', f"{API}/{T['audit_log']}/rows", CRED_N8N,
         body="={{ JSON.stringify({ data: $('Collect').first().json.logs.length ? $('Collect').first().json.logs : [{ time: new Date().toISOString(), event: 'heartbeat', jobId: '', issueId: '', who: 'automation', details: '' }], returnType: 'count' }) }}"),
    http('Mark audits handled', [2600, 0], 'PATCH', f"{API}/{T['invoice_audits']}/rows/update", CRED_N8N,
         body="={{ JSON.stringify({ filter: { type: 'and', filters: [{ columnName: 'handled', condition: 'eq', value: '' }, { columnName: 'auditedAt', condition: 'lte', value: $('Collect').first().json.handledBefore }] }, data: { handled: 'yes' } }) }}"),
    if_node('Sheet to update?', "={{ $('Collect').first().json.changed || $('Collect').first().json.daily || new Date().getMinutes() < 5 }}", [2800, 0]),
    http('Sheet: audits', [3000, -100], 'GET', f"{API}/{T['invoice_audits']}/rows", CRED_N8N, query=[('limit', '250')], paginate=True),
    http('Sheet: issues', [3200, -100], 'GET', f"{API}/{T['audit_issues']}/rows", CRED_N8N, query=[('limit', '250')], paginate=True),
    http('Sheet: log', [3400, -100], 'GET', f"{API}/{T['audit_log']}/rows", CRED_N8N,
         query=[('limit', '250'), ('filter', json.dumps({'type': 'and', 'filters': [{'columnName': 'event', 'condition': 'neq', 'value': 'heartbeat'}]}))], paginate=True),
    code('Sheet rows', SHEETDATA, [3600, -100]),
    http('Clear the sheet', [3800, -100], 'POST', f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values:batchClear', CRED_SHEETS,
         body='={{ JSON.stringify($json.clear) }}'),
    http('Write the sheet', [4000, -100], 'POST', f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values:batchUpdate', CRED_SHEETS,
         body="={{ JSON.stringify($('Sheet rows').first().json.write) }}"),
    if_node('Daily report?', "={{ !!$('Sheet rows').first().json.daily }}", [4200, -100]),
    http('Post the daily report', [4400, -200], 'POST', 'https://slack.com/api/chat.postMessage', CRED_SLACK,
         body=f"={{{{ JSON.stringify({{ channel: '{CHANNEL}', text: $('Sheet rows').first().json.daily, unfurl_links: false }}) }}}}"),
]
one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
order = ['Every 5 minutes', 'New audits', 'All issues', 'Open threads', 'Thread replies', 'Plan', 'Anything to send?']
connections = {a: {'main': [one(b)]} for a, b in zip(order, order[1:])}
connections['Anything to send?'] = {'main': [one('Send to Slack'), one('Collect')]}
connections['Send to Slack'] = {'main': [one('Collect')]}
for a, b in [('Collect', 'Save the log'), ('Save the log', 'Mark audits handled'), ('One row each', 'Save issues'),
             ('Sheet: audits', 'Sheet: issues'), ('Sheet: issues', 'Sheet: log'), ('Sheet: log', 'Sheet rows'),
             ('Sheet rows', 'Clear the sheet'), ('Clear the sheet', 'Write the sheet'), ('Write the sheet', 'Daily report?')]:
    connections[a] = {'main': [one(b)]}
# issues are saved first (depth-first), then the sheet mirror reads them
connections['Mark audits handled'] = {'main': [one('One row each') + one('Sheet to update?')]}
connections['Sheet to update?'] = {'main': [one('Sheet: audits'), []]}
connections['Daily report?'] = {'main': [one('Post the daily report'), []]}

wf = {'name': '#32 Invoice issues: threads, sheet, daily report' + ('' if LIVE else ' [PREVIEW - #automation-errors]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all'}}
(ROOT / 'workflows/invoice-issues.json').write_text(json.dumps(wf, indent=2))
print(ROOT / 'workflows/invoice-issues.json', 'LIVE' if LIVE else 'PREVIEW')
