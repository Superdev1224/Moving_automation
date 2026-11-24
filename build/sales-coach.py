#!/usr/bin/env python3
"""Builds workflows/sales-coach.json — #31 Sales call coaching (2026-11-16). Rules in lib/sales-coach.js.

Every 10 minutes: new calls in the call_transcripts table (2+ minutes) that have a hot/warm/cold post in
#lead-scores (i.e. the AI already decided it is a sales conversation with a lead or realtor) get coaching
against the company's sales script and objection playbook, as a reply in that post's thread.
Separate from #1/#3 on purpose: the live scoring workflow is not touched.

  python3 build/sales-coach.py          preview: the coaching goes to #automation-errors (with a link to the lead post)
  python3 build/sales-coach.py --live   live: reply in the #lead-scores thread
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = '--live' in sys.argv
SCORES = 'C0C343MQBLH'                 # #lead-scores
ERRORS = 'C0C2X8B9VS9'
TABLE = (ROOT / 'workflows/call-transcripts.table').read_text().strip()
CRED_N8N = {'httpHeaderAuth': {'id': 'Bc7JADy3VUTvng5V', 'name': 'n8n API (self)'}}
CRED_ANTHROPIC = {'anthropicApi': {'id': 'IGrpfmtwR5bpYErA', 'name': 'Anthropic - Express Haulers'}}
CRED_SLACK = {'slackApi': {'id': '95y179q3TMPitM0m', 'name': 'Slack - Express Haulers'}}

strip = lambda js: re.sub(r"\nif \(typeof module !== 'undefined'\).*", '', js)
LIB = strip((ROOT / 'lib/sales-coach.js').read_text())

WINDOW = """
// the last 3 hours of stored calls (each call is coached once; the lead post can take a few minutes)
const from = new Date(Date.now() - 3 * 3600e3).toISOString();
return [{ json: { filter: JSON.stringify({ type: 'and', filters: [{ columnName: 'startTime', condition: 'gte', value: from }] }),
  oldest: Math.floor(Date.now() / 1000) - 4 * 3600 } }];
"""

PICK = LIB + f"""
// Calls to coach: long enough, not done yet, and with a lead-score post (= a sales conversation).
const state = $getWorkflowStaticData('global');
state.done = state.done || {{}};
const posts = ($('Lead posts').first().json.messages || []).filter(m => m.bot_id || m.app_id);
const out = [];
for (const c of $('Stored calls').first().json.data || []) {{
  if (state.done[c.callId] || (+c.duration || 0) < COACH_MIN_SECONDS) continue;
  const inbound = c.direction === 'Inbound';
  const call = {{ callId: c.callId, startTime: c.startTime, duration: +c.duration, direction: c.direction,
    customerNumber: inbound ? c.fromNumber : c.toNumber, customer: inbound ? c.fromName : c.toName,
    rep: inbound ? c.toName : c.fromName, transcript: c.transcript,
    when: new Date(c.startTime).toLocaleString('en-US', {{ timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' }}) }};
  const post = leadPostFor(call, posts);
  if (!post) {{
    // no lead post 40 minutes after the call: not a sales call (staff, customer logistics...) - forget it
    if (Date.now() - Date.parse(c.startTime) > ((+c.duration || 0) + 2400) * 1000) state.done[c.callId] = Date.now();
    continue;
  }}
  state.done[c.callId] = Date.now();
  out.push({{ json: {{ ...call, postTs: post.ts, body: coachBody(call) }} }});
}}
for (const [k, v] of Object.entries(state.done)) if (Date.now() - v > 2 * 86400e3) delete state.done[k];
return out.slice(0, 6);
"""

WRITE = LIB + f"""
const LIVE = {json.dumps(LIVE)};
const calls = $('Calls to coach').all();
return $input.all().map((r, i) => {{
  const c = calls[i].json, text = coachSlack(parseCoach(r.json));
  if (!text) return null;
  if (LIVE) return {{ json: {{ channel: '{SCORES}', thread: c.postTs, text }} }};
  const link = `https://expresshaulersmoving.slack.com/archives/{SCORES}/p${{String(c.postTs).replace('.', '')}}`;
  return {{ json: {{ channel: '{ERRORS}', thread: '', text: `:test_tube: _Preview - would reply in <${{link}}|this #lead-scores thread>_ (${{c.direction}} call ${{c.when}})\\n` + text }} }};
}}).filter(Boolean);
"""


def code(name, js, pos):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': pos, 'parameters': {'jsCode': js.strip()}}


nodes = [
    {'name': 'Every 10 minutes', 'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2, 'position': [0, 0],
     'parameters': {'rule': {'interval': [{'field': 'minutes', 'minutesInterval': 10}]}}},
    code('Window', WINDOW, [200, 0]),
    {'name': 'Stored calls', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [400, 0],
     'parameters': {'method': 'GET', 'url': f'https://n8n.expresshaulers.com/api/v1/data-tables/{TABLE}/rows',
                    'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'filter', 'value': '={{ $json.filter }}'}, {'name': 'limit', 'value': '250'}]},
                    'options': {}},
     'credentials': CRED_N8N, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 5000},
    {'name': 'Lead posts', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [600, 0],
     'parameters': {'method': 'GET', 'url': 'https://slack.com/api/conversations.history',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'slackApi',
                    'sendQuery': True, 'queryParameters': {'parameters': [
                        {'name': 'channel', 'value': SCORES}, {'name': 'oldest', 'value': "={{ $('Window').first().json.oldest }}"},
                        {'name': 'limit', 'value': '300'}]},
                    'options': {}},
     'credentials': CRED_SLACK, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 5000},
    code('Calls to coach', PICK, [800, 0]),
    {'name': 'Coach', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2, 'position': [1000, 0],
     'parameters': {'method': 'POST', 'url': 'https://api.anthropic.com/v1/messages',
                    'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'anthropicApi',
                    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'anthropic-version', 'value': '2023-06-01'}]},
                    'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.body) }}',
                    'options': {'timeout': 180000}},
     'credentials': CRED_ANTHROPIC, 'retryOnFail': True, 'maxTries': 2, 'waitBetweenTries': 20000,
     'onError': 'continueRegularOutput'},
    code('Write it', WRITE, [1200, 0]),
    {'name': 'Post', 'type': 'n8n-nodes-base.slack', 'typeVersion': 2.3, 'position': [1400, 0],
     'parameters': {'authentication': 'accessToken', 'resource': 'message', 'operation': 'post', 'select': 'channel',
                    'channelId': {'__rl': True, 'value': '={{ $json.channel }}', 'mode': 'id'},
                    'text': '={{ $json.text }}',
                    'otherOptions': {'includeLinkToWorkflow': False, 'unfurl_links': False, 'unfurl_media': False,
                                     **({'thread_ts': {'replyValues': {'thread_ts': '={{ $json.thread }}'}}} if LIVE else {})}},
     'credentials': CRED_SLACK},
]
order = ['Every 10 minutes', 'Window', 'Stored calls', 'Lead posts', 'Calls to coach', 'Coach', 'Write it', 'Post']
connections = {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(order, order[1:])}
wf = {'name': '#31 Sales call coaching' + ('' if LIVE else ' [PREVIEW - #automation-errors]'),
      'nodes': nodes, 'connections': connections,
      'settings': {'executionOrder': 'v1', 'timezone': 'America/New_York',
                   'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all'}}
(ROOT / 'workflows/sales-coach.json').write_text(json.dumps(wf, indent=2))
print(ROOT / 'workflows/sales-coach.json', 'LIVE' if LIVE else 'PREVIEW')
