#!/usr/bin/env python3
"""Builds workflows/instagram-dm.json — Instagram DMs → Slack + ClickUp + Movegistics.

Two jobs on one endpoint (https://n8n.expresshaulers.com/webhook/instagram-dm):
  GET  — Meta's subscription check: it must echo hub.challenge back when the verify token matches.
  POST — a real message event. Until Meta approves the app, only messages from accounts with a role
         on the app arrive here, which is what we use for the review demo.

A DM is treated like any other lead: Slack post in the client's template, then the lead-sync workflow
creates the Movegistics lead and the ClickUp card.

  python3 build/instagram-dm.py          test mode  → #automation-errors, raw payload shown
  python3 build/instagram-dm.py --live   live       → #inbound-leads + lead-sync hand-over
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ASK_LIVE = '--reply' in sys.argv        # actually send the "please send your details" DM
ERRORS = 'C0C2X8B9VS9'                 # #automation-errors
LEADS = 'C0C27RC6QTT'                  # #inbound-leads
PARTNERS = 'C08P1DFLJGJ'               # #realtors-and-influencers
CHANNEL = LEADS if LIVE else ERRORS
VERIFY_TOKEN = next(l.split('=', 1)[1].strip() for l in (ROOT / '.secrets').read_text().splitlines()
                    if l.startswith('IG_VERIFY_TOKEN='))
def _maybe(name):
    for line in (ROOT / '.secrets').read_text().splitlines():
        if line.startswith(name + '='):
            return line.split('=', 1)[1].strip()
    return ''


def _secret(name):
    return next(l.split('=', 1)[1].strip() for l in (ROOT / '.secrets').read_text().splitlines()
                if l.startswith(name + '='))

# both Instagram accounts of the business, by every id Meta uses for them
ACCOUNTS = {
    '17841450121094501': {'handle': 'expresshaulers', 'token': _secret('IG_ACCESS_TOKEN')},
    '28433014466364978': {'handle': 'expresshaulers', 'token': _secret('IG_ACCESS_TOKEN')},
    '17841413456955431': {'handle': 'expresshaulersmovers', 'token': _secret('IG_ACCESS_TOKEN_2')},
    '38927551020226029': {'handle': 'expresshaulersmovers', 'token': _secret('IG_ACCESS_TOKEN_2')},
}
SEED_TOKEN = _secret('IG_ACCESS_TOKEN')
# Accounts Meta has stopped from sending through the API. Replies for them are only previewed.
# @expresshaulers: error 368 "temporarily blocked from this action" on 2026-09-29 - take it off
# this list once a test send works again.
REPLIES_PAUSED = ['expresshaulers']
# Meta may sign with the Instagram app secret or the main app secret, so both are accepted.
APP_SECRETS = [v for v in (_maybe('IG_APP_SECRET'), _maybe('FB_APP_SECRET')) if v]
LEAD_SYNC_WF = (ROOT / 'workflows/lead-sync.id').read_text().strip()

CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = (strip((ROOT / 'lib/lead-template.js').read_text())
       + strip((ROOT / 'lib/instagram-triage.js').read_text())
       + strip((ROOT / 'lib/collab-email.js').read_text()))
CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}

READ = LIB + f"""
// Meta signs every delivery with the app secret. Anything unsigned or wrongly signed is dropped:
// the URL alone must not be enough to push fake leads into the board.
const crypto = require('crypto');
const APP_SECRETS = {json.dumps(APP_SECRETS)};
function metaSigned(item) {{
  const sig = String(item.json.headers?.['x-hub-signature-256'] || '');
  const raw = item.binary?.data?.data
    ? Buffer.from(item.binary.data.data, 'base64')
    : Buffer.from(JSON.stringify(item.json.body ?? {{}}));
  if (!sig.startsWith('sha256=')) return false;
  return APP_SECRETS.some(secret => {{
    const mine = 'sha256=' + crypto.createHmac('sha256', secret).update(raw).digest('hex');
    try {{ return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(mine)); }} catch (e) {{ return false; }}
  }});
}}

