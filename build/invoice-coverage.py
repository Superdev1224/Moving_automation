#!/usr/bin/env python3
"""Builds workflows/invoice-coverage.json — catches invoices that never came by email (item #10/#11).

Movegistics only emails contact@ when an invoice is *sent by email*. Invoices that are
collected on site, sent another way, or never sent produce no email, so the invoice check
never sees them. Stripe, however, records every final payment. Each morning this workflow:
  1. lists yesterday's Stripe payments (excluding the $52 deposits),
  2. reads every invoice email (and its PDF) from the last 4 days,
  3. flags every payment with no matching invoice email, and any gaps in the IN###### numbers.

  python3 build/invoice-coverage.py          test mode: posts to #automation-errors
  python3 build/invoice-coverage.py --live   posts to #invoices
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
DAYS = next((int(a.split('=')[1]) for a in sys.argv if a.startswith('--days=')), 1)   # for testing over a longer period
ERRORS = 'C0C2X8B9VS9'           # #automation-errors
INVOICES = 'C0C1ZV783V1'         # #invoices
CHANNEL = INVOICES if LIVE else ERRORS

LIB = re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/parse-invoice.js').read_text())
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_STRIPE = {'stripeApi': {'id': 'bn8XQuRj56UsqRMB', 'name': 'Stripe - Express Haulers (read-only)'}}

WINDOW = """
// Yesterday in New York time, plus a longer look-back for invoice emails
// (an invoice can be emailed a day or two before or after the card is charged).
const TZ = 'America/New_York';
const off = ms => {
  const p = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hourCycle: 'h23', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .formatToParts(new Date(ms)).reduce((o, x) => (o[x.type] = x.value, o), {});
  return (Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - ms) / 60000;
};
const [m, d, y] = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric', month: 'numeric', day: 'numeric' })
  .format(new Date()).split('/').map(Number);
const guess = Date.UTC(y, m - 1, d);
const todayStart = guess - off(guess - off(guess) * 60000) * 60000;
const start = todayStart - %DAYS% * 86400000;
return [{ json: {
  start, end: todayStart,
  startSec: Math.floor(start / 1000), endSec: Math.floor(todayStart / 1000),
  invoiceSince: start - 3 * 86400000,
  dayName: new Date(start).toLocaleDateString('en-US', { timeZone: TZ, weekday: 'long', month: 'long', day: 'numeric' }),
} }];
"""

INVOICES_SEEN = LIB + """
// Read every invoice PDF from the look-back window.
const out = [];
for (const item of $input.all()) {
  const text = item.json.text || '';
  if (!text) continue;
  const inv = parseInvoice(text);
  out.push({ json: { number: inv.number, customer: inv.customer, email: inv.email,
                     balance: inv.balance, grandTotal: inv.grandTotal } });
}
return out.length ? out : [{ json: { none: true } }];
"""

COMPARE = """
// Match each final payment to an invoice email; report what has no invoice and any gaps.
const w = $('Window').first().json;
const payments = $('Stripe payments').all().flatMap(i => i.json.data || [])
  .filter(c => c.paid && c.amount !== 5200 && c.amount !== 5000 && !c.refunded
            && c.created >= w.startSec && c.created < w.endSec);
const invoices = $input.all().map(i => i.json).filter(x => !x.none && x.number);

const norm = s => (s || '').toLowerCase().replace(/[^a-z ]/g, ' ').replace(/\\s+/g, ' ').trim();
const near = (a, b) => a != null && b != null && Math.abs(a - b) < 0.02;
const used = new Set();
const unmatched = [];
for (const p of payments) {
  const amt = p.amount / 100;
  const name = norm(p.billing_details?.name), email = (p.billing_details?.email || p.receipt_email || '').toLowerCase();
  const hit = invoices.find(v => !used.has(v.number) && (
      near(v.balance, amt) || near(v.grandTotal, amt)
      || (email && (v.email || '').toLowerCase() === email)
      || (name && norm(v.customer) && (norm(v.customer).includes(name.split(' ')[0]) && name.includes(norm(v.customer).split(' ')[0])))));
  if (hit) used.add(hit.number);
  else unmatched.push({ amount: amt, name: p.billing_details?.name || 'name not on the payment', email,
                        time: new Date(p.created * 1000).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) });
}

