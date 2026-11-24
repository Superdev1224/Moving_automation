#!/usr/bin/env python3
"""Builds workflows/invoices.json (item #5, with the math check from #12).

Movegistics "Invoice: INxxxxx" email -> read PDF -> Slack #invoices: summary + math check + PDF.
  python3 build/invoices.py            test mode (posts to #automation-errors)
  python3 build/invoices.py --live     posts to #invoices
  python3 build/invoices.py --probe    webhook-triggered copy that replays one past invoice (for testing)
"""
import json, pathlib, re, secrets, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/parse-invoice.js').read_text())
LIB += re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/invoice-issues.js').read_text())
TABLES = json.loads((ROOT / 'workflows/audit-tables.json').read_text())
CRED_N8N = {'httpHeaderAuth': {'id': 'Bc7JADy3VUTvng5V', 'name': 'n8n API (self)'}}

LIVE, PROBE = '--live' in sys.argv, '--probe' in sys.argv
ERRORS = 'C0C2X8B9VS9'                        # #automation-errors
CHANNEL = 'C0C1ZV783V1' if LIVE else ERRORS   # #invoices

CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
QUERY = 'from:noreply@movegistics.com subject:"Invoice:"'

BUILD = LIB + f"""
return $input.all().map(item => {{
  const inv = parseInvoice(item.json.text || '');
  const chk = checkInvoice(inv);
  return {{
    json: {{ channel: '{CHANNEL}', text: invoiceSlackText(inv, chk), ok: chk.ok, number: inv.number, inv, chk,
             customer: inv.customer, email: inv.email, balance: inv.balance, grandTotal: inv.grandTotal,
             invoiceDate: inv.date,
             fileName: (inv.number || 'invoice') + '.pdf' }},
    binary: item.binary,
  }};
}});
"""

DEPOSIT = LIB + """
// Add the deposit check: the customer's estimates in Movegistics say whether a deposit was paid.
const invoices = $('Check math').all();
return $input.all().map((res, i) => {
  const src = invoices[i];
  const j = src.json;
  const estimates = res.json?.data?.list || [];
  const chk = mergeChecks(j.chk, checkDeposit(j.inv, estimates));
  return { json: { ...j, chk, ok: chk.ok, estimates, text: invoiceSlackText(j.inv, chk) }, binary: src.binary };
});
"""

AUDIT_WF = (ROOT / 'workflows/invoice-audit.id').read_text().strip()
WRITE = LIB + """
// The post: math and deposit (here) + the estimate and crew-report audit (#5b). If the audit failed,
// the invoice is posted as before.
const src = $('Check the deposit').all();
return $input.all().map((res, i) => {
  const j = src[i].json;
  const a = res.json && Array.isArray(res.json.problems) ? res.json : null;
  const chk = a ? { ...j.chk, ok: j.chk.ok && !a.problems.length } : j.chk;
  const { estimates, ...rest } = j;
  // the audit record (Invoice Auditor spec): outcome, issues; the issue workflow (#32) posts only the problems
  const o = auditOutcome(j.inv, j.chk, a);
  const inv = j.inv, gmail = $('New invoice email').all()[i]?.json || {};
  const est = (JSON.stringify((a && a.lines) || []).match(/E[SW]\\d{4,}/) || [])[0] || '';
  const audit = { jobId: inv.number || gmail.id, invoice: inv.number || '', gmailId: gmail.id || '', customer: inv.customer || '',
    email: inv.email || '', invoiceDate: inv.date || '', jobDay: (a && a.job) ? '' : '', estimate: est, rcLines: '',
    outcome: o.outcome, confidence: o.confidence, issues: JSON.stringify(o.issues), missing: o.missing.join('; '),
    notes: JSON.stringify({ notes: o.notes, lines: o.lines }).slice(0, 4000), handled: '',
    issueCount: o.issues.length, lineTotal: inv.lineTotal || 0, grandTotal: inv.grandTotal || 0, balance: inv.balance ?? inv.grandTotal ?? 0,
    fee: inv.fee || 0, deposit: (inv.deposits || []).reduce((x, y) => x + y, 0), auditedAt: new Date().toISOString() };
  return { json: { ...rest, ok: chk.ok, auditFailed: !a, audit, text: invoiceSlackText(j.inv, j.chk, a) }, binary: src[i].binary };
});
"""

