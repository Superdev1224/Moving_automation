#!/usr/bin/env python3
"""Builds workflows/invoice-audit.json — #5b invoice audit (called by #5 for every invoice).

For one invoice: the customer's signed estimate (rate, crew size, minimum hours), the job on the
Connecteam schedule, and the crew lead's texts from that day in RingCentral. Claude reads the texts
into a job report (start, finish, breaks, extra crew, materials) and the audit compares it all with
the invoice. Returns {problems, notes, lines}; #5 puts them in the #invoices post.

  python3 build/invoice-audit.py
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
MG = 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1'
SCHEDULER_ID = 16427123
EXTENSIONS = ['607020048', '1390410048', '1576621048', '1622565048']

CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}
CRED_CONNECTEAM = {'httpHeaderAuth': {'id': 'rwvSlLRKFeQl7TOv', 'name': 'Connecteam - Express Haulers'}}
CRED_RC_BASIC = {'httpBasicAuth': {'id': '87DDrO5uOfI7ikqp', 'name': 'RingCentral app (client id/secret)'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
INVOICES = 'C0C1ZV783V1'   # #invoices: crews and the office post start/finish/materials there too (since Oct)
RC_JWT = (ROOT / '.secrets').read_text().split('RC_JWT=')[1].split('\n')[0]

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/crew-report.js').read_text()) + strip((ROOT / 'lib/invoice-audit.js').read_text())

PLAN = LIB + """
// Which signed estimate, and which days of the schedule to read.
const j = $input.first().json;
const invoiceMs = Date.parse(j.invoiceDate || '') || Date.now();
const est = pickEstimate(j.estimates || [], j.email, invoiceMs);
const s = Math.floor(invoiceMs / 1000);
return [{ json: { inv: j.inv, email: j.email, customer: j.customer, invoiceMs,
  estimateId: est ? est._id : 'none', winFrom: s - 4 * 86400, winTo: s + 2 * 86400 } }];
"""

FIND = LIB + """
// The job this invoice is for, its crew, and their phone numbers.
const plan = $('Plan').first().json;
const shifts = $('Schedule').first().json.data?.shifts || [];
const users = $input.first().json.data?.users || [];
const job = findJob(shifts, plan.customer, plan.email, plan.invoiceMs + 12 * 3600e3);
const ten = p => String(p || '').replace(/\\D/g, '').slice(-10);
const phones = {};
for (const u of users) if (ten(u.phoneNumber).length === 10 && (!job || !job.crew.length || job.crew.includes(u.userId)))
  phones[ten(u.phoneNumber)] = `${u.firstName || ''} ${u.lastName || ''}`.trim();
return [{ json: { found: !!job, job, phones,
  dateFrom: job ? new Date(job.start - 5 * 3600e3).toISOString() : '',
  dateTo: job ? new Date(job.start + 22 * 3600e3).toISOString() : '' } }];
"""

LINES = """
const token = $input.first().json.access_token;
const f = $('Find the job').first().json;
return %EXT%.map(ext => ({ json: { ext, token, dateFrom: f.dateFrom, dateTo: f.dateTo } }));
""".replace('%EXT%', json.dumps(EXTENSIONS))

ASK = LIB + """
// The crew's texts that day, for Claude to read as job reports.
const f = $('Find the job').first().json;
const ten = p => String(p || '').replace(/\\D/g, '').slice(-10);
const seen = new Set(), texts = [];
for (const m of $('Crew texts').all().flatMap(i => i.json.records || [])) {
  const from = ten(m.from?.phoneNumber);
  if (!f.phones[from] || seen.has(m.id)) continue;
  seen.add(m.id);
  texts.push({ at: m.creationTime, time: new Date(m.creationTime).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }),
               from: f.phones[from], text: String(m.subject || '').slice(0, 1500) });
}
// what people posted in #invoices that day (the office relays crew calls there: "9:30 AM to 4:30 PM / 12 tapes")
// 2026-11-18: only posts that name this crew's customers or crew members. #invoices carries every crew's
// reports, and the office's "Jennifer Digirolamo / 7:45 - 11:45 AM" was read as Alina Bowe's (other crew, same day).
const nameWords = [...(f.job.dayJobs || []).map(j => String(j.title || '').split('|')[0]), ...Object.values(f.phones || {})]
  .flatMap(n => String(n).toLowerCase().split(/[^a-z]+/)).filter(w => w.length >= 3);
