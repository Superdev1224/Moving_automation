#!/usr/bin/env python3
"""Builds workflows/booking-status-v2.json — booking status straight from Movegistics (#7/#8 rebuilt).

Every 5 minutes: read the recent Movegistics estimates and put each customer's card where the
estimate says it belongs — signed and deposit paid = Moves Booked, one of the two = Pending. It keeps
no memory between runs, so nothing can go stale: if the card is already right it does nothing.

It also works the other way round: a work request opened in Movegistics in the last week with no
card on the board at all gets one (after 30 minutes), in Opportunities, filled from the work request.
If the team then makes a card by hand for the same customer, theirs is kept: our card's comments are
copied onto it and ours is archived.

This replaces the first version, whose memory of "deposit seen" and "signature seen" was being wiped
by n8n's Gmail polling trigger, so the two halves never met and no card moved after 21 September.

  python3 build/booking-status-v2.py          dry run: reports what it would move
  python3 build/booking-status-v2.py --live   live: moves the cards
"""
import json, pathlib, re, sys, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'                 # #automation-errors
LIST_ID = '901705949004'
MG_URL = 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1'

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}

# The WHOLE board is read, every status (2026-11-18). Until then only a named list of statuses was
# read; around 11/15 the team renamed "negotiation review" to "negotiatie/review", the cards in it
# became invisible here, and 9 customers got a duplicate "Movegistics Lead" card in three days.
# Reading is harmless: only the statuses in MAY_MOVE_FROM (lib/booking-status.js) are ever changed.
CARDS_URL = (f'https://api.clickup.com/api/v2/list/{LIST_ID}/task?include_markdown_description=true&include_closed=true')

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = (strip((ROOT / 'lib/booking-status.js').read_text())
       + strip((ROOT / 'lib/estimates.js').read_text())
       + strip((ROOT / 'lib/booking.js').read_text())
       + strip((ROOT / 'lib/lead-template.js').read_text())
       + strip((ROOT / 'lib/mg-cards.js').read_text()))

DECIDE = LIB + f"""
// Where does Movegistics say each card should be?
const estimates = $('Movegistics estimates').first().json.data?.list || [];
const tasks = $('Cards that could move').all().flatMap(i => i.json.tasks || []);
// customers' cards also live on "All Influencer Leads" and "All Realtors" (Mahima's card was moved to
// the influencer board after her move, and this made her a second one): look there before creating
const others = $('Other boards').all().flatMap(i => i.json.tasks || []);
const opps = $('Movegistics work requests').first().json.data?.rows || [];
// the other boards count as "has a card" too; their statuses are not in MAY_MOVE_FROM, so
// those cards are recognised but never moved
const {{ moves, unmatched: unmatchedAll }} = bookingMoves(estimates, [...tasks, ...others], Date.now());
// "signed and paid, but no card to move" is said ONCE per estimate (it repeated every 5 minutes for
// Mitul Mundra, 2026-10-30) and again only if still unsolved a week later
const memory = $getWorkflowStaticData('global');
memory.unmatchedSaid = memory.unmatchedSaid || {{}};
for (const [k, v] of Object.entries(memory.unmatchedSaid)) if (Date.now() - v > 7 * 86400000) delete memory.unmatchedSaid[k];
const unmatchedKey = u => String(u.estimate || u.displayId || u.estimateId || u.email || u.name || JSON.stringify(u)).slice(0, 80);
const unmatched = unmatchedAll.filter(u => !memory.unmatchedSaid[unmatchedKey(u)]);
for (const u of unmatched) memory.unmatchedSaid[unmatchedKey(u)] = Date.now();
// the other direction: a Movegistics customer nobody has made a card for
// FAIL SAFE: cards are only created when the whole board was read. On 2026-10-02 ClickUp answered one
// read with an error, the board looked empty, and 30 duplicate cards were made in one run.
const pages = [...$('Cards that could move').all(), ...$('Other boards').all()];
const boardOk = !pages.some(p => p.json && p.json.error) && tasks.length >= 1500;
const newCards = boardOk ? movegisticsCards(opps, estimates, [...tasks, ...others], Date.now()) : [];
// and the duplicate catcher: a card the team made by hand after ours -> keep theirs, archive ours
const {{ merges, leftAlone }} = boardOk ? duplicateMerges([...tasks, ...others], Date.now()) : {{ merges: [], leftAlone: [] }};
// Booked influencers STAY in Moves Booked until the move is done (client, 2026-11-02: content wait is for
// moves that are done). The move to "influencers content wait" happens in the finished-moves workflow.
const handoffs = [];
const report = [bookingSlackReport(moves, unmatched, {json.dumps(LIVE)}),
  mgCardSlack(newCards, {json.dumps(LIVE)}), mergeSlack(merges, leftAlone, {json.dumps(LIVE)}), handoffSlack(handoffs)].filter(Boolean).join('\\n\\n');
const out = moves.map(m => ({{ json: {{ ...m, comment: bookingComment(m) }} }}));
out.push(...newCards.map(c => ({{ json: c }})));
out.push(...merges.map(m => ({{ json: m }})));
out.push(...handoffs.map(h => ({{ json: h }})));
if (report) out.push({{ json: {{ isReport: true, channel: '{ERRORS}', report }} }});
return out;
"""

