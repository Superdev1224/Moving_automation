#!/usr/bin/env python3
"""Builds workflows/estimates.json — Movegistics estimate → ClickUp card (Additional Integrations #4).

Every 10 minutes: the 50 most recently updated Movegistics estimates. For each new or changed one
(sent, re-sent, opened, signed, deposit paid) a comment with the total, job type and PDF link goes on
the customer's ClickUp card (matched by email, then phone, then name). The first run only takes a
baseline, so old estimates are not commented.

  python3 build/estimates.py          test mode  → comments previewed in #automation-errors
  python3 build/estimates.py --live   live       → comments on the cards
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                                # #automation-errors
LIST_ID = '901705949004'                              # All Leads

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/estimates.js').read_text()) + strip((ROOT / 'lib/booking.js').read_text())

CHANGES = LIB + """
// What changed since the last run? (staticData keeps one small snapshot per estimate)
const state = $getWorkflowStaticData('global');
const rows = $input.first().json.data?.list || [];
if (!rows.length) return [];                               // Movegistics hiccup: keep the old snapshot
const events = estimateEvents(rows, state.estimates || null);
state.estimates = rememberEstimates(rows, state.estimates, Date.now());
return events.map(ev => ({ json: ev }));
"""

MATCH = LIB + """
// Find each customer's card and write the comment.
const tasks = $input.all().flatMap(i => i.json.tasks || []);
const latestMd = {};                 // two estimates for one card in the same run build on each other
return $('What changed').all().map(item => {
  const ev = item.json, who = estimateCustomer(ev.estimate);
  const match = matchCard(tasks, who);
  const comment = estimateComment(ev);
  const where = match ? `<${match.task.url}|${match.task.name}> (matched by ${match.how})` : `NO CARD FOUND for ${who.name} ${who.email}`;
  let cardMd = null;
  if (match) {
    const id = match.task.id;
    cardMd = withEstimate(latestMd[id] ?? (match.task.markdown_description || ''), ev.estimate);
    if (cardMd) latestMd[id] = cardMd;
  }
  return { json: { cardId: match ? match.task.id : null, comment, cardMd,
    preview: `:test_tube: *Estimate → ClickUp* would comment on ${where}:\\n>${comment.replace(/\\n/g, '\\n>')}` } };
});
"""


def code(name, js, pos, once=False):
    # never set executeOnce on a Code node: it drops every input item after the first,
    # and these nodes read $input.all(). Code nodes already run once for all items.
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}


def with_retry(nodes, names):
    # a passing ClickUp/API hiccup is retried before anything decides a card does not exist
    for n in nodes:
        if n['name'] in names:
            n.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000})
    return nodes

nodes = [
    {'name': 'Every 10 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 10}]}}},
    {'name': 'Movegistics estimates', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'POST', 'url': 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1/estimate/list/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': '100821'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': json.dumps({'filters': [], 'page': {'limit': 50, 'pageId': 0},
                                            'sortBy': {'direction': 'DESC', 'property': 'updatedAt'}, 'timeZone': 'America/New_York'}),
                    'options': {}},
     'credentials': CRED_MG},
    code('What changed', CHANGES, [440, 0]),
    {'name': 'Open ClickUp cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [660, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [{'name': 'include_markdown_description', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 50}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Find the card', MATCH, [880, 0], once=True),
]

if LIVE:
    nodes += [
        {'name': 'Has a card?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1100, 0],
         'parameters': {'conditions': {
             'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
             'combinator': 'and',
             'conditions': [{'id': 'card', 'leftValue': '={{ $json.cardId }}', 'rightValue': '',
                             'operator': {'type': 'string', 'operation': 'notEmpty', 'singleValue': True}}]},
             'options': {}}},
        {'name': 'Comment on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 0],
         'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.cardId }}/comment',
                        'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                        'sendBody': True, 'specifyBody': 'json',
                        'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}', 'options': {}},
         'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
        {'name': 'Estimate on the card yet?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1320, 160],
         'parameters': {'conditions': {
             'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
             'combinator': 'and',
             'conditions': [{'id': 'md', 'leftValue': '={{ !!$json.cardMd }}', 'rightValue': '',
                             'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
             'options': {}}},
        {'name': 'Put the estimate on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1540, 160],
         'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.cardId }}',
                        'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                        'sendBody': True, 'specifyBody': 'json',
                        'jsonBody': '={{ JSON.stringify({ markdown_content: $json.cardMd }) }}', 'options': {}},
         'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
    ]
else:
    nodes += [
        {'name': 'Preview in Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1100, 0],
         'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                        'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'},
                        'text': '={{ $json.preview }}',
                        'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
         'credentials': CRED_SLACK},
    ]

nodes = with_retry(nodes, ['Open ClickUp cards'])
order = [n['name'] for n in nodes]
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}
if LIVE:
    connections['Has a card?'] = {'main': [[{'node': 'Comment on the card', 'type': 'main', 'index': 0},
                                            {'node': 'Estimate on the card yet?', 'type': 'main', 'index': 0}], []]}
    connections['Estimate on the card yet?'] = {'main': [[{'node': 'Put the estimate on the card', 'type': 'main', 'index': 0}], []]}
    connections.pop('Comment on the card', None)

wf = {'name': '#19 Movegistics estimate → ClickUp card' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/estimates.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