const mentionsUs = t => { const ws = new Set(String(t).toLowerCase().split(/[^a-z]+/)); return nameWords.some(w => ws.has(w)); };
for (const m of $('Slack posts').isExecuted ? ($('Slack posts').first().json.messages || []) : []) {
  if (m.bot_id || m.subtype || !m.text || !mentionsUs(m.text)) continue;
  const at = new Date(+m.ts * 1000).toISOString();
  texts.push({ at, time: new Date(at).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }),
               from: 'Slack: ' + (m.user_profile?.real_name || m.user || 'office'), text: String(m.text).replace(/<@\\w+>/g, '@someone').slice(0, 1500) });
}
texts.sort((a, b) => a.at.localeCompare(b.at));
const has = texts.length > 0;
return [{ json: { has, count: texts.length, body: has ? crewBody(f.job.day, f.job.dayJobs, texts.slice(-150)) : null } }];
"""

AUDIT = LIB + """
// Put it together: estimate, crew report, invoice.
const plan = $('Plan').first().json;
const estRaw = $('Signed estimate').first().json;
const est = estRaw && (estRaw.data || estRaw).movePricing ? { ...(estRaw.data || estRaw), displayId: (estRaw.data || estRaw).displayId } : null;
const f = $('Find the job').isExecuted ? $('Find the job').first().json : { found: false };
let report = null;
if ($('Read the crew texts').isExecuted && f.found) {
  const reports = parseCrew($('Read the crew texts').first().json);
  const name = String(plan.customer || '').toLowerCase().split(/\\s+/)[0];
  report = reports.find(r => r.job === f.job.title) || reports.find(r => name && r.job.toLowerCase().includes(name)) || null;
}
const r = auditInvoice({ inv: plan.inv, est, report, job: f.found ? f.job : null });
return [{ json: { ...r, job: f.found ? f.job.title : null, crewTexts: $('Any crew texts?').isExecuted ? $('Crew texts for Claude').first().json.count : 0 } }];
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


def get(name, url, pos, cred, headers=None, once=True):
    n = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
         'parameters': {'method': 'GET', 'url': url, 'authentication': 'genericCredentialType',
                        'genericAuthType': 'httpHeaderAuth', 'options': {}},
         'credentials': cred, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'}
    if headers:
        n['parameters'].update({'sendHeaders': True, 'headerParameters': {'parameters': [{'name': k, 'value': v} for k, v in headers]}})
    if once:
        n['executeOnce'] = True
    return n


nodes = [
    {'name': 'Invoice handed over', 'type': 'n8n-nodes-base.executeWorkflowTrigger', 'typeVersion': 1.1,
     'position': [0, 0], 'parameters': {'inputSource': 'passthrough'}},
    code('Plan', PLAN, [200, 0]),
    get('Signed estimate', f"={MG}/estimate/{{{{ $json.estimateId }}}}", [400, 0], CRED_MG, [('X-LOCATION-ID', '100821')]),
    get('Schedule', f"=https://api.connecteam.com/scheduler/v1/schedulers/{SCHEDULER_ID}/shifts"
                    "?startTime={{ $('Plan').first().json.winFrom }}&endTime={{ $('Plan').first().json.winTo }}&limit=500",
        [600, 0], CRED_CONNECTEAM),
    get('Crew', 'https://api.connecteam.com/users/v1/users?limit=500', [800, 0], CRED_CONNECTEAM),
    code('Find the job', FIND, [1000, 0]),
    if_node('Job on the schedule?', '={{ $json.found }}', [1200, 0]),
    {'name': 'RingCentral token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1400, -100],
     'parameters': {'method': 'POST', 'url': 'https://platform.ringcentral.com/restapi/oauth/token',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpBasicAuth',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'grant_type', 'value': 'urn:ietf:params:oauth:grant-type:jwt-bearer'},
                        {'name': 'assertion', 'value': RC_JWT}]},
                    'options': {}},
     'credentials': CRED_RC_BASIC},
    code('Each company line', LINES, [1600, -100]),
    {'name': 'Crew texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1800, -100],
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
    {'name': 'Slack posts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1900, -100],
     'parameters': {'method': 'GET', 'url': 'https://slack.com/api/conversations.history',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'slackApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'channel', 'value': INVOICES},
                        {'name': 'oldest', 'value': "={{ Date.parse($('Find the job').first().json.dateFrom) / 1000 }}"},
                        {'name': 'latest', 'value': "={{ Date.parse($('Find the job').first().json.dateTo) / 1000 }}"},
                        {'name': 'limit', 'value': '200'}]},
                    'options': {}},
     'credentials': CRED_SLACK, 'executeOnce': True, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Crew texts for Claude', ASK, [2000, -100]),
    if_node('Any crew texts?', '={{ $json.has }}', [2200, -100]),
    {'name': 'Read the crew texts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2400, -200],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.body) }}',
                    'options': {}},
     'credentials': CRED_ANTHROPIC, 'onError': 'continueRegularOutput'},
    code('Audit', AUDIT, [2600, 0]),
]

one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
connections = {
    'Invoice handed over': {'main': [one('Plan')]},
    'Plan': {'main': [one('Signed estimate')]},
    'Signed estimate': {'main': [one('Schedule')]},
    'Schedule': {'main': [one('Crew')]},
    'Crew': {'main': [one('Find the job')]},
    'Find the job': {'main': [one('Job on the schedule?')]},
    'Job on the schedule?': {'main': [one('RingCentral token'), one('Audit')]},
    'RingCentral token': {'main': [one('Each company line')]},
    'Each company line': {'main': [one('Crew texts')]},
    'Crew texts': {'main': [one('Slack posts')]},
    'Slack posts': {'main': [one('Crew texts for Claude')]},
    'Crew texts for Claude': {'main': [one('Any crew texts?')]},
    'Any crew texts?': {'main': [one('Read the crew texts'), one('Audit')]},
    'Read the crew texts': {'main': [one('Audit')]},
}
wf = {'name': '#5b Invoice audit (estimate + crew report)', 'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}
(ROOT / 'workflows/invoice-audit.json').write_text(json.dumps(wf, indent=2))
print(ROOT / 'workflows/invoice-audit.json')
