// Conversation AI (items #1 and #3): rate a customer conversation hot/warm/cold with sales tips
// (Slack #lead-scores) and write a short "where we left off + next steps" note (ClickUp comment).
// Input is a transcribed phone call or a text-message thread.

// Where a customer stands, taken straight from their ClickUp status.
//   lead   = not booked yet: gets a hot/warm/cold score in #lead-scores
//   booked = move booked, not done yet
//   past   = move completed or lost: never a lead; complaints go to #sales-issues-and-damages
const STAGE_GROUPS = {
  booked: ['moves booked', 'upsell in progess', 'upsold customers'],
  // "moves lost" is not here: a lost lead who calls back is a lead again
  past: ['moves complete', 'moves complete not paid', 'moves complete + paid', 'affrim offered'],
};
// The team marks a paid deposit in the card name ("DB 9/30", "DP", "DEPOSIT"). A card can sit in
// Pending with that marker (e.g. "Need to sign again") and is still an existing customer, not a lead.
const DEPOSIT_MARK = /(?:^|[|\s])(DB|DP|DEPOSIT)(?:[|\s]|\d|$)/i;
function stageGroup(status, cardName) {
  const st = (status || '').toLowerCase();
  if (STAGE_GROUPS.past.includes(st)) return 'past';
  if (STAGE_GROUPS.booked.includes(st)) return 'booked';
  if (DEPOSIT_MARK.test(cardName || '')) return 'booked';
  return 'lead';
}

const SYSTEM = [
  'You analyse conversations between a moving company (Express Haulers Moving, New Jersey) and a customer.',
  'Phone-call transcripts are machine-made and may contain mistakes; read past small errors.',
  'FIRST decide who the other person is (contactType). The conversation decides, the CRM stage is a hint:',
  '"new_lead" = asking about a move they have not booked yet (a lost lead coming back counts as new_lead);',
  '"booked_customer" = their move is booked, scheduled or happening now (arrival times, crew on site, gate',
  'codes, damage noticed during the move, confirming details for an upcoming move);',
  '"past_customer" = their move is done (invoice, payment, damage found afterwards, reviews, storage pickup);',
  '"employee" = crew, movers, drivers or office staff talking about jobs, schedules, pay or other customers;',
  '"vendor" = suppliers, truck or storage companies, marketing or sales pitches to us, job seekers;',
  '"realtor" = a real-estate agent talking about a client; "other" = anything else.',
  'Someone the CRM calls a LEAD who talks about their move happening today is a booked_customer.',
  'You are also told where the number stands in the CRM: a LEAD (not booked yet), BOOKED (deposit',
  'paid, move not done yet, even if paperwork is being re-signed) or PAST (move completed).',
  'Rate temperature ONLY when contactType is new_lead or realtor (rate the client they bring):',
  'hot = ready to book now: asked to reserve, agreed to a price, sent a deposit, or picked a date;',
  'warm = interested, engaged, comparing options, waiting on a quote or a date;',
  'cold = not interested, price shopping only, unreachable or wrong number.',
  'For everyone else (booked or past customers, employees, vendors) set temperature to null, and write tips about serving them (confirmations,',
  'COI, timing, resolving problems), never about winning a sale or asking for reviews during a complaint.',
  'substantive = false when nothing real was discussed: voicemail, caller put on hold or transferred, a',
  'wrong number, or a call that ends before the customer explains why they called.',
  'issue = "claim" (damage, missing items, OR a charge the customer says is wrong and wants fixed or refunded),',
  '"billing" (other questions about charges or invoices, e.g. asking for the invoice or how to pay), "complaint"',
  '(service problem) or null. Report an issue only when the customer raises one.',
  'Base everything on what was actually said. Use null when something was not discussed.',
  'BE BRIEF. People read this on a phone between jobs.',
  'If a previous summary is given, it is already on the customer card: write ONLY what is new since then.',
  'Never restate background (who referred them, crew size, original date, stage) unless it changed.',
  'newInfo = false when this conversation adds nothing that the previous summary does not already say',
  '(same date, same price, same open question). With no previous summary, newInfo is true.',
  'Keep every note to 2-3 sentences in total. At most one tip, only if genuinely useful.',
  'nextSteps: the concrete things someone here must DO after this conversation (at most 3, only real ones):',
  'what to send, what to confirm, when to call back. Put any agreed day or time in the step itself',
  '("call back Tue 2pm"). No generic filler, no repeating the summary.',
  'Skip generic advice such as "update the CRM", "confirm in writing" or "follow up".',
  'Also suggest which ClickUp status the card should be in, using the team\'s own rules:',
  'new leads = a brand-new enquiry nobody has worked yet (some are spam or fake).',
  'scheduled follow ups = there is a CONFIRMED date and time to call them back.',
  'opportunities = not new, no callback booked, little interest, price sensitive, going cold.',
  'realtors w customers = the lead came to us through a realtor, early stage.',
  'date on hold = they postponed and will not be moving any time soon.',
  'unresponsive leads = no answer after about 5 calls or 5 texts.',
  'onsite estimate = they want someone to come and estimate in person, not a phone quote.',
  'moves lost = not interested at all and does not want to be followed up with.',
  'Only suggest a status when THIS conversation makes it clear; otherwise null. Never suggest',
  'pending, moves booked, upsell or moves complete: those follow the deposit, signature and move date.',
  'salesRep: the first name of the Express Haulers person who talked or texted with the customer, as they introduced themselves or were called ("this is Shar", "thanks Jane"); null if no name is said.',
  'Reply with JSON only:',
  '{"contactType":"new_lead|booked_customer|past_customer|employee|vendor|realtor|other","temperature":"hot|warm|cold|null","isCustomerConversation":true|false,"substantive":true|false,"newInfo":true|false,"issue":null,"customerName":null,"salesRep":null,',
  '"description":"<max 12 words>",',
  '"tips":["<at most 1, under 12 words>"],',
  '"summary":"<1-2 short sentences, max 30 words, only what is new>",',
  '"nextSteps":["<at most 3, each under 8 words>"],',
  '"suggestedStatus":"<one of the statuses above, or null>","statusWhy":"<max 10 words>",'
  + '"moveDate":"<only if it was set or changed in this conversation, else null>","quotedPrice":null,"objections":null}',
].join(' ');

