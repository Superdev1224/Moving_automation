#!/usr/bin/env python3
"""Builds workflows/lead-webhook.json — leads pushed straight in, no email needed (item #8).

Endpoints (both need the header  X-Lead-Token: <secret>):
  POST /webhook/lead/thumbtack   <- Thumbtack "Manage webhooks"
  POST /webhook/lead/yelp        <- Zapier (Yelp Leads -> Webhooks by Zapier)

In test mode the raw payload is posted to #automation-errors so the exact field names can be
confirmed; in live mode the tidy lead goes to #inbound-leads.

  python3 build/lead-webhook.py          test mode
  python3 build/lead-webhook.py --live   live
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
ERRORS = 'C0C2X8B9VS9'          # #automation-errors
LEADS = 'C0C27RC6QTT'           # #inbound-leads
CHANNEL = LEADS if LIVE else ERRORS

CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}
LEAD_SYNC_WF = (ROOT / 'workflows/lead-sync.id').read_text().strip()   # the Movegistics + ClickUp workflow
CRED_TOKEN = {'httpHeaderAuth': {'id': 'Ngy2vdBsaENomJeL', 'name': 'Lead webhook token'}}

NORMALISE = """
// Thumbtack and Zapier send different shapes, and Thumbtack may change its field names,
// so pick the first field that looks right and keep the raw payload for reference.
const SHOW_RAW = %SHOW_RAW%;
const CHANNEL = '%CHANNEL%';

const pick = (o, names) => {
  const norm = k => k.toLowerCase().replace(/[^a-z]/g, '');
  for (const n of names) {
    for (const key of Object.keys(o || {})) {
      // match the whole key ("customer_name") or just its last part ("location_city" -> "city")
      if (norm(key) === n || norm(key.split('_').pop()) === n) {
        const v = o[key];
        if (v !== null && v !== undefined && v !== '') return typeof v === 'object' ? JSON.stringify(v) : String(v);
      }
    }
  }
  return '';
};
// flatten nested objects so {customer:{name:..}} is searchable too
const flatten = (o, out = {}, prefix = '') => {
  for (const [k, v] of Object.entries(o || {})) {
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, out, prefix + k + '_');
    else out[prefix + k] = v;
  }
  return out;
};

