#!/usr/bin/env python3
"""Builds workflows/lead-sync.json — new leads go into Movegistics AND onto the ClickUp board.

Step 1 of the "leads straight into Movegistics" plan: website contact-form leads (from the
Shopify email). Yelp and Thumbtack join once their direct webhooks are connected.

For every new lead:
  1. look for the person in Movegistics (leads + contacts, by email or phone)
  2. look for an open ClickUp card for them (email / phone / name)
  3. create the Movegistics lead if it's new  (Movegistics = source of truth)
  4. create a ClickUp card in "New Leads" if there isn't one, in the team's card template;
     if there already is one, add a comment about the new enquiry instead
Problems go to #automation-errors; successes are silent (the card itself is the confirmation).
"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
strip = lambda s: re.sub(r"\nif \(typeof module.*\n?$", "\n", s)
LIB = (strip((ROOT / 'lib/parse-card.js').read_text()) + strip((ROOT / 'lib/lead-template.js').read_text() + '\n' + (ROOT / 'lib/parse-lead.js').read_text()) + strip((ROOT / 'lib/lead-sync.js').read_text())
       + strip((ROOT / 'lib/booking.js').read_text()))

ENABLED_SOURCES = ['Website']          # add 'Yelp', 'Thumbtack' when their webhooks replace the emails
ERRORS = 'C0C2X8B9VS9'
LIST_ID = '901705949004'
MG = 'https://0ft1db7rtg.execute-api.us-east-1.amazonaws.com/prod/open/v1'
MG_LOCATION = '100821'

CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
CRED_CLICKUP = {'clickUpApi': {'id': 'jumjES1E83DxkLsH', 'name': 'ClickUp - Express Haulers'}}
CRED_MG = {'httpHeaderAuth': {'id': 'U0m6BwX0J1tdgtjN', 'name': 'Movegistics - Express Haulers'}}

READ = LIB + f"""
// Two ways in:
//   - a lead email (website contact form), read here;
//   - a lead handed over by the Thumbtack / Yelp webhook workflow (already read there).
const ENABLED = {json.dumps(ENABLED_SOURCES)};
const out = [];
for (const item of $input.all()) {{
  let lead = item.json.pushedLead || null;
  if (!lead) {{
    lead = parseLeadEmail(item.json);
    if (!lead || !ENABLED.includes(lead.source)) continue;
  }}
  if (!lead.name && !lead.phone && !lead.email) continue;          // nothing to identify them by
  out.push({{ json: {{ lead, key: searchKey(lead), mgBody: movegisticsLead(lead) }} }});
}}
return out;
"""

DECIDE = LIB + """
// Already in Movegistics? Already on the board? Decide what to create.
const leads = $('Read lead').all();
const mgLeads = $('Movegistics: leads').all();
const mgContacts = $('Movegistics: contacts').all();
// the influencer board too: an influencer's card is often moved there, and their next DM must find it
// (not "All Realtors": a realtor's own card would swallow every new client they refer)
const tasks = [...$('ClickUp: open cards').all(), ...$('Other boards').all()].flatMap(i => i.json.tasks || []);
return leads.map((item, i) => {
  const { lead, mgBody } = item.json;
  // same person + same move date = duplicate; a different move date = a new move (repeat customer)
  const mg = existingMovegisticsMove(lead, mgLeads[i]?.json?.data?.rows, mgContacts[i]?.json?.data?.rows);
  const card = existingCardForMove(tasks, lead);
  const returning = !!(mg?.returning || card?.returning);
  // "October 20th" (Instagram, texts) becomes 2026-10-20, so the card reads 10/20 like every other
  // card and its name carries the date
  const when = leadDate(lead, Date.now());
  const date = when ? `${when.y}-${String(when.m).padStart(2, '0')}-${String(when.d).padStart(2, '0')}` : lead.date;
  const theLead = { ...lead, date, returning };
  // a follow-up to a card we made: its Movegistics lead is written on the card, so fill that one in
  // rather than opening a second lead (first messages often have no phone or email to match on)
  const mgOnCard = card?.task ? mgIdOnCard(card.task) : null;
  const cardFill = card?.task ? fillCardBlanks(card.task.markdown_description || card.task.description, theLead) : null;
  return { json: { lead: theLead, mgBody: movegisticsLead(theLead),
    // someone who already has a card but gave no email or phone (an Instagram customer chatting on):
    // Movegistics cannot recognise them, so asking it would only open a second lead (Mahima Goyal, 10/12)
    createLead: (!mg || !!mg.returning) && !mgOnCard && !(card?.task && !lead.email && !lead.phone) && !lead.noMovegistics,
    mgUpdateId: mgOnCard, cardFill,
    mgExisting: mg && !mg.returning ? mg : null, mgKnown: mg || null,
    createCard: !card?.task, cardId: card?.task ? card.task.id : null, cardName: card?.task ? card.task.name : null,
    returning } };
});
"""

AFTER_CREATE = """
// Movegistics answered: keep the new lead's id with the lead it belongs to.
const asked = $('New in Movegistics?').all(0);
return $input.all().map((item, i) => ({ json: { ...asked[i].json,
  mgLeadId: item.json?.data?.id || null,
  // Movegistics allows one lead per email/phone. For a returning customer with a new move date that
  // is expected: the card says so and the team adds the new date to the existing Movegistics record.
  mgAlready: !item.json?.data?.id && /already exists/i.test(item.json?.message || '') && asked[i].json.returning,
  mgError: item.json?.data?.id || (/already exists/i.test(item.json?.message || '') && asked[i].json.returning) ? null
    : (item.json?.message || item.json?.error?.message || 'no id returned') } }));