function claudeBody(conv, model = 'claude-opus-5') {
  const head = conv.kind === 'call'
    ? `Phone call (${conv.direction || '?'}), ${Math.round((conv.duration || 0) / 60)} min, customer number ${conv.customerNumber}`
    : `Text message thread with ${conv.customerNumber}`;
  const group = stageGroup(conv.cardStatus, conv.cardName);
  const id = conv.identity || {};
  const stage = conv.cardStatus
    ? `Customer stage: ${String(conv.cardStatus).toLowerCase() === 'moves lost' ? 'LOST LEAD (marked Moves Lost)' : group.toUpperCase()} (ClickUp status "${conv.cardStatus}", card: ${conv.cardName || '?'})`
    : id.state === 'possible'
      ? `CRM: no card has this number. A card with the same first name exists, "${id.card.name}" (status "${id.card.status}") - it may or may not be them.`
      : 'CRM: no card found for this number or name (a new lead, or a known customer calling from another number)';
  return {
    model,
    max_tokens: 3000,
    system: SYSTEM,
    messages: [{ role: 'user', content: [
      head, `Date: ${conv.startTime || ''}`, stage,
      conv.previous ? `Previous summary (${conv.previous.when}), already on the card:\n${conv.previous.summary}` : 'No previous summary.',
      '', (conv.text || '').slice(0, 40000),
    ].join('\n') }],
  };
}

function parseClaude(res) {
  const txt = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join(' ');
  const m = txt.match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch { return null; }
}

const HEAT = { hot: ':fire: HOT', warm: ':large_orange_circle: WARM', cold: ':snowflake: COLD' };

// Hard cap so a note never runs longer than asked: first N sentences only.
const firstSentences = (text, n) => {
  const parts = String(text || '').replace(/\s+/g, ' ').trim().match(/[^.!?]+[.!?]+(?=\s|$)|[^.!?]+$/g) || [];
  return parts.slice(0, n).map(x => x.trim()).join(' ');
};

// Claude may answer with a string or a list for these fields.
const asText = v => (Array.isArray(v) ? v.filter(Boolean).join('; ') : (v || ''));
const asList = v => (Array.isArray(v) ? v.filter(Boolean) : (v ? [v] : []));