NOTE = LIB + """
// the note for the team's card: what our automatic card held
const m = $('A duplicate to merge?').item.json;
return { json: { ...m, note: mergeNote(m, $json.comments || []) } };
"""

def with_retry(nodes, names):
    # a passing ClickUp/API hiccup is retried before anything decides a card does not exist
    for n in nodes:
        if n['name'] in names:
            n.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000})
    return nodes

nodes = [
    # every 5 minutes: at 15 the team was moving booked cards by hand before we got there (2026-09-30)
    {'name': 'Every 5 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 5}]}}},
    {'name': 'Movegistics estimates', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [220, 0],
     'parameters': {'method': 'POST', 'url': f'{MG_URL}/estimate/list/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': '100821'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': json.dumps({'filters': [], 'page': {'limit': 100, 'pageId': 0},
                                            'sortBy': {'direction': 'DESC', 'property': 'updatedAt'},
                                            'timeZone': 'America/New_York'}),
                    'options': {}},
     'credentials': CRED_MG},
    {'name': 'Movegistics work requests', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [330, 0],
     'parameters': {'method': 'POST', 'url': f'{MG_URL}/opportunity/list/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': '100821'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': json.dumps({'filters': [], 'page': {'limit': 100, 'pageId': 0},
                                            'sortBy': {'direction': 'DESC', 'property': 'createdAt'},
                                            'timeZone': 'America/New_York'}),
                    'options': {}},
     'credentials': CRED_MG},
    {'name': 'Cards that could move', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [440, 0],
     'parameters': {'method': 'GET', 'url': CARDS_URL,
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 90}}}},   # 3,776 cards = 38 pages on 2026-11-18
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Which other boards', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [550, 160],
     'parameters': {'jsCode': 'return ["901705958022", "901712515969"].map(list => ({ json: { list } }));'}},
    {'name': 'Other boards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [600, 160],
     'parameters': {'method': 'GET', 'url': '=https://api.clickup.com/api/v2/list/{{ $json.list }}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'include_markdown_description', 'value': 'true'},
                        {'name': 'include_closed', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 10}}}},
     'credentials': CRED_CLICKUP, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Where should each card be?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [660, 0],
     'parameters': {'jsCode': DECIDE.strip()}},
    {'name': 'A card or the report?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [880, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isReport', 'leftValue': '={{ $json.isReport }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Post the summary', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1100, -120],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.report }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    # the duplicate catcher (2026-10-05): read our card's comments, write them up for the team's card,
    # archive ours, then leave the note. A failed read or archive stops there and is tried next run.
    # booked influencer: to the influencer board, status content wait, then a note on the card (2026-11-01)
    {'name': 'Influencer booked?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1100, 600],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isHandoff', 'leftValue': '={{ $json.isHandoff }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'To the influencer board', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 640],
     'parameters': {'method': 'PUT',
                    'url': '=https://api.clickup.com/api/v3/workspaces/9017263768/tasks/{{ $json.id }}/home_list/{{ $json.toList }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    # the influencer board has no "moves booked": ClickUp needs to be told what it becomes
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status_mappings: [{ source_status: "moves booked", destination_status: $json.toStatus }] }) }}',
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueErrorOutput'},
    {'name': 'Set content wait', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1540, 640],
     'parameters': {'method': 'PUT', 'url': "=https://api.clickup.com/api/v2/task/{{ $('Influencer booked?').item.json.id }}",
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': "={{ JSON.stringify({ status: $('Influencer booked?').item.json.toStatus }) }}", 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Say so on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 640],
     'parameters': {'method': 'POST', 'url': "=https://api.clickup.com/api/v2/task/{{ $('Influencer booked?').item.json.id }}/comment",
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': "={{ JSON.stringify({ comment_text: $('Influencer booked?').item.json.comment, notify_all: false }) }}",
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'A duplicate to merge?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1100, 360],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isMerge', 'leftValue': '={{ $json.isMerge }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Its comments', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 420],
     'parameters': {'method': 'GET', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.autoId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueErrorOutput'},
    {'name': 'Write the note', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1540, 420],
     'parameters': {'mode': 'runOnceForEachItem', 'jsCode': NOTE.strip()}},
    {'name': 'Archive our copy', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1760, 420],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.autoId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '{"archived": true}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueErrorOutput'},
    {'name': 'Note on their card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1980, 420],
     'parameters': {'method': 'POST',
                    'url': "=https://api.clickup.com/api/v2/task/{{ $('Write the note').item.json.keepId }}/comment",
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': "={{ JSON.stringify({ comment_text: $('Write the note').item.json.note, notify_all: false }) }}",
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'New card or a move?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1100, 120],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isNew', 'leftValue': '={{ $json.isNew }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Make the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 20],
     'parameters': {'method': 'POST', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.cardBody) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Move the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1320, 220],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.id }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ status: $json.to }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Say why on the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1540, 220],
     'parameters': {'method': 'POST',
                    'url': "=https://api.clickup.com/api/v2/task/{{ $('New card or a move?').item.json.id }}/comment",
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': "={{ JSON.stringify({ comment_text: $('New card or a move?').item.json.comment, notify_all: false }) }}",
                    'options': {}},
     'credentials': CRED_CLICKUP, 'disabled': not LIVE, 'onError': 'continueRegularOutput'},
]