THREAD = f"""
// A revised invoice (same number sent again) goes in the thread of the first post, not as a new post.
const recent = $('Recent posts').first().json.messages || [];
const firstPost = number => recent.filter(m => !m.thread_ts || m.thread_ts === m.ts)
  .filter(m => String(m.text || '').includes(`*Invoice ${{number}}*`)).sort((a, b) => +a.ts - +b.ts)[0];
return $('Write the post').all().map(item => {{
  const j = item.json, first = j.number && firstPost(j.number);
  if (!first) return item;
  return {{ json: {{ ...j, threadTs: first.ts, text: ':repeat: *Revised version*\\n' + j.text }}, binary: item.binary }};
}});
"""

NO_PDF = f"""
return $input.all().map(item => ({{ json: {{ channel: '{ERRORS}',
  text: `:warning: Invoice email arrived but its PDF could not be read: *${{item.json.subject || 'unknown subject'}}*. Please check it in Gmail.` }} }}));
"""

def slack_msg(name, pos):
    return {'name': name, 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': pos,
            'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                           'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                           'text': '={{ $json.text }}', 'otherOptions': {'includeLinkToWorkflow': False}},
            'credentials': CRED_SLACK}

PROBE_PATH = 'invoice-probe-' + secrets.token_hex(12)
if PROBE:
    trigger = [
        {'name': 'Probe', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [-240, 0], 'webhookId': PROBE_PATH,
         'parameters': {'path': PROBE_PATH, 'httpMethod': 'GET',
                        'responseMode': 'lastNode', 'responseData': 'allEntries', 'options': {}}},
        {'name': 'New invoice email', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [0, 0],
         'parameters': {'resource': 'message', 'operation': 'getAll', 'returnAll': False, 'limit': 1, 'simple': False,
                        'filters': {'q': '={{ $json.query.q }}'}, 'options': {'downloadAttachments': True}},
         'credentials': CRED_GMAIL},
    ]
else:
    trigger = [
        {'name': 'New invoice email', 'type': 'n8n-nodes-base.gmailTrigger', 'typeVersion': 1.2, 'position': [0, 0],
         'parameters': {'pollTimes': {'item': [{'mode': 'everyMinute'}]}, 'simple': False,
                        'filters': {'q': QUERY}, 'options': {'downloadAttachments': True}},
         'credentials': CRED_GMAIL},
    ]