// The company lines (RingCentral, 2026-11-20): which number the customer called or was called/texted from.
const LINES = { '8629304630': '1st line', '2012552545': '2nd line', '8623329066': '3rd line', '8629999390': '4th line', '8623799113': 'Main line' };
function lineLabel(num) {
  const d = String(num || '').replace(/\D/g, '').slice(-10);
  if (d.length !== 10) return '';
  return `${LINES[d] || 'Line'} (${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
}

function conversationSlack(a, conv, card) {
  const who = a.customerName || (card && card.name) || conv.customerNumber;
  const what = conv.kind === 'call'
    ? `${conv.direction === 'Inbound' ? 'Inbound' : 'Outbound'} call, ${Math.round((conv.duration || 0) / 60)} min`
    : 'Text conversation';
  let s = `${HEAT[a.temperature] || ':grey_question:'} — *${who}* (${conv.customerNumber}) · _${what}_\n${firstSentences(a.description, 1)}\n`;
  if (asList(a.tips).length) s += `• ${asList(a.tips)[0]}\n`;
  // 2026-11-20 client: the salesperson and the phone line on every lead score
  const line = lineLabel(conv.ourNumber);
  s += `:bust_in_silhouette: *Sales:* ${a.salesRep || '_name not said_'}${line ? `  ·  :telephone_receiver: *Line:* ${line}` : ''}\n`;
  s += card ? `<${card.url}|Open card> (${card.status})` : '_No ClickUp card for this number._';
  return s;
}

const ISSUE = { claim: ':rotating_light: Possible claim', billing: ':moneybag: Billing question',
                complaint: ':warning: Customer complaint' };

function issueSlack(a, conv, card) {
  const who = a.customerName || (card && card.name) || conv.customerNumber;
  const what = conv.kind === 'call' ? `${conv.direction === 'Inbound' ? 'Inbound' : 'Outbound'} call` : 'Text conversation';
  let s = `${ISSUE[a.issue] || ':warning: Customer issue'} — *${who}* (${conv.customerNumber}) · _${what}_\n${firstSentences(a.summary || a.description, 2)}\n`;
  if (asList(a.nextSteps).length) s += `Next: ${asList(a.nextSteps)[0]}\n`;
  s += card ? `<${card.url}|Open card in ClickUp> (${card.status})` : '_No ClickUp card found for this number._';
  return s;
}

function clickupComment(a, conv) {
  const when = conv.startTime ? new Date(conv.startTime).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  const what = conv.kind === 'call' ? `Call, ${Math.max(1, Math.round((conv.duration || 0) / 60))} min` : 'Texts';
  const label = a.temperature ? a.temperature.toUpperCase() : (a.issue ? `${a.issue} raised` : '');
  const lines = [`${what} · ${when}${label ? ' · ' + label : ''}`, firstSentences(a.summary, 2)];
  const next = asList(a.nextSteps).slice(0, 3);           // the action items (#10), kept to one line
  if (next.length) lines.push(`Next: ${next.join(' · ')}`);
  const suggestion = statusSuggestion(a, conv);           // status suggestion (#3), suggest-only
  if (suggestion) lines.push(suggestion);
  return lines.join('\n');
}

// Card status suggestions (#3). Suggest only: the team moves the card, the automation never does.
// Statuses driven by the deposit, the signature or the move date are left alone completely.
const AI_MAY_SUGGEST = ['new leads', 'scheduled follow ups', 'opportunities', 'realtors w customers',
  'date on hold', 'unresponsive leads', 'onsite estimate', 'moves lost'];
const AI_KEEPS_OUT = ['pending', 'moves booked', 'upsell in progess', 'interested upselling',
  'upsold customers', 'moves complete', 'moves complete not paid', 'moves complete + paid', 'affrim offered'];

// Since 2026-09-30 the AI moves the card itself (client: "the card statuses are not being updated
// automatically"). Same guardrails as the suggestions, and never back to "new leads".
function aiStatusMove(a, conv) {
  const want = String(a.suggestedStatus || '').toLowerCase().trim();
  const now = String(conv.cardStatus || '').toLowerCase().trim();
  if (!want || !AI_MAY_SUGGEST.includes(want) || want === 'new leads') return null;
  if (a.contactType && !['new_lead', 'realtor'].includes(a.contactType)) return null;   // customers and staff: never
  if (!now || AI_KEEPS_OUT.includes(now) || want === now) return null;   // already right, or not ours to touch
  // the team's own instruction on the card wins (Dusan Zivanovic | DONT FOLLOW-UP, 2026-09-30)
  if (/\b(do\s*n[o']?t|dont|no)\s*follow[\s-]*ups?\b/i.test(String(conv.cardName || ''))) return null;
  return want;
}

function statusSuggestion(a, conv) {
  const want = aiStatusMove(a, conv);
  if (!want) return '';
  const why = String(a.statusWhy || '').trim();
  return `🤖 AI moved this card from ${String(conv.cardStatus || '').toUpperCase()} to *${want.toUpperCase()}*`
    + `${why ? ` - ${why}` : ''}. Move it back if that's wrong.`;
}

