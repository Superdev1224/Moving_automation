#!/usr/bin/env python3
"""Builds workflows/moves-complete.json — finished moves close themselves off (client's status rules).

Every day at 9:15 AM New York: booked cards ("moves booked", "upsell in progess", "interested
upselling", "upsold customers") whose move date has passed go to "moves complete", with a comment saying why. Cards whose dates disagree or cannot be
read are never touched; they are listed instead so the team can fix them.

  python3 build/moves-complete.py          dry run: reports what it would move, changes nothing
  python3 build/moves-complete.py --live   live: really moves the cards
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                                     # #automation-errors
LIST_ID = '901705949004'                                   # All Leads

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/parse-card.js').read_text()) + strip((ROOT / 'lib/moves-complete.js').read_text())

FINISHED = LIB + f"""
// Which booked moves are over? (dates that disagree are reported, never moved)
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const {{ done, unreadable }} = finishedMoves(tasks, Date.now());
const report = completeSlack(done, unreadable, {json.dumps(LIVE)});
const out = done.map(r => ({{ json: {{ ...r, toStatus: r.influencer ? CONTENT_WAIT : TO_STATUS, toList: r.influencer ? INFLUENCER_LIST : '',
  comment: completeComment(r), report: '' }} }}));
// the report rides on its own item so it is posted once, even when nothing moved
if (report) out.push({{ json: {{ report, channel: '{ERRORS}', isReport: true }} }});
return out;
"""

nodes = [
    {'name': 'Every day 9:15 AM', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 9, 'triggerAtMinute': 15}]}}},
    {'name': 'Booked and upsold cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     # repeated statuses[] must live in the URL: n8n keeps only the last same-named query parameter
     'parameters': {'method': 'GET',
                    'url': ('https://api.clickup.com/api/v2/list/901705949004/task'
                            '?include_markdown_description=true'
                            '&statuses%5B%5D=moves%20booked&statuses%5B%5D=upsell%20in%20progess'
                            '&statuses%5B%5D=interested%20upselling&statuses%5B%5D=upsold%20customers'),
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 10}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Finished moves', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [440, 0],
     'parameters': {'jsCode': FINISHED.strip()}},
    {'name': 'A card or the report?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [660, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isReport', 'leftValue': '={{ $json.isReport }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Post the summary', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [880, -120],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.report }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'Influencer?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [770, 260],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'influencer', 'leftValue': '={{ $json.influencer }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    # to the influencer board; it has none of the booked statuses, so ClickUp is told what this one becomes
    {'name': 'To the influencer board', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [880, 300],
     'parameters': {'method': 'PUT',
                    'url': '=https://api.clickup.com/api/v3/workspaces/9017263768/tasks/{{ $json.id }}/home_list/{{ $json.toList }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status_mappings: [{ source_status: $json.status, destination_status: $json.toStatus }] }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueErrorOutput'},
    {'name': 'Move the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [880, 120],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.id }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status: $json.toStatus }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Say why on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1100, 120],
     'parameters': {'method': 'POST', 'url': "=https://api.clickup.com/api/v2/task/{{ $('A card or the report?').item.json.id }}/comment",
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': "={{ JSON.stringify({ comment_text: $('A card or the report?').item.json.comment, notify_all: false }) }}",
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
]

connections = {
    'Every day 9:15 AM': {'main': [[{'node': 'Booked and upsold cards', 'type': 'main', 'index': 0}]]},
    'Booked and upsold cards': {'main': [[{'node': 'Finished moves', 'type': 'main', 'index': 0}]]},
    'Finished moves': {'main': [[{'node': 'A card or the report?', 'type': 'main', 'index': 0}]]},
    'A card or the report?': {'main': [[{'node': 'Post the summary', 'type': 'main', 'index': 0}],
                                       [{'node': 'Influencer?', 'type': 'main', 'index': 0}]]},
    'Influencer?': {'main': [[{'node': 'To the influencer board', 'type': 'main', 'index': 0}],
                             [{'node': 'Move the card', 'type': 'main', 'index': 0}]]},
    # a failed board move leaves the card where it is (error output unconnected) - tried again tomorrow
    'To the influencer board': {'main': [[{'node': 'Say why on the card', 'type': 'main', 'index': 0}], []]},
    'Move the card': {'main': [[{'node': 'Say why on the card', 'type': 'main', 'index': 0}]]},
}

wf = {'name': '#25 Finished moves → Moves Complete' + ('' if LIVE else ' [DRY RUN - nothing moved]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/moves-complete.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'DRY RUN')