const out = [];
for (const item of $input.all()) {
  const body = item.json.body ?? item.json;
  // which endpoint it arrived on tells us the platform
  const source = `${item.json.webhookUrl || ''} ${item.json.params?.source || ''} ${body.source || ''}`.toLowerCase();
  const flat = flatten(body);
  const lead = {
    source: source.includes('thumb') ? 'Thumbtack' : source.includes('yelp') ? 'Yelp'
      : source.includes('website') ? 'Website' : 'Webhook',
    name: pick(flat, ['customername', 'name', 'customerfullname', 'firstname', 'consumername', 'fullname']),
    phone: pick(flat, ['phone', 'phonenumber', 'customerphone', 'consumerphone', 'mobile']),
    email: pick(flat, ['email', 'emailaddress', 'customeremail', 'consumeremail']),
    service: pick(flat, ['servicename', 'service', 'category', 'jobtype', 'requesttitle', 'title']),
    location: pick(flat, ['movingfrom', 'origin', 'location', 'city', 'zipcode', 'zip', 'address', 'servicearea']),
    movingTo: pick(flat, ['movingto', 'destination', 'to']),
    date: pick(flat, ['movedate', 'movingdate', 'date', 'startdate', 'requestdate', 'preferreddate', 'schedule']),
    details: pick(flat, ['details', 'description', 'message', 'note', 'notes', 'requestdetails']),
    leadId: pick(flat, ['leadid', 'id', 'requestid', 'conversationid']),
  };

  // Thumbtack's own format (V4, confirmed with their test lead on 2026-09-29): read it field by
  // field instead of guessing - the guesser took the business name for the customer's.
  const tt = body.data && body.data.customer && body.event && body.event.eventType ? body.data : null;
  if (tt) {
    const c = tt.customer || {}, r = tt.request || {}, loc = r.location || {};
    const qa = (r.details || []).filter(d => d && d.question && d.answer).map(d => [String(d.question), String(d.answer)]);
    const ans = re => (qa.find(([q]) => re.test(q)) || [])[1] || '';
    const start = ((r.proposedTimes || [])[0] || {}).start;
    lead.name = [c.firstName, c.lastName].filter(Boolean).join(' ').trim();
    lead.phone = String(c.phone || '');
    lead.email = String(c.email || '');
    lead.service = (r.category || {}).name || '';
    lead.location = [[loc.address1, loc.address2].filter(Boolean).join(' '), loc.city,
      [loc.state, loc.zipCode].filter(Boolean).join(' ')].filter(Boolean).join(', ');
    lead.movingTo = ans(/moving to|destination|new (address|home)|drop.?off|where.*(to|going)/i);
    lead.date = start ? new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(start))
      : ans(/date|when/i);
    lead.details = [r.description, ...qa.map(([q, a]) => `${q}: ${a}`),
      tt.estimate && tt.estimate.total ? `Thumbtack estimate: ${tt.estimate.total}` : ''].filter(Boolean).join(' · ');
    lead.leadId = String(tt.negotiationID || r.requestID || '');
  }

  // Yelp through Zapier (confirmed with a real lead on 2026-09-29): the answers come as one
  // "Question: ... / Answer: ..." block, and "date" is when the lead arrived, not the move date.
  if (!tt && body.lead_id && (body.lead_name || body.project_summary)) {
    const qa = [...String(body.project_summary || '').matchAll(/Question:\\s*(.+?)\\s*\\nAnswer:[ \\t]*(.*)/g)]
      .map(m => [m[1].trim(), m[2].trim()]).filter(([, a]) => a);
    const ans = re => (qa.find(([q]) => re.test(q)) || [])[1] || '';
    const zip = String(body.postal_code || ans(/location postcode|zip/i) || '');
    lead.name = String(body.lead_name || '');
    lead.phone = String(body.phone || '');
    // Yelp gives a relay address, not the customer's email: anything sent to it is posted in their Yelp
    // chat (a calendar invite for Jessica Jabr's follow-up showed up there, 2026-10-11). It stays off the
    // card and out of Movegistics; only the chat id is kept, for our own automatic first reply.
    const relay = String(body.email || '').trim().match(/^leadsapi\\+([0-9a-f]+)@messaging\\.yelp\\.com$/i);
    lead.email = relay ? '' : String(body.email || '');
    lead.yelpChat = relay ? relay[1].toLowerCase() : '';
    lead.service = ans(/kind of service/i);
    lead.location = zip;
    lead.movingTo = ans(/where are you moving to/i);
    lead.date = (ans(/availability dates/i).match(/\\d{4}-\\d{2}-\\d{2}/) || [''])[0] || ans(/availability dates/i);
    lead.details = [ans(/additional information/i), body.yelp_link ? `Yelp: ${body.yelp_link}` : ''].filter(Boolean).join(' · ');
    lead.leadId = String(body.lead_id);
    lead.extra = [['Size', ans(/size of your move/i)], ['Stairs (pickup)', ans(/floors at your current/i)],
      ['Stairs (drop-off)', ans(/floors at your destination/i)], ['Packing / prep', ans(/assistance preparing/i)]];
  }

  // Website forms posted by the team's own zaps (Helium forms, TikTok, referrals - 2026-10-01): they
  // already post to #inbound-leads themselves, so they can ask us to skip our Slack post, and they
  // say which form the lead came from.
  const zapSource = String(body.lead_source || '').trim().slice(0, 40);
  const skipSlack = /^(yes|true|1)$/i.test(String(body.skip_slack || ''));
  // The website path takes its fields by name: the guesser above would read "referrer_name" as the
  // customer's name and "call_date" as the move date. Every card says which form it came from.
  if (lead.source === 'Website' && !tt) {
    const s = k => String(body[k] ?? '').trim();
    const form = zapSource || 'Get 10% Off Form';                  // no form named = the PageFly form
    lead.source = /tiktok/i.test(form) ? 'TikTok' : 'Website';
    lead.tag = form.toLowerCase() === 'tiktok' ? '' : form;
    lead.name = s('name'); lead.phone = s('phone'); lead.email = s('email');
    lead.location = s('moving_from'); lead.movingTo = s('moving_to'); lead.date = s('move_date');
    const notes = [];
    if (s('referrer_name') || s('referrer_phone')) {
      notes.push(`Referred by ${[s('referrer_name'), s('referrer_phone'), s('referrer_email')].filter(Boolean).join(', ')}`);
    }
    if (s('call_date') || s('call_time')) {
      notes.push(`Wants a call ${[s('call_date'), s('call_time')].filter(Boolean).join(' at ')}`);
      const d = s('call_date').match(/(\\d{4})-(\\d{2})-(\\d{2})/) || s('call_date').match(/^(\\d{1,2})\\/(\\d{1,2})/);
      const when = d ? (d.length === 4 ? `${+d[2]}/${+d[3]}` : `${+d[1]}/${+d[2]}`) : s('call_date');
      lead.tag = `${form} | Callback ${when}${s('call_time') ? ' @' + s('call_time').replace(/\\s+/g, '') : ''}`;
      lead.cardStatus = 'scheduled follow ups';
    }
    lead.details = [s('message'), ...notes].filter(Boolean).join(' · ');
    lead.template = {
      fromType: s('from_residence_type'), fromApt: s('from_apt'), fromStairs: s('from_stairs'),
      fromDistance: s('from_loading_distance'), fromAccess: s('from_access'),
      toType: s('to_residence_type'), toApt: s('to_apt'), toStairs: s('to_stairs'),
      toDistance: s('to_unloading_distance'), toAccess: s('to_access'),
      inventory: s('inventory'), packing: s('packing'), supplies: s('supplies'), rental: s('rental'),
      junk: s('junk'), storage: s('storage'),
      source: s('heard_about_us') || (s('referrer_name') ? `Referral: ${s('referrer_name')}` : ''),
    };
  }

  // Thumbtack can also send message and review notifications: those are not new leads.
  const kind = String((body.event && body.event.eventType) || body.type || body.eventType || body.webhookType
    || body.topic || (typeof body.event === 'string' ? body.event : '')).toLowerCase();
  const trap = String(body.website_url || body.company_website || '').trim();     // honeypot
  const isLead = !trap && !/review|message|reply/.test(kind) && !!(lead.name || lead.phone || lead.email || lead.yelpChat);
  const iso = (lead.date || '').match(/^\\d{4}-\\d{2}-\\d{2}/) ? lead.date.slice(0, 10) : lead.date;
  const pushedLead = { source: lead.source, name: lead.name, phone: lead.phone, email: lead.email,
    from: lead.location, to: lead.movingTo || '', date: iso,
    details: [['Service', lead.service], ['Details', lead.details], ...(lead.extra || [])].filter(([, v]) => v),
    gmailId: lead.leadId ? `${lead.source.toLowerCase()}:${lead.leadId}` : '',
    ...(lead.tag ? { tag: lead.tag } : {}), ...(lead.template ? { template: lead.template } : {}),
    ...(lead.cardStatus ? { cardStatus: lead.cardStatus } : {}), ...(lead.yelpChat ? { yelpChat: lead.yelpChat } : {}) };
  const icon = lead.source === 'Thumbtack' ? ':large_blue_circle:' : lead.source === 'Yelp' ? ':red_circle:'
    : lead.source === 'Website' ? ':globe_with_meridians:' : ':bell:';
  let text = `${icon} *New ${lead.source} lead*${lead.tag ? ' · ' + lead.tag : ''} — ${lead.name || 'Name not given'}\\n` + templateSlack(pushedLead)
    + `\\n_Sent straight from ${lead.source}, no email involved._`;
  if (SHOW_RAW) text += `\\n\\n:test_tube: _raw payload for setup:_\\n\\`\\`\\`${JSON.stringify(body).slice(0, 2500)}\\`\\`\\``;
  // live: nothing that is not a lead reaches #inbound-leads (an empty Zapier test once did)
  if (!SHOW_RAW && !isLead) continue;
  out.push({ json: { channel: CHANNEL, text, lead, isLead, kind, pushedLead, skipSlack } });
}
return out;
"""

def webhook(name, path, pos):
    return {'name': name, 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': pos,
            'webhookId': path.replace('/', '-'),
            'parameters': {'path': path, 'httpMethod': 'POST', 'responseMode': 'onReceived',
                           'authentication': 'headerAuth', 'options': {}},
            'credentials': CRED_TOKEN}

# Some platforms cannot add a custom header, so the same endpoints also exist with the
# secret in the URL itself. Treat these URLs as passwords.
SECRET_PATH = '314290742d8d324e7e89'

def open_webhook(name, path, pos):
    return {'name': name, 'type': 'n8n-nodes-base.webhook', 'typeVersion': 2, 'position': pos,
            'webhookId': path.replace('/', '-'),
            'parameters': {'path': path, 'httpMethod': 'POST', 'responseMode': 'onReceived', 'options': {}}}

nodes = [
    # the website form posts straight here, so the Shopify notification email is no longer needed
    open_webhook('Website form', f'lead/website/{SECRET_PATH}', [0, -300]),
    webhook('Thumbtack lead', 'lead/thumbtack', [0, -160]),
    webhook('Yelp lead (via Zapier)', 'lead/yelp', [0, -20]),
    open_webhook('Thumbtack lead (secret URL)', f'lead/thumbtack/{SECRET_PATH}', [0, 120]),
    open_webhook('Yelp lead (secret URL)', f'lead/yelp/{SECRET_PATH}', [0, 260]),
    {'name': 'Tidy up the lead', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [260, 0],
     'parameters': {'jsCode': (ROOT / 'lib/lead-template.js').read_text().split("if (typeof module")[0] + NORMALISE.replace('%CHANNEL%', CHANNEL)
                    .replace('%SHOW_RAW%', 'false' if LIVE else 'true').strip()}},
    # a zap that already posts to #inbound-leads itself sends skip_slack=yes: no second post
    {'name': 'Post it?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [400, 0],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'post', 'leftValue': '={{ !$json.skipSlack }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'New lead?', 'type': 'n8n-nodes-base.if', 'typeVersion': 2.2, 'position': [520, 200],
     'parameters': {'conditions': {
         'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'loose', 'version': 2},
         'combinator': 'and',
         'conditions': [{'id': 'isLead', 'leftValue': '={{ $json.isLead }}', 'rightValue': '',
                         'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]},
         'options': {}}},
    {'name': 'Hand over for Movegistics + ClickUp', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2,
     'position': [760, 200],
     'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': LEAD_SYNC_WF, 'mode': 'id'},
                    'mode': 'once', 'options': {'waitForSubWorkflow': False}},
     'disabled': not LIVE},
    {'name': 'Only the lead', 'type': 'n8n-nodes-base.set', 'typeVersion': 3.4, 'position': [640, 320],
     'parameters': {'mode': 'raw', 'jsonOutput': '={{ JSON.stringify({ pushedLead: $json.pushedLead }) }}', 'options': {}}},
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [520, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False}},
     'credentials': CRED_SLACK},
]

one = lambda n: [{'node': n, 'type': 'main', 'index': 0}]
connections = {
    'Website form': {'main': [one('Tidy up the lead')]},
    'Thumbtack lead': {'main': [one('Tidy up the lead')]},
    'Yelp lead (via Zapier)': {'main': [one('Tidy up the lead')]},
    'Thumbtack lead (secret URL)': {'main': [one('Tidy up the lead')]},
    'Yelp lead (secret URL)': {'main': [one('Tidy up the lead')]},
    'Tidy up the lead': {'main': [[{'node': 'Post it?', 'type': 'main', 'index': 0},
                                   {'node': 'New lead?', 'type': 'main', 'index': 0}]]},
    'New lead?': {'main': [one('Only the lead'), []]},
    'Post it?': {'main': [one('Post to Slack'), []]},
    'Only the lead': {'main': [one('Hand over for Movegistics + ClickUp')]},
}

wf = {'name': '#8b Leads pushed straight in (Thumbtack + Yelp via Zapier)' + ('' if LIVE else ' [TEST MODE]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/lead-webhook.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'LIVE' if LIVE else 'TEST MODE')