// Group RingCentral SMS records into one thread per customer number.
function threadsFromMessages(records, ourNumbers) {
  const ours = new Set((ourNumbers || []).map(n => String(n).replace(/\D/g, '')));
  const threads = new Map();
  for (const m of records) {
    const inbound = m.direction === 'Inbound';
    const other = inbound
      ? ((m.from && m.from.phoneNumber) || '')
      : ((m.to && m.to[0] && m.to[0].phoneNumber) || '');
    const digits = String(other).replace(/\D/g, '');
    if (!digits || ours.has(digits)) continue;
    if (!threads.has(other)) threads.set(other, { customerNumber: other, messages: [], hasInbound: false });
    const t = threads.get(other);
    t.ourNumber = (inbound ? (m.to && m.to[0] && m.to[0].phoneNumber) : (m.from && m.from.phoneNumber)) || t.ourNumber || '';
    t.messages.push({ at: m.creationTime, who: inbound ? 'Customer' : 'Express Haulers', text: m.subject || '' });
    if (inbound) t.hasInbound = true;
  }
  for (const t of threads.values()) {
    t.messages.sort((a, b) => String(a.at).localeCompare(String(b.at)));
    t.text = t.messages.map(m => `[${String(m.at).slice(0, 16).replace('T', ' ')}] ${m.who}: ${m.text}`).join('\n');
    t.lastAt = t.messages[t.messages.length - 1].at;
  }
  return [...threads.values()];
}

// ---- Claims raised on a call or in texts (Additional Integrations #12) ----------------------
// The claims workflow already turns claim EMAILS into cards in "Claims Intake". A claim raised on
// the phone or by text lands in the same list, and matches an existing card for that customer so
// one claim never becomes two cards.
function claimCardName(a, conv) {
  const who = a.customerName || conv.cardName?.split('|')[0].trim() || conv.customerNumber;
  return `${who} | ${a.issue === 'claim' ? 'Damage claim' : 'Complaint'} (from ${conv.kind === 'call' ? 'call' : 'text'})`;
}

function claimCard(a, conv) {
  const when = conv.startTime ? new Date(conv.startTime).toLocaleString('en-US', { timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' }) : '';
  const how = conv.kind === 'call' ? `Phone call, ${Math.max(1, Math.round((conv.duration || 0) / 60))} min` : 'Text messages';
  const lines = [
    '## MOVING COMPLAINT INTAKE FORM',
    '**Status:** Under Review',
    '',
    `**Customer:** ${a.customerName || 'Not given on the call'}`,
    `**Phone Number:** ${conv.customerNumber || ''}`,
    `**Reported:** ${when} (${how})`,
    `**Move Date:** ${a.moveDate || ''}`,
    '',
    '**What the customer said**',
    a.summary || a.description || '',
    '',
    '**Items / damage**',
    '',
    '**Estimated value**',
    '',
    '**Next**',
    ...asList(a.nextSteps).slice(0, 3).map(n => `- ${n}`),
    '',
    `_Created automatically from a ${conv.kind === 'call' ? 'phone call' : 'text conversation'}. Please confirm the items and values with the customer._`,
  ];
  return { name: claimCardName(a, conv), markdown_content: lines.join('\n'), status: 'to do' };
}

// An open claim card for this customer already? (claims from email, calls and texts all land here)
function matchClaimCard(tasks, conv, a) {
  const phone = String(conv.customerNumber || '').replace(/\D/g, '').slice(-10);
  const name = (a.customerName || '').toLowerCase().trim();
  const hay = t => `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '').toLowerCase();
  const open = (tasks || []).filter(t => !['closed', 'resolved', 'complete'].includes(String(t.status?.status || '').toLowerCase()))
    .sort((x, y) => +(y.date_updated || 0) - +(x.date_updated || 0));
  return open.find(t => phone && hay(t).replace(/\D/g, '').includes(phone))
    || (name.length > 4 ? open.find(t => hay(t).includes(name)) : null)
    || null;
}

// only real prospects get a hot/warm/cold score (client, 2026-10-02: staff and existing customers were being scored)
const LEAD_CONTACTS = ['new_lead', 'realtor'];
const isLeadContact = a => !a.contactType || LEAD_CONTACTS.includes(a.contactType);

if (typeof module !== 'undefined') module.exports = { lineLabel, isLeadContact, claudeBody, aiStatusMove, statusSuggestion, claimCard, claimCardName, matchClaimCard, parseClaude, conversationSlack, issueSlack, stageGroup, clickupComment, threadsFromMessages, SYSTEM };
