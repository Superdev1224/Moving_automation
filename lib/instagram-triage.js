// Instagram DMs are not all the same (client's status rules, 2026-09-25): most are customers, but
// many are realtors passing on a client, or influencers proposing a collaboration. Each belongs in a
// different place, so Claude reads the message and says which it is.

const IG_KINDS = {
  customer:   { status: 'new leads',            tag: '',                  channel: 'leads' },
  realtor:    { status: 'realtors w customers', tag: 'Realtor referral',  channel: 'leads' },
  influencer: { status: 'new leads',            tag: 'Influencer',        channel: 'partners' },
  other:      { status: '',                     tag: '',                  channel: 'partners' },
};

function triageSystem() {
  return [
    'You read Instagram messages sent to a New Jersey moving company (Express Haulers Moving).',
    'Decide what the message is:',
    '"customer" = someone who wants a move, a quote or a price for themselves.',
    '"realtor" = an estate agent or realtor passing on a client who needs to move, or asking on their behalf.',
    '"influencer" = a creator, brand or agency proposing a collaboration, promotion, gifting or paid partnership.',
    '"other" = anything else: a compliment, a job application, a supplier pitch, spam, or a message that asks nothing.',
    'Then decide "moving": is there an actual move for us - the sender\'s own, or a realtor\'s client\'s?',
    '"yes" = they have a move or plan one, at any time ("in November", "end of the year"), they are looking',
    'for a new place, or they ask for a moving quote or price.',
    '"no" = they SAY they are not moving, not moving any time soon, or have no move.',
    '"unclear" = the message does not say either way (a greeting, "what are your rates?", "tell me more",',
    'or talk about a collaboration that never mentions a move).',
    'Many of these messages are replies to the company\'s own collaboration offer to creators: a creator',
    'who answers "I would be interested but I\'m not moving" is "no".',
    'Then decide "collab": "yes" = they say they are interested in collaborating with the company, accept',
    'its collaboration offer, ask about it, or propose one themselves; otherwise "no".',
    'Also pull out the customer name if the message gives one, the move details if they are there,',
    'and any phone number or email address they type in the message.',
    'Answer with JSON only:',
    '{"kind":"customer|realtor|influencer|other","moving":"yes|no|unclear","collab":"yes|no","who":"<name or empty>","why":"<max 8 words>",',
    '"moveDate":"<if stated, else empty>","from":"<origin if stated>","to":"<destination if stated>",'
    + '"phone":"<if they typed one, else empty>","email":"<if they typed one, else empty>"}',
  ].join(' ');
}

function triageBody(text, sender, model = 'claude-opus-5') {
  return { model, max_tokens: 2500, system: triageSystem(),
    messages: [{ role: 'user', content: `Instagram message from ${sender || 'unknown'}:\n\n${String(text).slice(0, 1500)}` }] };
}

function parseTriage(res) {
  const out = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  const m = out.match(/\{[\s\S]*\}/);
  try { return m ? JSON.parse(m[0]) : {}; } catch (e) { return {}; }
}

// Our own accounts must never end up in the customer's name: if the profile lookup gives us back
// the business (or nothing useful), fall back to the name in the message, then the handle.
const OUR_OWN = ['express haulers moving', 'express haulers', 'expresshaulers', 'expresshaulersmovers'];
function customerName(lead, verdict, handle) {
  const fromMessage = String(verdict.who || '').trim();
  if (fromMessage) return fromMessage;
  const profile = String(lead.name || '').trim();
  if (profile && !OUR_OWN.includes(profile.toLowerCase())) return profile;
  return handle ? '@' + handle : 'Instagram user';
}

