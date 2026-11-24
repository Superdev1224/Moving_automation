#!/usr/bin/env python3
"""Builds workflows/followup-reminders.json (item #2).

Every minute: ClickUp "SCHEDULED FOLLOW UPS" card whose follow-up time is 10 min away -> Slack #sales.
Every day 8:00 AM: list of today's + overdue follow-ups -> Slack #sales.
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
strip = lambda s: re.sub(r"\nif \(typeof module.*\n?$", "\n", s)
LIB = strip((ROOT / 'lib/parse-card.js').read_text()) + strip((ROOT / 'lib/followups.js').read_text())

TEST_MODE = '--live' not in sys.argv
CHANNEL = 'C0C2X8B9VS9' if TEST_MODE else 'C08PL61A32L'  # #automation-errors / #sales

CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

REMIND = LIB + f"""
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const store = $getWorkflowStaticData('global');
store.reminded = store.reminded || {{}};
return dueReminders(tasks, Date.now(), store.reminded).map(text => ({{ json: {{ channel: '{CHANNEL}', text }} }}));
"""

MORNING = LIB + f"""
const tasks = $input.all().flatMap(i => i.json.tasks || []);
return [{{ json: {{ channel: '{CHANNEL}', text: morningList(tasks, Date.now()) }} }}];
"""

def fetch(name, pos):
    return {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
            'parameters': {
                'method': 'GET',
                'url': 'https://api.clickup.com/api/v2/list/901705949004/task',
                'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                'sendQuery': True,
                'queryParameters': {'parameters': [{'name': 'statuses[]', 'value': 'scheduled follow ups'}]},
                'options': {'pagination': {'pagination': {
                    'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                    'paginationCompleteWhen': 'other',
                    'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                    'limitPagesFetched': True, 'maxRequests': 20}}}},
            'credentials': CRED_CLICKUP}

def slack(name, pos):
    return {'name': name, 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': pos,
            'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                           'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                           'text': '={{ $json.text }}',
                           'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
            'credentials': CRED_SLACK}

def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': {'jsCode': js.strip()}}

nodes = [
    {'name': 'Every minute', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 1}]}}},
    fetch('Get follow-up cards', [240, 0]),
    code('Due in 10 min?', REMIND, [480, 0]),
    slack('Post reminder', [720, 0]),
    {'name': 'Every day 8:00 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 260],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 8, 'triggerAtMinute': 0}]}}},
    fetch('Get follow-up cards (morning)', [240, 260]),
    code('Build morning list', MORNING, [480, 260]),
    slack('Post morning list', [720, 260]),
]

link = lambda n: {'main': [[{'node': n, 'type': 'main', 'index': 0}]]}
connections = {
    'Every minute': link('Get follow-up cards'),
    'Get follow-up cards': link('Due in 10 min?'),
    'Due in 10 min?': link('Post reminder'),
    'Every day 8:00 AM': link('Get follow-up cards (morning)'),
    'Get follow-up cards (morning)': link('Build morning list'),
    'Build morning list': link('Post morning list'),
}

wf = {'name': '#2 Follow-up reminders → Slack #sales' + (' [TEST MODE]' if TEST_MODE else ''),
      'nodes': nodes, 'connections': connections,
      # runs every minute: keep only failed runs so the database doesn't fill up
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/followup-reminders.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'TEST MODE' if TEST_MODE else 'LIVE')