// Meta sends one entry per account, each with the messages that arrived.
const SHOW_RAW = {json.dumps(not LIVE)};
const ACCOUNTS = {json.dumps(ACCOUNTS)};       // which of the two business accounts got the message
const out = [];
for (const item of $input.all()) {{
  if (!metaSigned(item)) {{                               // not signed with a secret we know
    out.push({{ json: {{ channel: '{ERRORS}', rejected: true,
      text: ':lock: *An Instagram webhook was rejected* - the signature did not match either app secret.'
        + ' If this was a real message, the main app secret is probably missing on our side.' }} }});
    continue;
  }}
  const raw = item.binary?.data?.data ? Buffer.from(item.binary.data.data, 'base64').toString('utf8') : '';
  const body = raw ? JSON.parse(raw) : (item.json.body ?? item.json);
  for (const entry of body.entry || []) {{
    for (const m of entry.messaging || []) {{
      const text = (m.message?.text || '').trim();
      if (!text || m.message?.is_echo) continue;          // our own replies come back as echoes
      if (!/[\\p{{L}}\\p{{N}}]/u.test(text)) continue;         // a lone emoji or reaction is not worth a post
      const handle = m.sender?.username || m.sender?.id || '';
      const senderId = m.sender?.id || '';
      const inbox = ACCOUNTS[String(m.recipient?.id || entry.id || '')] || {{}};
      const state = $getWorkflowStaticData('global');
      const lead = {{
        source: 'Instagram', name: m.sender?.name || m.sender?.username || 'Instagram user', phone: '', email: '',
        from: '', to: '', date: '',
        details: [['Message', text.slice(0, 900)], ['Instagram', handle],
                  ['Sent to', inbox.handle ? '@' + inbox.handle : '']].filter(([, v]) => v),
        gmailId: `instagram:${{m.message?.mid || entry.id}}`,
      }};
      out.push({{ json: {{
        channel: '{CHANNEL}',
        text: `:camera: *New Instagram message* - ${{lead.name}}${{inbox.handle ? ` (to @${{inbox.handle}})` : ''}}\\n` + templateSlack(lead)
          + (SHOW_RAW ? `\\n\\n:test_tube: _raw:_\\n\\`\\`\\`${{JSON.stringify(body).slice(0, 1500)}}\\`\\`\\`` : ''),
        pushedLead: lead,
        handle, senderId, message: text, inbox: inbox.handle || '',
        igToken: (state.tokens || {{}})[inbox.handle] || inbox.token || '%SEED%',
      }} }});
    }}
  }}
}}
return out;
""".replace('%SEED%', SEED_TOKEN)

SORT = LIB + f"""
// Customer, realtor referral, or influencer? Each goes to a different channel and status.
const msgs = $('Name the sender').all();
return $input.all().map((res, i) => {{
  const j = {{ ...msgs[i].json }};
  if (j.rejected) return {{ json: j }};
  const {{ lead, kind, rules, why, moving, collab }} = triagedLead(j.pushedLead, parseTriage(res.json), j.handle);
  // a card only when there is a move (needsCard); the Slack post and collaboration email still happen
  // an influencer who is not moving still gets a card - on the influencer board, "no moving plans"
  // (client, 2026-11-16: "the influencers are not creating new cards anymore"); no Movegistics lead
  const parked = !needsCard(kind, moving, collab) && kind === 'influencer' && moving === 'no';
  const card = needsCard(kind, moving, collab) || parked;
  if (parked) Object.assign(lead, {{ cardList: '901705958022', cardStatus: 'no moving plans', noMovegistics: true }});
  const raw = j.text.includes(':test_tube:') ? '\\n\\n:test_tube:' + j.text.split(':test_tube:')[1] : '';
  const inbox = j.inbox ? ` (to @${{j.inbox}})` : '';
  // a message that is not a lead gets one line, not the whole template
  const head = `${{IG_HEADLINE[kind]}} - ${{lead.name}}${{inbox}}${{why ? ` - _${{why}}_` : ''}}`
    + (parked ? ' - _not moving now: card added to All Influencer Leads → No Moving Plans_' : '')
    + (kind !== 'other' && !card ? (moving === 'no' ? ' - _no ClickUp card: says they are not moving_'
      : ' - _no ClickUp card yet: no move or collaboration interest mentioned_') : '');
  const body = kind === 'other' ? `> ${{String(j.message).slice(0, 200)}}` : templateSlack(lead);
  return {{ json: {{ ...j, kind, moving, pushedLead: lead, isLead: card,
    // #inbound-leads is for leads only (client, 2026-09-30): anything else goes to the partners channel
    channel: kind === 'other' || rules.channel === 'partners' ? '{PARTNERS}' : '{CHANNEL}',
    text: `${{head}}\\n` + body + raw,
    // someone we have been talking to for days gets one line, not the new-lead template
    shortText: `:speech_balloon: *Instagram message* - ${{lead.name}}${{j.handle ? ` (@${{j.handle}})` : ''}}${{inbox}} - _ongoing conversation_\\n> ${{String(j.message).slice(0, 300)}}` + raw }} }};
}});
"""

ASK = LIB + f"""
// Instagram never gives us an email or phone, so when a lead arrives without them we ask in the DM.
// Never twice in a week, and never for messages that are not leads.
const src = $('Customer, realtor or influencer?').all();
return $input.all().map((conv, i) => {{
  const j = {{ ...src[i].json }};
  const lead = {{ ...j.pushedLead, senderId: j.senderId }};
  const ask = shouldAsk(lead, j.kind, conv.json, Date.now());
  const reply = askForDetails(lead, j.kind);
  const paused = {json.dumps(REPLIES_PAUSED)}.includes(j.inbox);
  const collabTo = collabRecipient(j, conv.json);      // an influencer has given us their email
  const collabMoveKnown = collabTo ? moveTimeKnown(j, conv.json) : false;   // for the circle-back line
  const sendNow = {json.dumps(ASK_LIVE)} && !paused;
  return {{ json: {{ ...j, ask, reply, sendNow, collabTo, collabMoveKnown,
    gmailQuery: collabTo ? `in:sent to:${{collabTo}} subject:"Collaboration Details"` : '',
    replyPreview: `:speech_balloon: *${{sendNow ? 'Replied' : 'Would reply'}} on Instagram* to ${{lead.name}} (@${{j.handle}})`
      + (paused ? ` - _not sent: Meta has paused sending from @${{j.inbox}}, please answer by hand_` : '')
      + ` (${{j.kind}}) - we have ${{lead.phone ? 'their phone' : 'no phone'}}, ${{lead.email ? 'their email' : 'no email'}}\\n`
      + `>${{reply.replace(/\\n/g, '\\n>')}}` }} }};
}});
"""

SLACK_POST = """
// New lead or a conversation that has been going on for days (Mahima Goyal, an existing customer,
// was posted on 10/12 as if she were a new lead)? The chat's last messages tell us.
const j = $('Customer, realtor or influencer?').item.json;
const msgs = (($json.data || [])[0]?.messages?.data) || [];
const times = msgs.map(m => Date.parse(m.created_time)).filter(Boolean);
const ongoing = times.length > 0 && Date.now() - Math.min(...times) > 3 * 86400000;
return { json: { channel: j.channel, text: ongoing && j.shortText ? j.shortText : j.text, ongoing } };
"""

ACCESS_ALERT = """
const who = $('Who sent it').all(), msgs = $('Read the message').all();
const memory = $getWorkflowStaticData('global');
memory.accessAlerted = memory.accessAlerted || {};
const today = new Date().toISOString().slice(0, 10);
const out = [];
who.forEach((w, i) => {
  const err = JSON.stringify(w.json?.error || '');
  if (!/access token|session has been invalidated|OAuthException/i.test(err)) return;
  const inbox = msgs[i]?.json?.inbox || 'an account';
  if (memory.accessAlerted[inbox] === today) return;
  memory.accessAlerted[inbox] = today;
  out.push({ json: { text: `:rotating_light: *Instagram has refused our access for @${inbox}* - new DMs show as "Instagram user" `
    + `and automatic replies do not go out. Someone must generate a new token: Meta developer app → Instagram → `
    + `API setup with Instagram login → Generate token, then send it over.` } });
});
return out;
"""

NAME_SENDER = LIB + """
// Put the real Instagram name and handle on the lead (the webhook only carries an id).
const msgs = $('Read the message').all();
return $input.all().map((who, i) => {
  const j = { ...msgs[i].json };
  const username = who.json?.username || '';
  if (!username) { j.triageBody = triageBody(j.message, j.handle); return { json: j }; }
  const name = String(who.json?.name || username).split('|')[0].trim() || username;
  j.handle = username;
  j.pushedLead = { ...j.pushedLead, name,
    details: j.pushedLead.details.map(([k, v]) => (k === 'Instagram' ? [k, '@' + username] : [k, v])) };
  const raw = j.text.includes(':test_tube:') ? '\\n\\n:test_tube:' + j.text.split(':test_tube:')[1] : '';
  j.text = `:camera: *New Instagram message* - ${name} (@${username})\\n` + templateSlack(j.pushedLead) + raw;
  j.triageBody = triageBody(j.message, username);
  return { json: j };
});
"""

# Instagram tokens last 60 days, so both accounts refresh themselves and the fresh ones are kept
# in this workflow's own storage.
REFRESH_LIST = f"""
const state = $getWorkflowStaticData('global');
const seeds = {json.dumps({v['handle']: v['token'] for v in ACCOUNTS.values()})};
return Object.entries(seeds).map(([handle, seed]) =>
  ({{ json: {{ handle, token: (state.tokens || {{}})[handle] || seed }} }}));