// Fold what Claude found into the lead, and say where it should be posted.
function triagedLead(lead, verdict, handle) {
  const kind = IG_KINDS[verdict.kind] ? verdict.kind : 'customer';
  const rules = IG_KINDS[kind];
  // a phone or email typed in the message is worth more than anything else we have
  const digits = String(verdict.phone || '').replace(/\D/g, '');
  const out = { ...lead, kind,
    name: customerName(lead, verdict, handle),
    phone: digits.length >= 10 ? digits.slice(-10) : lead.phone,
    email: /.+@.+\..+/.test(String(verdict.email || '')) ? String(verdict.email).trim() : lead.email,
    date: verdict.moveDate || lead.date,
    from: verdict.from || lead.from,
    to: verdict.to || lead.to,
    cardStatus: rules.status,
    tag: rules.tag };
  return { lead: out, kind, rules, why: verdict.why || '', moving: String(verdict.moving || 'unclear').toLowerCase(),
    collab: String(verdict.collab || 'no').toLowerCase() };
}

// Does this message get a ClickUp card? (client, 2026-10-11: "it keeps adding ClickUp cards that are
// not interested from IG - it's not actually a lead".) 19 of the 34 Instagram cards to 10/10 were
// archived, most of them creators answering our collaboration offer with "I'm not moving".
// A card needs a move or a creator who wants to work with us: customers and realtors unless they say
// there is no move; creators when they mention a move OR are interested in collaborating ("or if they
// are interested in collaborating with us", same day) - but never when they say they are not moving,
// since the collaboration is a move filmed for content. Everything still goes to Slack, and creators
// still get the collaboration email.
function needsCard(kind, moving, collab) {
  if (kind === 'other' || moving === 'no') return false;
  if (kind === 'influencer') return moving === 'yes' || collab === 'yes';
  return true;
}

const IG_HEADLINE = {
  customer: ':camera: *New Instagram lead*',
  realtor: ':house_with_garden: *Realtor referral on Instagram*',
  influencer: ':sparkles: *Influencer message on Instagram*',
  other: ':speech_balloon: *Instagram message* (not a lead)',
};

// The reply we send back in the DM. What we ask for depends on who is writing:
// a customer's phone number (a quick call gets the rest), an influencer's email (so the team can
// send collaboration details). Wording to be approved by the client (2026-09-26).
function askForDetails(lead, kind) {
  const first = String(lead.name || '').split(' ')[0].replace(/^@/, '');
  const hi = first && first !== 'Instagram' ? `Hi ${first}!` : 'Hi!';
  if (kind === 'influencer') {
    return `${hi} Thanks for reaching out - we are always happy to hear from creators. `
      + `What is the best email to send our collaboration details to? `
      + `We will send over what we offer and how we usually work together.`;
  }
  // realtors mostly write to pass on a client, so we ask for the client, not for the realtor
  if (kind === 'realtor') {
    return `${hi} Thank you for thinking of Express Haulers Moving - we really appreciate the referral. `
      + `Could you send over your client's name, phone number and moving date? `
      + `We will reach out to them right away and keep you posted.\n\n`
      + `We call from (862) 379-9113.`;
  }
  return `${hi} Thanks for reaching out to Express Haulers Moving - we would love to help with your move. `
    + `What is the best number to reach you on, and a good time to call? `
    + `A quick call is usually all it takes to get you an accurate quote.\n\n`
    + `We call from (862) 379-9113.`;
}

// Ask for the one thing we cannot get any other way: a phone number from a customer, the client's
// details from a realtor,
// an email from an influencer. Never for chit-chat, and never twice in a week.
function shouldAsk(lead, kind, conversation, nowMs = Date.now()) {
  if (kind === 'influencer') {
    if (lead.email) return false;
  } else if (['customer', 'realtor'].includes(kind)) {
    if (lead.phone) return false;
  } else {
    return false;
  }
  const msgs = ((conversation?.data || [])[0]?.messages?.data) || [];
  const ours = msgs.filter(m => String(m.from?.id || '') !== String(lead.senderId || ''));
  return !ours.some(m => nowMs - Date.parse(m.created_time) < 7 * 86400000);
}

if (typeof module !== 'undefined') module.exports = { needsCard, triageBody, customerName, askForDetails, shouldAsk, parseTriage, triagedLead, IG_KINDS, IG_HEADLINE };
