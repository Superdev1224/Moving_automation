#!/usr/bin/env python3
"""Builds workflows/lost-reasons.json — why leads are lost (Additional Integrations #7).

Every day at 9:45 AM: cards that landed in "Moves Lost" since yesterday. Claude reads the team's own
notes on each card, picks the reason, and writes it back as a comment. The running tally is kept in
the workflow, and on the 1st of the month the summary goes to Slack.

The Google Sheet is added once the Sheets API is switched on for contact@expresshaulers.com.

  python3 build/lost-reasons.py          test mode: no comments written, summary to #automation-errors
  python3 build/lost-reasons.py --live   live: writes the reason on each card
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
CHANNEL = 'C09D8S04H62' if LIVE else 'C0C2X8B9VS9'     # #daily-digest-and-updates / #automation-errors
LIST_ID = '901705949004'
PER_RUN = 12                                            # cards judged in one Claude call
SHEET_ID = '1MSW69-pwFe1zHYAM_4fdKxQGwmLlcZ_gvklXFz9ZsN8'                # "Express Haulers - Lost Leads"
CRED_SHEETS = {'googleSheetsOAuth2Api': {'id': 'm3PnEzDECqHcJrrF', 'name': 'Google Sheets - Express Haulers'}}

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/lost-reasons.js').read_text())

RECENT = LIB + f"""
// Cards that landed in Moves Lost since yesterday.
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const since = Date.now() - 26 * 3600000;
const fresh = tasks.filter(t => +t.date_updated > since).slice(0, {PER_RUN});
return fresh.map(t => ({{ json: {{ id: t.id, task: t }} }}));
"""

CONTEXT = LIB + """
// Pair each card with its comments; skip any card that already carries a reason.
const cards = $('Lost since yesterday').all().map(i => i.json.task);
const rows = [];
$input.all().forEach((res, i) => {
  const ctx = cardContext(cards[i], res.json?.comments || []);
  if (!ctx.alreadyDone) rows.push(ctx);          // plain objects: the prompt reads id/name/notes
});
if (!rows.length) return [];
return [{ json: { cards: rows, claudeBody: lostReasonBody(rows) } }];
"""

DECIDE = LIB + f"""
// Claude answered: write the reason on each card and keep the monthly tally.
const state = $getWorkflowStaticData('global');
state.byMonth = state.byMonth || {{}};
state.rows = state.rows || [];
const cards = $('Card and its notes').first().json.cards;
const byId = Object.fromEntries(parseLostReasons($input.first().json).map(r => [r.id, r]));
const out = [];
for (const c of cards) {{
  const r = byId[c.id];
  if (!r) continue;
  const month = new Date(c.lostAt).toLocaleDateString('en-US', {{ timeZone: 'America/New_York', year: 'numeric', month: '2-digit' }});
  state.byMonth[month] = state.byMonth[month] || {{}};
  state.byMonth[month][r.reason] = (state.byMonth[month][r.reason] || 0) + 1;
  state.rows.push(sheetRow(c, r));
  if (state.rows.length > 5000) state.rows = state.rows.slice(-5000);
  out.push({{ json: {{ id: c.id, comment: lostComment(r), reason: r.reason, name: c.name,
    row: sheetRow(c, r) }} }});
}}
return out;
"""

MONTHLY = LIB + f"""
// On the 1st: last month's tally, from what the daily run recorded.
const state = $getWorkflowStaticData('global');
const now = new Date();
const last = new Date(now.getFullYear(), now.getMonth() - 1, 15);
const key = last.toLocaleDateString('en-US', {{ timeZone: 'America/New_York', year: 'numeric', month: '2-digit' }});
const counts = (state.byMonth || {{}})[key] || {{}};
const rows = Object.entries(counts).flatMap(([reason, n]) => Array(n).fill({{ Reason: reason }}));
if (!rows.length) return [];
const monthName = last.toLocaleDateString('en-US', {{ month: 'long', year: 'numeric' }});
return [{{ json: {{ channel: '{CHANNEL}', text: monthlyReport(rows, monthName) }} }}];
"""


def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}


nodes = [
    {'name': 'Every day 9:45 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 45}]}}},
    {'name': 'Lost cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'GET',
                    'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task'
                           '?include_markdown_description=true&include_closed=true'
                           '&order_by=updated&statuses%5B%5D=moves%20lost',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Lost since yesterday', RECENT, [440, 0]),
    {'name': 'Notes on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [660, 0],
     'parameters': {'method': 'GET', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.id }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Card and its notes', CONTEXT, [880, 0]),
    {'name': 'Ask Claude why', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, 0],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.claudeBody) }}',
                    'options': {}},
     'credentials': CRED_ANTHROPIC},
    code('Write the reason', DECIDE, [1320, 0]),
    {'name': 'Add a row to the sheet', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1540, 160],
     'parameters': {'method': 'POST',
                    'url': f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/'
                           "'Lost leads'!A:F:append?valueInputOption=USER_ENTERED",
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'googleSheetsOAuth2Api',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ values: [[ $json.row["Lost on"], $json.row.Customer,'
                                ' $json.row.Reason, $json.row["What happened"], $json.row.Card,'
                                ' $json.row.Certain ]] }) }}',
                    'options': {}},
     'credentials': CRED_SHEETS, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    # the sheet reads newest first (client, 2026-09-29), so re-sort once the day's rows are in
    {'name': 'Newest first', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 160],
     'executeOnce': True,
     'parameters': {'method': 'POST',
                    'url': f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'googleSheetsOAuth2Api',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': json.dumps({'requests': [{'sortRange': {
                        'range': {'sheetId': 170622439, 'startRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 6},
                        'sortSpecs': [{'dimensionIndex': 0, 'sortOrder': 'DESCENDING'}]}}]}),
                    'options': {}},
     'credentials': CRED_SHEETS, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Note it on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1540, 0],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.id }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},

    # --- the monthly summary ---
    {'name': 'First of the month', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 260],
     'parameters': {'rule': {'interval': [{'field': 'cronExpression', 'expression': '0 9 1 * *'}]}}},
    code('Last month in numbers', MONTHLY, [440, 260]),
    {'name': 'Post the report', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [660, 260],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
]

daily = ['Every day 9:45 AM', 'Lost cards', 'Lost since yesterday', 'Notes on the card', 'Card and its notes',
         'Ask Claude why', 'Write the reason', 'Note it on the card']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(daily, daily[1:])}
connections['Write the reason'] = {'main': [[{'node': 'Note it on the card', 'type': 'main', 'index': 0},
                                             {'node': 'Add a row to the sheet', 'type': 'main', 'index': 0}]]}
connections['Add a row to the sheet'] = {'main': [[{'node': 'Newest first', 'type': 'main', 'index': 0}]]}
connections['First of the month'] = {'main': [[{'node': 'Last month in numbers', 'type': 'main', 'index': 0}]]}
connections['Last month in numbers'] = {'main': [[{'node': 'Post the report', 'type': 'main', 'index': 0}]]}

wf = {'name': '#28 Why leads are lost' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/lost-reasons.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
