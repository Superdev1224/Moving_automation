#!/usr/bin/env python3
"""Builds workflows/storage-reminders.json — #28 Storage payment reminders (2026-11-16).

Client PDF "AI Automations", item 3. Every day at 9 AM: read the "Customers With Storage" tracker
(an Excel file in contact@'s Google Drive) and post ONE combined message:
  - payment coming up: 7, 3 and 1 day before "Paid Up To" (and on the day)
  - overdue: Mondays only (once a week)
  - storage ending: customers paid up to their end date, on the same days, so the delivery gets booked
Nothing to say = no post. Same rules the client's other AI tool used in #invoices until 10/21.

  python3 build/storage-reminders.py               preview: posts to #automation-errors
  python3 build/storage-reminders.py --live        live: posts to #invoices with @channel
  add --hook to swap the 9 AM trigger for a webhook (one-off test runs only, never deployed)
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
HOOK = '--hook' in sys.argv
ERRORS = 'C0C2X8B9VS9'                                  # #automation-errors
CHANNEL = 'C0C1ZV783V1' if LIVE else ERRORS             # #invoices
FILE_ID = '1gwG1fFk8Jr2KNI8ur_bo67pUs49u82W7'
CRED_GOOGLE = {'googleOAuth2Api': {'id': (ROOT / 'workflows/google-cal-drive.credential').read_text().strip(),
                                   'name': 'Google - Drive read + Calendar (contact@)'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/storage-reminders.js').read_text())

WRITE = LIB + f"""
// One combined message, or nothing.
const today = {"($('Every day 9 AM').first().json.query?.today) || " if HOOK else ""}etToday(new Date());
const rows = $input.all().map(i => i.json).filter(r => !r.error);
if (!rows.length) return [{{ json: {{ channel: '{ERRORS}', text: ':warning: Storage reminders: could not read the Customers With Storage tracker today (empty or unreadable). Nothing was posted.' }} }}];
const customers = storageCustomers(rows, today);
const text = storageMessage(storageReminders(customers, today), today, {json.dumps(LIVE)});
return text ? [{{ json: {{ channel: '{CHANNEL}', text }} }}] : [];
"""

if HOOK:
    trigger = {'name': 'Every day 9 AM', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [0, 0],
               'webhookId': 'storage-test', 'parameters': {'path': 'storage-test', 'httpMethod': 'GET', 'options': {}}}
else:
    trigger = {'name': 'Every day 9 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
               'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '0 9 * * *'}]}}}

nodes = [
    trigger,
    {'name': 'Download the tracker', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'GET', 'url': f'https://www.googleapis.com/drive/v3/files/{FILE_ID}?alt=media',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'googleOAuth2Api',
                    'options': {'response': {'response': {'responseFormat': 'file'}}}},
     'credentials': CRED_GOOGLE, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 5000,
     'onError': 'continueErrorOutput'},
    {'name': 'Read the rows', 'type': 'n8n-nodes-base.extractFromFile', 'typeVersion': 1, 'position': [440, -80],
     'parameters': {'operation': 'xlsx',
                    'options': {'sheetName': 'Storage Tracker', 'range': 'A10:N300', 'headerRow': True, 'rawData': True}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Write the reminder', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [660, -80],
     'parameters': {'jsCode': WRITE.strip()}},
    {'name': 'Post it', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [880, -80],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'Tell us it failed', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [440, 120],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                    'text': '=:warning: Storage reminders: could not download the Customers With Storage tracker from Google Drive'
                            ' ({{ $json.error?.message || "unknown error" }}). If this repeats, the Google sign-in on the credential'
                            ' "Google - Drive read + Calendar (contact@)" may need redoing.',
                    'otherOptions': {'includeLinkToWorkflow': True, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
]
connections = {
    'Every day 9 AM': {'main': [[{'node': 'Download the tracker', 'type': 'main', 'index': 0}]]},
    'Download the tracker': {'main': [[{'node': 'Read the rows', 'type': 'main', 'index': 0}],
                                      [{'node': 'Tell us it failed', 'type': 'main', 'index': 0}]]},
    'Read the rows': {'main': [[{'node': 'Write the reminder', 'type': 'main', 'index': 0}]]},
    'Write the reminder': {'main': [[{'node': 'Post it', 'type': 'main', 'index': 0}]]},
}

wf = {'name': '#28 Storage payment reminders' + ('' if LIVE else ' [PREVIEW - posts to #automation-errors]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}
out = ROOT / ('workflows/storage-reminders-hook.json' if HOOK else 'workflows/storage-reminders.json')
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'PREVIEW', '(webhook test copy)' if HOOK else '')
