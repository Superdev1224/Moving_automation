#!/usr/bin/env python3
"""Builds workflows/inbound-leads.json (item #1).

New lead email in contact@expresshaulers.com (website form, Yelp quote request,
Thumbtack direct lead) -> message in Slack #inbound-leads.
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PARSER = re.sub(r"\nif \(typeof module.*\n?$", "\n", (ROOT / 'lib/lead-template.js').read_text() + '\n' + (ROOT / 'lib/parse-lead.js').read_text())

TEST_MODE = '--live' not in sys.argv   # test mode posts to #automation-errors instead
CHANNEL = 'C0C2X8B9VS9' if TEST_MODE else 'C0C27RC6QTT'  # #automation-errors / #inbound-leads

CRED_GMAIL = {'gmailOAuth2': {'id': 'fpkyfyuZdxSfWTM8', 'name': 'Gmail - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

# Thumbtack and Yelp leads arrive by webhook since 2026-09-29 (#8b; Yelp through Zapier), with
# the address, date and answers the emails never had - reading their emails too would bring every
# lead in twice
QUERY = ('from:(mailer@shopify.com) '
         '-subject:"Great fit" -in:sent')

PARSE = PARSER + f"""
const CHANNEL = '{CHANNEL}';
const out = [];
for (const item of $input.all()) {{
  const lead = parseLeadEmail(item.json);
  if (lead) out.push({{ json: {{ channel: CHANNEL, text: slackText(lead), source: lead.source, name: lead.name, gmailId: lead.gmailId,
    pushedLead: {{ ...lead, tag: lead.tag || 'Website quote form' }} }} }});
}}
return out;
"""

# The website's quote form (Moving From / Moving To / Moving Date) only EMAILS its submissions: from
# 2026-10-20 to 11-04, 44 of them never reached Slack, ClickUp or Movegistics (Stephen Ingui among them)
# because this workflow had been switched off when the "Get 10% Off" form started posting to us
# directly. It is back, and skips any lead already posted in #inbound-leads in the last 6 hours, so a
# form that both posts and emails is not announced twice. Cards and Movegistics leads come from
# lead-sync (#16), which never makes a second card for the same person.
NOT_POSTED = f"""
const posts = ($('Recent lead posts').first()?.json?.messages || [])
  .filter(m => Date.now() / 1000 - parseFloat(m.ts) < 6 * 3600).map(m => String(m.text || '').toLowerCase());
const digitsOf = s => String(s || '').replace(/\\D/g, '');
return $('Parse lead').all().filter(i => {{
  const l = i.json.pushedLead || {{}};
  const email = String(l.email || '').trim().toLowerCase();
  const phone = digitsOf(l.phone).slice(-10);
  return !posts.some(p => (email && p.includes(email)) || (phone.length === 10 && digitsOf(p).includes(phone)));
}}).map(i => ({{ json: i.json }}));
"""

LEAD_SYNC_WF = (ROOT / 'workflows/lead-sync.id').read_text().strip()

nodes = [
    {'name': 'New email', 'type': 'n8n-nodes-base.gmailTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'pollTimes': {'item': [{'mode': 'everyMinute'}]}, 'simple': False,
                    'filters': {'q': QUERY}, 'options': {}},
     'credentials': CRED_GMAIL},
    {'name': 'Parse lead', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [240, 0],
     'parameters': {'jsCode': PARSE.strip()}},
    {'name': 'Recent lead posts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [460, 0],
     'executeOnce': True,
     'parameters': {'method': 'GET', 'url': f'https://slack.com/api/conversations.history?channel={CHANNEL}&limit=100',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'slackApi', 'options': {}},
     'credentials': CRED_SLACK, 'alwaysOutputData': True, 'onError': 'continueRegularOutput'},
    {'name': 'Not posted yet?', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [680, 0],
     'parameters': {'jsCode': NOT_POSTED.strip()}},
    {'name': 'Post to Slack', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [900, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False}},
     'credentials': CRED_SLACK},
    {'name': 'Only the lead', 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [900, 200],
     'parameters': {'jsCode': "return $input.all().map(i => ({ json: { pushedLead: i.json.pushedLead } }));"}},
    {'name': 'Hand over for Movegistics + ClickUp', 'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2,
     'position': [1120, 200],
     'parameters': {'source': 'database', 'workflowId': {'__rl': True, 'value': LEAD_SYNC_WF, 'mode': 'id'},
                    'mode': 'each', 'options': {'waitForSubWorkflow': False}},
     'disabled': TEST_MODE},
]

connections = {
    'New email': {'main': [[{'node': 'Parse lead', 'type': 'main', 'index': 0}]]},
    'Parse lead': {'main': [[{'node': 'Recent lead posts', 'type': 'main', 'index': 0}]]},
    'Recent lead posts': {'main': [[{'node': 'Not posted yet?', 'type': 'main', 'index': 0}]]},
    'Not posted yet?': {'main': [[{'node': 'Post to Slack', 'type': 'main', 'index': 0},
                                  {'node': 'Only the lead', 'type': 'main', 'index': 0}]]},
    'Only the lead': {'main': [[{'node': 'Hand over for Movegistics + ClickUp', 'type': 'main', 'index': 0}]]},
}

wf = {'name': '#1 Inbound leads → Slack #inbound-leads' + (' [TEST MODE]' if TEST_MODE else ''),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all'}}

out = ROOT / 'workflows/inbound-leads.json'
out.write_text(json.dumps(wf, indent=2))
print(out, 'TEST MODE' if TEST_MODE else 'LIVE')