// gaps in the invoice numbering among what we have seen
const nums = [...new Set(invoices.map(v => parseInt(String(v.number).slice(2), 10)).filter(n => n > 0))].sort((a, b) => a - b);
const gaps = [];
for (let n = nums[0]; nums.length && n <= nums[nums.length - 1]; n++) if (!nums.includes(n)) gaps.push('IN' + String(n).padStart(6, '0'));

const fmt = n => '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let text = `:mag: *Invoice check for ${w.dayName}*\\n`;
text += `${payments.length} final payment(s) in Stripe, ${payments.length - unmatched.length} matched to an invoice email.\\n`;
if (unmatched.length) {
  text += `\\n:warning: *Paid, but no invoice email was received* — the invoice maths was not checked for these:\\n`;
  text += unmatched.map(u => `• ${fmt(u.amount)} — ${u.name}${u.email ? ` (${u.email})` : ''} at ${u.time}`).join('\\n');
  text += `\\n_Please check these invoices in Movegistics (they were probably collected on site or not sent by email)._`;
} else if (payments.length) {
  text += ':white_check_mark: Every payment has a matching invoice.';
}
if (gaps.length) text += `\\n\\n*Invoice numbers with no email* (last few days): ${gaps.slice(-15).join(', ')}`;
return [{ json: { channel: '%CHANNEL%', text, quiet: !payments.length && !gaps.length } }];
"""

def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}

def http(name, pos, url, cred, generic=None, predefined=None, qs=None, once=True):
    p = {'method': 'GET', 'url': url, 'options': {}}
    if predefined:
        p['authentication'] = 'predefinedCredentialType'; p['nodeCredentialType'] = predefined
    if generic:
        p['authentication'] = 'genericCredentialType'; p['genericAuthType'] = generic
    if qs:
        p['sendQuery'] = True; p['queryParameters'] = {'parameters': [{'name': k, 'value': v} for k, v in qs]}
    if once:
        p['executeOnce'] = True
    return {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
            'parameters': p, 'credentials': cred, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'}

nodes = [
    {'name': 'Every day 9:00 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 0}]}}},
    code('Window', WINDOW.replace('%DAYS%', str(DAYS)), [220, 0]),
    http('Stripe payments', [440, -120], 'https://api.stripe.com/v1/charges', CRED_STRIPE, predefined='stripeApi',
         qs=[('created[gte]', '={{ $json.startSec }}'), ('created[lt]', '={{ $json.endSec }}'), ('limit', '100')]),
    {'name': 'Invoice emails', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [660, -120],
     'parameters': {'resource': 'message', 'operation': 'getAll', 'returnAll': True, 'simple': False,
                    'filters': {'q': "=from:noreply@movegistics.com subject:\"Invoice:\" after:{{ Math.floor($('Window').first().json.invoiceSince / 1000) }}"},
                    'options': {'downloadAttachments': True}},
     'credentials': CRED_GMAIL, 'executeOnce': True, 'alwaysOutputData': True},
    {'name': 'Read PDF', 'type': 'n8n-nodes-base.extractFromFile', 'typeVersion': 1, 'position': [880, -120],
     'parameters': {'operation': 'pdf', 'binaryPropertyName': 'attachment_0', 'options': {}},
     'onError': 'continueRegularOutput', 'alwaysOutputData': True},
    code('Invoices seen', INVOICES_SEEN, [1100, -120]),
    code('Compare', COMPARE.replace('%CHANNEL%', CHANNEL), [1760, -120]),
    {'name': 'Anything to report?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1980, -120],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'q', 'leftValue': '={{ $json.quiet }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'false', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2200, -160],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}', 'otherOptions': {'includeLinkToWorkflow': False}},
     'credentials': CRED_SLACK},
]

one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
connections = {
    'Every day 9:00 AM': {'main': [one('Window')]},
    'Window': {'main': [one('Stripe payments')]},
    'Stripe payments': {'main': [one('Invoice emails')]},
    'Invoice emails': {'main': [one('Read PDF')]},
    'Read PDF': {'main': [one('Invoices seen')]},
    'Invoices seen': {'main': [one('Compare')]},
    'Compare': {'main': [one('Anything to report?')]},
    'Anything to report?': {'main': [one('Post to Slack'), []]},
}

wf = {'name': '#10b Invoice coverage: Stripe payments vs invoice emails' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/invoice-coverage.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