"""

CARD = LIB + """
// Build the ClickUp card (or the comment for an existing card) and flag any problem.
return $input.all().map(item => {
  const j = item.json;
  const mgId = j.mgLeadId || (j.mgExisting && `${j.mgExisting.kind} ${j.mgExisting.id} (already existed)`)
    || (j.mgAlready && `this customer is already in Movegistics (${j.mgKnown?.kind || 'lead'} ${j.mgKnown?.id || ''}), so please add the new move date there`) || null;
  const who = j.lead.name || j.lead.phone || j.lead.email;
  return { json: { ...j,
    cardBody: clickupCard(j.lead, mgId),
    comment: (j.lead.source === 'Instagram' ? '💬 Instagram message' : `🔁 ${j.lead.source} enquiry received again`)
      + ` (${new Date().toLocaleString('en-US', { timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' })}).`
      + (j.lead.details?.length ? ` Message: ${j.lead.details.map(([, v]) => v).join(' ').slice(0, 300)}` : '')
      + (j.cardFill ? ` Added to the card: ${j.cardFill.filled.join(', ').toLowerCase()}.` : ''),
    problem: j.mgError ? `:warning: ${j.lead.source} lead *${who}* could not be added to Movegistics: ${j.mgError}` : null,
  } };
});
"""

MG_FILL = LIB + """
// Find the card's Movegistics lead among the recent ones and fill in only what it is missing.
const asked = $('Movegistics lead to fill?').all(0);
const out = [];
$input.all().forEach((res, i) => {
  const j = asked[i] ? asked[i].json : null;
  if (!j) return;
  const row = (res.json?.data?.rows || []).find(r => r._id === j.mgUpdateId);
  const fill = fillMgBlanks(row, j.lead);
  if (fill) out.push({ json: { mgPut: fill.body, filled: fill.filled, name: j.lead.name } });
});
return out;
"""

COPY_CHECK = LIB + """
// A minute after making the card: is there an older card for this same enquiry that the board did not
// show yet when we looked? Then ours is the copy - the oldest card is kept (see earlierCardFor).
const mine = $('Create ClickUp card').item.json;
const prepared = $('Prepare card').item.json;
const keep = earlierCardFor($json.tasks || [], mine, prepared.lead, Date.now());
if (!keep) return { json: { isCopy: false } };
return { json: { isCopy: true, copyId: mine.id, cardId: keep.id, cardName: keep.name, lead: prepared.lead,
  comment: prepared.comment,
  cardFill: fillCardBlanks(keep.markdown_description || keep.description, prepared.lead) } };