"""

REFRESH = """
const state = $getWorkflowStaticData('global');
state.tokens = state.tokens || {};
const asked = $('Each Instagram account').all();
const out = [];
$input.all().forEach((res, i) => {
  const handle = asked[i].json.handle;
  if (res.json?.access_token) { state.tokens[handle] = res.json.access_token; }
  out.push({ json: { handle, refreshed: !!res.json?.access_token,
                     expiresInDays: Math.round((res.json?.expires_in || 0) / 86400) } });
});
return out;
"""

COLLAB_NEW = """
// Only once per influencer: skip anyone Gmail says already got the collaboration email.
const asked = $('Influencer gave an email?').all(0);
const out = [];
$input.all().forEach((res, i) => {
  const j = asked[i] && asked[i].json;
  if (!j || (res.json?.resultSizeEstimate || 0) > 0) return;
  out.push({ json: { to: j.collabTo, name: j.pushedLead?.name || j.handle, handle: j.handle, inbox: j.inbox,
    subject: COLLAB_SUBJECT, body: collabEmail(!!j.collabMoveKnown) } });
});
return out;
"""

nodes = [
    # the collaboration email: sent the moment an influencer gives us their email (client, 2026-09-30)
    {'name': 'Influencer gave an email?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1440, 520],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'collab', 'leftValue': '={{ !!$json.collabTo }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Sent it before?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1660, 520],
     'parameters': {'method': 'GET', 'url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'gmailOAuth2',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'q', 'value': '={{ $json.gmailQuery }}'}, {'name': 'maxResults', 'value': '1'}]},
                    'options': {}},
     'credentials': CRED_GMAIL, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Not sent yet', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1880, 520],
     'parameters': {'jsCode': (LIB + COLLAB_NEW).strip()}},
    {'name': 'Send the collaboration email', 'type': 'n8n-nodes-base.gmail', 'typeVersion': 2.1, 'position': [2100, 520],
     'parameters': {'resource': 'message', 'operation': 'send',
                    'sendTo': '={{ $json.to }}', 'subject': '={{ $json.subject }}',
                    'emailType': 'text', 'message': '={{ $json.body }}',
                    'options': {'appendAttribution': False}},
     'credentials': CRED_GMAIL, 'onError': 'continueRegularOutput'},
    {'name': 'Tell the team', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [2320, 520],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': PARTNERS, 'mode': 'id'},
                    'text': "={{ ($json.error ? ':warning: Could NOT email the collaboration details to ' : ':incoming_envelope: Collaboration details emailed to ')"
                            " + $('Not sent yet').item.json.name + ' (@' + $('Not sent yet').item.json.handle + ') - '"
                            " + $('Not sent yet').item.json.to + ($json.error ? ' - please send it by hand' : '') }}",
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
    # Meta checks the endpoint with a GET before it will send anything, so both methods are accepted.
    {'name': 'Instagram webhook', 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': [0, 0],
     'webhookId': 'instagram-dm',
     'parameters': {'path': 'instagram-dm', 'multipleMethods': True, 'httpMethod': ['GET', 'POST'],
                    'responseMode': 'responseNode', 'options': {'rawBody': True}}},
    {'name': 'Answer the check', 'type': 'n8n-nodes-base.respondToWebhook', 'typeVersion': 1.1, 'position': [440, -120],
     'parameters': {'respondWith': 'text',
                    'responseBody': '={{ $json.query?.["hub.verify_token"] === "' + VERIFY_TOKEN
                                    + '" ? $json.query?.["hub.challenge"] : "wrong verify token" }}',
                    'options': {}}},
    {'name': 'Say thanks to Meta', 'type': 'n8n-nodes-base.respondToWebhook', 'typeVersion': 1.1, 'position': [440, 120],
     'parameters': {'respondWith': 'text', 'responseBody': 'EVENT_RECEIVED', 'options': {}}},
    {'name': 'Read the message', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [660, 120],
     'parameters': {'jsCode': READ.strip()}},

    {'name': 'Who sent it', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [770, 120],
     'parameters': {'method': 'GET',
                    'url': '=https://graph.instagram.com/v21.0/{{ $json.senderId }}?fields=name,username'
                           '&access_token={{ $json.igToken }}',
                    'options': {}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Name the sender', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [880, 120],
     'parameters': {'jsCode': NAME_SENDER.strip()}},
    {'name': 'Write the Slack post', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1110, 40],
     'parameters': {'mode': 'runOnceForEachItem', 'jsCode': SLACK_POST.strip()}},
    # Instagram refused our token (password change / security reset, 2026-11): say so the same day,
    # once per account per day - from 11/2 to 11/8 it went unnoticed and every DM showed "Instagram user"
    {'name': 'Access refused?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [990, -80],
     'parameters': {'jsCode': ACCESS_ALERT.strip()}},
    {'name': 'Tell us about access', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1210, -80],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': ERRORS, 'mode': 'id'}, 'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [880, 120],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'What kind of message?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [935, 120],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True,
                    'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json',
                    'jsonBody': '={{ JSON.stringify($json.triageBody) }}',
                    'options': {}},
     'credentials': CRED_ANTHROPIC, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Customer, realtor or influencer?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [960, 120],
     'parameters': {'jsCode': SORT.strip()}},
    {'name': 'Have we replied before?', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
     'position': [1000, 300],
     'parameters': {'method': 'GET', 'url': '=https://graph.instagram.com/v21.0/me/conversations'
                    '?user_id={{ $json.senderId }}&fields=messages.limit(10){from,created_time,message}'
                    '&access_token={{ $json.igToken }}',
                    'options': {}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Ask for their details?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1220, 300],
     'parameters': {'jsCode': ASK.strip()}},
    {'name': 'Worth asking?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1440, 300],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'ask', 'leftValue': '={{ $json.ask }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Send it now?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [1550, 240],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'sendNow', 'leftValue': '={{ $json.sendNow }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Send the reply', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1660, 240],
     'parameters': {'method': 'POST', 'url': 'https://graph.instagram.com/v21.0/me/messages',
                    'sendBody': True, 'contentType': 'form-urlencoded',
                    'bodyParameters': {'parameters': [
                        {'name': 'recipient', 'value': '={{ JSON.stringify({ id: $json.senderId }) }}'},
                        {'name': 'message', 'value': '={{ JSON.stringify({ text: $json.reply }) }}'},
                        {'name': 'access_token', 'value': '={{ $json.igToken }}'}]},
                    'options': {}},
     'disabled': not ASK_LIVE, 'onError': 'continueRegularOutput'},
    {'name': 'Show the reply in Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1660, 380],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.replyPreview }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'A real message?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [990, 120],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [
             {'id': 'isLead', 'leftValue': '={{ $json.isLead }}', 'rightValue': '',
              'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}},
             {'id': 'notRejected', 'leftValue': '={{ $json.rejected }}', 'rightValue': '',
              'operator': {'type': 'boolean', 'operation': 'false', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Only the lead', 'type': 'n8n-nodes-base.set', 'typeVersion': 3.4, 'position': [1100, 120],
     'parameters': {'mode': 'raw', 'jsonOutput': '={{ JSON.stringify({ pushedLead: $json.pushedLead }) }}', 'options': {}}},
    {'name': 'Hand over for Movegistics + ClickUp', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2,
     'position': [1320, 120],
     'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': LEAD_SYNC_WF, 'mode': 'id'},
                    'mode': 'once', 'options': {'waitForSubWorkflow': False}},
     'disabled': not LIVE},
]

nodes += [
    {'name': 'Every 15 days', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 380],
     'parameters': {'rule': {'interval': [{'field': 'days', 'daysInterval': 15}]}}},
    {'name': 'Each Instagram account', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [220, 380],
     'parameters': {'jsCode': REFRESH_LIST.strip()}},
    {'name': 'Refresh the token', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [440, 380],
     'parameters': {'method': 'GET',
                    'url': '=https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token'
                           '&access_token={{ $json.token }}',
                    'options': {}},
     'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Keep the new token', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [660, 380],
     'parameters': {'jsCode': REFRESH.strip()}},
]

connections = {
    # output 0 is the GET (Meta's subscription check), output 1 is the POST (a real message)
    'Instagram webhook': {'main': [[{'node': 'Answer the check', 'type': 'main', 'index': 0}],
                                   [{'node': 'Say thanks to Meta', 'type': 'main', 'index': 0}]]},
    'Say thanks to Meta': {'main': [[{'node': 'Read the message', 'type': 'main', 'index': 0}]]},
    'Read the message': {'main': [[{'node': 'Who sent it', 'type': 'main', 'index': 0}]]},
    'Who sent it': {'main': [[{'node': 'Name the sender', 'type': 'main', 'index': 0}]]},
    'Name the sender': {'main': [[{'node': 'What kind of message?', 'type': 'main', 'index': 0},
                                  {'node': 'Access refused?', 'type': 'main', 'index': 0}]]},
    'Access refused?': {'main': [[{'node': 'Tell us about access', 'type': 'main', 'index': 0}]]},
    'What kind of message?': {'main': [[{'node': 'Customer, realtor or influencer?', 'type': 'main', 'index': 0}]]},
    'Customer, realtor or influencer?': {'main': [[{'node': 'A real message?', 'type': 'main', 'index': 0},
                                                   {'node': 'Have we replied before?', 'type': 'main', 'index': 0}]]},
    # the Slack post waits for the chat history: one line for an ongoing conversation
    'Have we replied before?': {'main': [[{'node': 'Ask for their details?', 'type': 'main', 'index': 0},
                                          {'node': 'Write the Slack post', 'type': 'main', 'index': 0}]]},
    'Write the Slack post': {'main': [[{'node': 'Post to Slack', 'type': 'main', 'index': 0}]]},
    'Ask for their details?': {'main': [[{'node': 'Worth asking?', 'type': 'main', 'index': 0},
                                         {'node': 'Influencer gave an email?', 'type': 'main', 'index': 0}]]},
    'Influencer gave an email?': {'main': [[{'node': 'Sent it before?', 'type': 'main', 'index': 0}], []]},
    'Sent it before?': {'main': [[{'node': 'Not sent yet', 'type': 'main', 'index': 0}]]},
    'Not sent yet': {'main': [[{'node': 'Send the collaboration email', 'type': 'main', 'index': 0}]]},
    'Send the collaboration email': {'main': [[{'node': 'Tell the team', 'type': 'main', 'index': 0}]]},
    'Send it now?': {'main': [[{'node': 'Send the reply', 'type': 'main', 'index': 0}], []]},
    'Worth asking?': {'main': [[{'node': 'Send it now?', 'type': 'main', 'index': 0},
                                {'node': 'Show the reply in Slack', 'type': 'main', 'index': 0}], []]},
    'Every 15 days': {'main': [[{'node': 'Each Instagram account', 'type': 'main', 'index': 0}]]},
    'Each Instagram account': {'main': [[{'node': 'Refresh the token', 'type': 'main', 'index': 0}]]},
    'Refresh the token': {'main': [[{'node': 'Keep the new token', 'type': 'main', 'index': 0}]]},
    'A real message?': {'main': [[{'node': 'Only the lead', 'type': 'main', 'index': 0}], []]},
    'Only the lead': {'main': [[{'node': 'Hand over for Movegistics + ClickUp', 'type': 'main', 'index': 0}]]},
}

wf = {'name': '#27 Instagram DMs → leads' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/instagram-dm.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