nodes = with_retry(nodes, ['Cards that could move', 'Other boards', 'Movegistics estimates', 'Movegistics work requests',
                           'Its comments', 'Archive our copy', 'Note on their card', 'To the influencer board', 'Set content wait'])
chain = ['Every 5 minutes', 'Movegistics estimates', 'Movegistics work requests', 'Cards that could move',
         'Which other boards', 'Other boards', 'Where should each card be?', 'A card or the report?']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(chain, chain[1:])}
connections['A card or the report?'] = {'main': [[{'node': 'Post the summary', 'type': 'main', 'index': 0}],
                                                 [{'node': 'Influencer booked?', 'type': 'main', 'index': 0}]]}
connections['Influencer booked?'] = {'main': [[{'node': 'To the influencer board', 'type': 'main', 'index': 0}],
                                              [{'node': 'A duplicate to merge?', 'type': 'main', 'index': 0}]]}
# a failed board move stops there (error output unconnected): the card stays in Moves Booked and is tried next run
connections['To the influencer board'] = {'main': [[{'node': 'Set content wait', 'type': 'main', 'index': 0}], []]}
connections['Set content wait'] = {'main': [[{'node': 'Say so on the card', 'type': 'main', 'index': 0}]]}
connections['A duplicate to merge?'] = {'main': [[{'node': 'Its comments', 'type': 'main', 'index': 0}],
                                                  [{'node': 'New card or a move?', 'type': 'main', 'index': 0}]]}
# second outputs (errors) go nowhere: the pair is still on the board and is tried again next run
connections['Its comments'] = {'main': [[{'node': 'Write the note', 'type': 'main', 'index': 0}], []]}
connections['Write the note'] = {'main': [[{'node': 'Archive our copy', 'type': 'main', 'index': 0}]]}
connections['Archive our copy'] = {'main': [[{'node': 'Note on their card', 'type': 'main', 'index': 0}], []]}
connections['New card or a move?'] = {'main': [[{'node': 'Make the card', 'type': 'main', 'index': 0}],
                                               [{'node': 'Move the card', 'type': 'main', 'index': 0}]]}
connections['Move the card'] = {'main': [[{'node': 'Say why on the card', 'type': 'main', 'index': 0}]]}

wf = {'name': '#7/#8 Booking status from Movegistics' + ('' if LIVE else ' [DRY RUN]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/booking-status-v2.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'DRY RUN')
