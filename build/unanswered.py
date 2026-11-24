#!/usr/bin/env python3
"""Builds workflows/unanswered.json — emails waiting for a reply (Additional Integrations #13).

Every 2 hours between 8 AM and 8 PM New York: Gmail threads whose last message is from a customer or
influencer with no answer from us. Claude drops the ones that need no reply ("thank you", receipts,
newsletters) and the website/Yelp/Thumbtack lead notifications. The rest go to #sales as one post.

  python3 build/unanswered.py          test mode  → #automation-errors
  python3 build/unanswered.py --live   live       → #sales
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C09D8S04H62' if LIVE else 'C0C2X8B9VS9'         # #daily-digest-and-updates / #automation-errors

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}

# Senders that never need a reply from the team: lead notifications and system mail.
NOT_A_CUSTOMER = ('-from:noreply@movegistics.com -from:mailer@shopify.com -from:yelp.com '
                  '-from:messaging.yelp.com -from:mail.yelp.com -from:thumbtack.com -from:pro.thumbtack.com '
                  '-from:ringcentral.com -from:stripe.com -from:google.com -from:clickup.com '
                  '-from:connecteam.com -from:slack.com -from:intuit.com -from:quickbooks.com')
QUERY = f'in:inbox -in:chats -from:me newer_than:7d {NOT_A_CUSTOMER}'

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/unanswered.js').read_text())

THREAD_IDS = LIB + """
// One item per thread that arrived in the last week (the message list gives ids only).
const msgs = $input.first().json.messages || [];
const ids = [...new Set(msgs.map(m => m.threadId))];
return ids.map(threadId => ({ json: { threadId } }));
"""

WAITING = LIB + """
// Which threads are still waiting for us, and have not been reported already?
const state = $getWorkflowStaticData('global');
const seen = pruneAlerted(state.alerted || {}, Date.now());
const rows = [];
for (const item of $input.all()) {
  const w = waitingThread(item.json, Date.now());
  if (!w || seen[w.messageId]) continue;        // one alert per customer message
  rows.push(w);
}
state.alerted = seen;
if (!rows.length) return [];
rows.sort((a, b) => b.waitedHours - a.waitedHours);
const batch = rows.slice(0, PER_RUN);        // the rest are picked up on the next run
return [{ json: { candidates: batch, waiting: rows.length, claudeBody: unansweredBody(batch) } }];
"""

DECIDE = LIB + f"""
// Claude answered: keep the ones that really need a reply, remember them, write the Slack post.
const state = $getWorkflowStaticData('global');
state.alerted = state.alerted || {{}};
const candidates = $('Threads waiting for us').first().json.candidates;
const verdicts = parseUnanswered($input.first().json);
const byId = Object.fromEntries(verdicts.map(v => [String(v.id), v]));
const rows = [];
for (const c of candidates) {{
  const v = byId[c.threadId];
  if (!v) continue;                             // Claude did not judge it: leave it for the next run
  state.alerted[c.messageId] = Date.now();      // judged once, whatever the verdict
  if (!v.needsReply) continue;
  rows.push({{ ...c, who: v.who || c.who, why: v.why || '' }});
}}
if (!rows.length) return [];
return [{{ json: {{ channel: '{CHANNEL}', text: unansweredSlack(rows), count: rows.length }} }}];
"""


def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}


nodes = [
    {'name': 'Every 2 hours (8am-8pm)', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '0 8-20/2 * * *'}]}}},
    {'name': 'Recent inbox mail', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'GET', 'url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'q', 'value': QUERY}, {'name': 'maxResults', 'value': '200'}]},
                    'options': {}},
     'credentials': CRED_GMAIL},
    code('One item per thread', THREAD_IDS, [440, 0]),
    # metadataHeaders has to repeat, and n8n keeps only the last of same-named query parameters,
    # so the whole query string lives in the URL.
    {'name': 'Read the thread', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [660, 0],
     'parameters': {'method': 'GET',
                    'url': '=https://gmail.googleapis.com/gmail/v1/users/me/threads/{{ $json.threadId }}'
                           '?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'options': {'batching': {'batch': {'batchSize': 20, 'batchInterval': 1200}}}},
     'credentials': CRED_GMAIL, 'onError': 'continueRegularOutput'},
    code('Threads waiting for us', WAITING, [880, 0]),
    {'name': 'Ask Claude', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, 0],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.claudeBody) }}',
                    'options': {}},
     'credentials': CRED_ANTHROPIC},
    code('Who needs an answer', DECIDE, [1320, 0]),
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1540, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

order = [n['name'] for n in nodes]
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}

wf = {'name': '#20 Emails waiting for a reply' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/unanswered.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