nodes = trigger + [
    {'name': 'Read PDF', 'type': 'n8n-nodes-base.extractFromFile', 'typeVersion': 1, 'position': [240, 0],
     'parameters': {'operation': 'pdf', 'binaryPropertyName': 'attachment_0', 'options': {'keepSource': 'binary'}},
     'onError': 'continueErrorOutput'},
    {'name': 'Check math', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [480, -80],
     'parameters': {'jsCode': BUILD.strip()}},
    # the customer's estimates, to check the deposit (searched by name: Movegistics' search ignores emails)
    {'name': 'Their estimates', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [560, -80],
     'parameters': {'method': 'POST', 'url': 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1/estimate/list/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': '100821'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': "={{ JSON.stringify({ filters: [], page: { limit: 20, pageId: 0 }, query: $json.customer || $json.email || 'nobody', sortBy: { direction: 'DESC', property: 'updatedAt' }, timeZone: 'America/New_York' }) }}",
                    'options': {}},
     'credentials': CRED_MG, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Check the deposit', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [640, -80],
     'parameters': {'jsCode': DEPOSIT.strip()}},
    {'name': 'Audit the job', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2, 'position': [680, -80],
     'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': AUDIT_WF, 'mode': 'id'},
                    'mode': 'each', 'options': {'waitForSubWorkflow': True}},
     'onError': 'continueRegularOutput', 'alwaysOutputData': True},
    {'name': 'Write the post', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [700, -80],
     'parameters': {'jsCode': WRITE.strip()}},
    {'name': 'Recent posts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [705, -80],
     'parameters': {'method': 'GET', 'url': 'https://slack.com/api/conversations.history',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'slackApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'channel', 'value': CHANNEL},
                        {'name': 'oldest', 'value': '={{ Math.floor(Date.now() / 1000) - 21 * 86400 }}'},
                        {'name': 'limit', 'value': '500'}]},
                    'options': {}},
     'credentials': CRED_SLACK, 'executeOnce': True, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Revision?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [710, -80],
     'parameters': {'jsCode': THREAD.strip()}},
    {'name': 'Post with PDF', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [720, -80],
     'parameters': {'authentication': 'accessToken', 'resource': 'file', 'operation': 'upload',
                    'binaryData': True, 'binaryPropertyName': 'attachment_0',
                    # channelId, not channelIds: Slack's new upload drops a list of channels silently, so every PDF
                    # before 2026-09-30 was uploaded privately and never showed up in #invoices
                    'options': {'channelId': '={{ $json.channel }}', 'initialComment': '={{ $json.text }}',
                                'threadTs': '={{ $json.threadTs || "" }}',
                                'fileName': '={{ $json.fileName }}', 'title': '={{ $json.fileName }}'}},
     'credentials': CRED_SLACK},
    {'name': 'Save the audit', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [720, -280],
     'parameters': {'method': 'POST', 'url': f"https://n8n.expresshaulers.com/api/v1/data-tables/{TABLES['invoice_audits']}/rows",
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': "={{ JSON.stringify({ data: [$json.audit], returnType: 'count' }) }}",
                    'options': {}},
     'credentials': CRED_N8N, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000, 'onError': 'continueRegularOutput'},
    {'name': 'Audit failed?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [900, -280],
     'parameters': {'conditions': {'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                                   'combinator': 'and',
                                   'conditions': [{'id': 'f', 'leftValue': "={{ $('Write the post').item.json.auditFailed }}", 'rightValue': '',
                                                   'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                    'options': {}}},
    {'name': 'Say the audit failed', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1100, -280],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                    'text': "=:warning: Invoice audit INCOMPLETE for {{ $('Write the post').item.json.audit.invoice }} ({{ $('Write the post').item.json.audit.customer }}): the estimate/crew check (#5b) did not run - a connection may be down. Recorded as Incomplete, not Passed.",
                    'otherOptions': {'includeLinkToWorkflow': True}},
     'credentials': CRED_SLACK},
    {'name': 'PDF missing', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [480, 120],
     'parameters': {'jsCode': NO_PDF.strip()}},
    slack_msg('Alert #automation-errors', [720, 120]),
]

nodes = [n for n in nodes if n['name'] not in ('Recent posts', 'Revision?', 'Post with PDF')]   # posting moved to #32 (2026-11-17)
link = lambda *outs: {'main': [[{'node': n, 'type': 'main', 'index': 0}] for n in outs]}
connections = {
    'New invoice email': link('Read PDF'),
    'Read PDF': link('Check math', 'PDF missing'),
    'Check math': link('Their estimates'),
    'Their estimates': link('Check the deposit'),
    'Check the deposit': link('Audit the job'),
    'Audit the job': link('Write the post'),
    'Write the post': link('Save the audit'),
    'Save the audit': link('Audit failed?'),
    'Audit failed?': {'main': [[{'node': 'Say the audit failed', 'type': 'main', 'index': 0}], []]},
    'PDF missing': link('Alert #automation-errors'),
}
if PROBE:
    connections['Probe'] = link('New invoice email')

name = '#5 Invoices → Slack #invoices (PDF + math check)'
name += ' [PROBE - delete me]' if PROBE else ('' if LIVE else ' [TEST MODE]')
wf = {'name': name, 'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York'}}

out = ROOT / ('workflows/invoices-probe.json' if PROBE else 'workflows/invoices.json')
out.write_text(json.dumps(wf, indent=2))
print(out, 'PROBE' if PROBE else ('LIVE' if LIVE else 'TEST MODE'))