"""

def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos,
            'parameters': {'jsCode': js.strip()}}

def if_node(name, left, pos):
    return {'name': name, 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': pos,
            'parameters': {'conditions': {
                'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
                'combinator': 'and',
                'conditions': [{'id': name, 'leftValue': left, 'rightValue': '',
                                'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
                'options': {}}}

def mg_search(name, what, pos):
    return {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': pos,
            'parameters': {'method': 'POST', 'url': f'{MG}/{what}/list/',
                           'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                           'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': MG_LOCATION}]},
                           'sendBody': True, 'specifyBody': 'json',
                           'jsonBody': "={{ JSON.stringify({ filters: [], page: { limit: 10, pageId: 0 }, query: $('Read lead').item.json.key, sortBy: { direction: 'DESC', property: 'updatedAt' }, timeZone: 'America/New_York' }) }}",
                           'options': {}},
            'credentials': CRED_MG, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'}

def slack(name, pos, text_expr, channel=ERRORS):
    return {'name': name, 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': pos,
            'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                           'channelId': {'__rl': True, 'value': channel, 'mode': 'id'},
                           'text': text_expr, 'otherOptions': {'includeLinkToWorkflow': False}},
            'credentials': CRED_SLACK}

def with_retry(nodes, names):
    # a passing ClickUp/API hiccup is retried before anything decides a card does not exist
    for n in nodes:
        if n['name'] in names:
            n.update({'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000})
    return nodes

nodes = [
    # off since 2026-10-01: the website form now posts straight to #8b from the site itself, and reading the
    # Shopify email as well made every website lead arrive twice (sometimes as two cards)
    {'name': 'Website lead email', 'disabled': True, 'type': 'n8n-nodes-base.gmailTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'pollTimes': {'item': [{'mode': 'everyMinute'}]}, 'simple': False,
                    'filters': {'q': 'from:mailer@shopify.com subject:"New customer message on"'}, 'options': {}},
     'credentials': CRED_GMAIL},
    {'name': 'Handed over by another workflow', 'type': 'n8n-nodes-base.executeWorkflowTrigger', 'typeVersion': 1.1,
     'position': [0, 200], 'parameters': {'inputSource': 'passthrough'}},
    code('Read lead', READ, [220, 0]),
    mg_search('Movegistics: leads', 'lead', [440, 0]),
    mg_search('Movegistics: contacts', 'contact', [660, 0]),
    {'name': 'ClickUp: open cards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [880, 0],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [{'name': 'include_markdown_description', 'value': 'true'}]},
                    'options': {'pagination': {'pagination': {
                        'parameters': {'parameters': [{'type': 'qs', 'name': 'page', 'value': '={{ $pageCount }}'}]},
                        'paginationCompleteWhen': 'other',
                        'completeExpression': '={{ $response.body.last_page !== false || !($response.body.tasks || []).length }}',
                        'limitPagesFetched': True, 'maxRequests': 25}}},
                    'executeOnce': True},
     'credentials': CRED_CLICKUP},
    {'name': 'Which other boards', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [990, 160],
     # Influencers and All Realtors: the team moves cards there and makes influencer cards there by hand
     'parameters': {'jsCode': 'return ["901705958022", "901712515969"].map(list => ({ json: { list } }));'}},
    {'name': 'Other boards', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1040, 160],
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
    code('Decide', DECIDE, [1100, 0]),
    if_node('New in Movegistics?', '={{ $json.createLead }}', [1320, 0]),
    {'name': 'Create Movegistics lead', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1540, -100],
     'parameters': {'method': 'POST', 'url': f'{MG}/lead/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': MG_LOCATION}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.mgBody) }}', 'options': {}},
     'credentials': CRED_MG, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Lead created', AFTER_CREATE, [1760, -100]),
    {'name': 'Together again', 'type': 'n8n-nodes-base.merge', 'typeVersion': 3, 'position': [1980, 0],
     'parameters': {'numberInputs': 2}},
    code('Prepare card', CARD, [2200, 0]),
    if_node('Needs a card?', '={{ $json.createCard }}', [2420, 0]),
    {'name': 'Create ClickUp card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2640, -100],
     # a lead can name its board (an influencer who is not moving goes to All Influencer Leads)
     'parameters': {'method': 'POST', 'url': f'=https://api.clickup.com/api/v2/list/{{{{ $json.lead.cardList || "{LIST_ID}" }}}}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.cardBody) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'onError': 'continueErrorOutput'},
    {'name': 'Note on existing card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2640, 100],
     'parameters': {'method': 'POST', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.cardId }}/comment',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ comment_text: $json.comment, notify_all: false }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
    if_node('Blanks on the card?', '={{ !!$json.cardFill }}', [2640, 220]),
    {'name': 'Fill in the card', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2860, 220],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.cardId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify({ markdown_content: $json.cardFill.markdown }) }}', 'options': {}},
     'credentials': CRED_CLICKUP, 'onError': 'continueRegularOutput'},
    if_node('Movegistics lead to fill?', '={{ !!$json.mgUpdateId }}', [2640, 380]),
    {'name': 'Movegistics: recent leads', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [2860, 380],
     'parameters': {'method': 'POST', 'url': f'{MG}/lead/list/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': MG_LOCATION}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': json.dumps({'filters': [], 'page': {'limit': 100, 'pageId': 0},
                                            'sortBy': {'direction': 'DESC', 'property': 'createdAt'},
                                            'timeZone': 'America/New_York'}),
                    'options': {}},
     'credentials': CRED_MG, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    code('Fill in Movegistics blanks', MG_FILL, [3080, 380]),
    {'name': 'Update Movegistics lead', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [3300, 380],
     'parameters': {'method': 'PUT', 'url': f'{MG}/lead/',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-LOCATION-ID', 'value': MG_LOCATION}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.mgPut) }}', 'options': {}},
     'credentials': CRED_MG, 'onError': 'continueRegularOutput'},
    # several messages in a row: each run made a card before it could see the others' (2026-10-05)
    {'name': 'Wait a minute', 'type': 'n8n-nodes-base.wait', 'typeVersion': 1.1, 'position': [2860, -260],
     'webhookId': '6f1d2c4e-8a3b-4c5d-9e7f-1a2b3c4d5e6f',
     'parameters': {'resume': 'timeInterval', 'amount': 60, 'unit': 'seconds'}},
    {'name': 'Cards made just now', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [3080, -260],
     'parameters': {'method': 'GET', 'url': f'https://api.clickup.com/api/v2/list/{LIST_ID}/task',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'order_by', 'value': 'created'}, {'name': 'include_closed', 'value': 'true'},
                        {'name': 'include_markdown_description', 'value': 'true'}]},
                    'options': {}},
     'credentials': CRED_CLICKUP, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000,
     'onError': 'continueErrorOutput'},
    {'name': 'Ours a copy?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [3300, -260],
     'parameters': {'mode': 'runOnceForEachItem', 'jsCode': COPY_CHECK.strip()}},
    if_node('A copy?', '={{ $json.isCopy }}', [3520, -260]),
    {'name': 'Archive our copy', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [3740, -260],
     'parameters': {'method': 'PUT', 'url': '=https://api.clickup.com/api/v2/task/{{ $json.copyId }}',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'clickUpApi',
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '{"archived": true}', 'options': {}},
     'credentials': CRED_CLICKUP, 'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 3000,
     'onError': 'continueErrorOutput'},
    {'name': 'Back to the first card', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [3960, -260],
     'parameters': {'mode': 'runOnceForEachItem', 'jsCode': "return { json: $('Ours a copy?').item.json };"}},
    if_node('Problem?', '={{ !!$json.problem }}', [2420, 560]),
    slack('Alert: Movegistics problem', [2640, 260], '={{ $json.problem }}'),
    slack('Alert: card not created', [2860, -100],
          "={{ ':warning: ' + $('Prepare card').item.json.lead.source + ' lead *' + ($('Prepare card').item.json.lead.name || '?') + '* could not be added to ClickUp: ' + ($json.error?.message || 'unknown error') }}"),
]

nodes = with_retry(nodes, ['ClickUp: open cards', 'Other boards'])
one = lambda n, i=0: [{'node': n, 'type': 'main', 'index': i}]
connections = {
    'Website lead email': {'main': [one('Read lead')]},
    'Handed over by another workflow': {'main': [one('Read lead')]},
    'Read lead': {'main': [one('Movegistics: leads')]},
    'Movegistics: leads': {'main': [one('Movegistics: contacts')]},
    'Movegistics: contacts': {'main': [one('ClickUp: open cards')]},
    'ClickUp: open cards': {'main': [one('Which other boards')]},
    'Which other boards': {'main': [one('Other boards')]},
    'Other boards': {'main': [one('Decide')]},
    'Decide': {'main': [one('New in Movegistics?')]},
    'New in Movegistics?': {'main': [one('Create Movegistics lead'), one('Together again', 1)]},
    'Create Movegistics lead': {'main': [one('Lead created')]},
    'Lead created': {'main': [one('Together again')]},
    'Together again': {'main': [one('Prepare card')]},
    'Prepare card': {'main': [[{'node': 'Needs a card?', 'type': 'main', 'index': 0},
                               {'node': 'Problem?', 'type': 'main', 'index': 0}]]},
    'Needs a card?': {'main': [one('Create ClickUp card'),
                               [{'node': 'Note on existing card', 'type': 'main', 'index': 0},
                                {'node': 'Blanks on the card?', 'type': 'main', 'index': 0},
                                {'node': 'Movegistics lead to fill?', 'type': 'main', 'index': 0}]]},
    'Blanks on the card?': {'main': [one('Fill in the card'), []]},
    'Movegistics lead to fill?': {'main': [one('Movegistics: recent leads'), []]},
    'Movegistics: recent leads': {'main': [one('Fill in Movegistics blanks')]},
    'Fill in Movegistics blanks': {'main': [one('Update Movegistics lead')]},
    'Create ClickUp card': {'main': [one('Wait a minute'), one('Alert: card not created')]},
    'Wait a minute': {'main': [one('Cards made just now')]},
    'Cards made just now': {'main': [one('Ours a copy?'), []]},      # a failed read: leave it be
    'Ours a copy?': {'main': [one('A copy?')]},
    'A copy?': {'main': [one('Archive our copy'), []]},
    'Archive our copy': {'main': [one('Back to the first card'), []]},
    # then exactly what happens when a message finds an existing card: a note, and its blanks filled
    'Back to the first card': {'main': [[{'node': 'Note on existing card', 'type': 'main', 'index': 0},
                                         {'node': 'Blanks on the card?', 'type': 'main', 'index': 0}]]},
    'Problem?': {'main': [one('Alert: Movegistics problem'), []]},
}

wf = {'name': '#16 New leads → Movegistics + ClickUp card', 'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}
out = ROOT / 'workflows/lead-sync.json'
out.write_text(json.dumps(wf, indent=2))
print(out)
