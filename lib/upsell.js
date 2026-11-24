// Upsell detection (item #9): an outbound RingCentral text offering extra services
// ("anything else we can take off your plate... packing, supplies, storage, junk removal")
// moves the customer's ClickUp card to "upsell in progess" (ClickUp's own spelling).
//
// Two stages: a cheap keyword filter here, then Claude confirms the candidates, because
// crew dispatch texts contain the job template (with "Junk Removal -" lines) and must not count.

const UPSELL_STATUS = 'upsell in progess';   // NOTE: spelled this way in ClickUp

// Job/dispatch templates sent to crews, never to upsell a customer.
const DISPATCH = /(date of move\s*-|moving from\s*-|starting time\s*-|inventory list|flights of stairs)/i;
const SERVICES = /(packing|unpacking|supplies|storage|junk\s*removal|crating)/i;
const OFFERS = /(take off your plate|we can (also )?(handle|help)|we also (handle|offer)|can also handle|help with packing|anything else (we|i) can|would you like (us )?to (add|handle))/i;

// Cheap first pass: worth asking Claude about?
function isUpsellCandidate(text) {
  const t = (text || '').replace(/\s+/g, ' ');
  if (!t || t.length > 1200) return false;
  if (DISPATCH.test(t)) return false;
  return OFFERS.test(t) && SERVICES.test(t) || /take off your plate/i.test(t);
}

// Obvious enough to skip the AI check entirely.
function isCertainUpsell(text) {
  return /take off your plate/i.test(text || '') && SERVICES.test(text || '');
}

const CLAUDE_SYSTEM =
  'You classify outgoing text messages from a moving company to decide if the message is an UPSELL: ' +
  'an offer of additional paid services (packing, unpacking, supplies, storage, junk removal, crating) ' +
  'to a customer who already has a move booked. ' +
  'It is NOT an upsell if the message is job details sent to a moving crew, a quote for the move itself, ' +
  'a scheduling or payment message, or a reply to a question the customer asked. ' +
  'Answer with JSON only: {"upsell": true|false, "why": "<8 words max>"}';

function claudeBody(text, model = 'claude-opus-5') {
  return {
    model,
    max_tokens: 200,
    system: CLAUDE_SYSTEM,
    messages: [{ role: 'user', content: `Message:\n"""${(text || '').slice(0, 1200)}"""` }],
  };
}

function parseClaude(res) {
  const txt = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join(' ');
  const m = txt.match(/\{[\s\S]*\}/);
  if (!m) return { upsell: false, why: 'no answer from Claude' };
  try { const j = JSON.parse(m[0]); return { upsell: j.upsell === true, why: String(j.why || '') }; }
  catch { return { upsell: false, why: 'unreadable answer' }; }
}

function upsellSlack(msg, card, reason) {
  const who = card ? `<${card.url}|${card.name}>` : msg.to;
  if (!card) return `:speech_balloon: *Upsell sent, no ClickUp card found* for ${msg.to}\n_${reason}_\nPlease move the card to "Upsell In Progess" by hand.`;
  return `:arrow_up: *Upsell In Progess*: ${who}\nUpsell text sent to ${msg.to} — _${reason}_`;
}

if (typeof module !== 'undefined') module.exports = { isUpsellCandidate, isCertainUpsell, claudeBody, parseClaude, upsellSlack, UPSELL_STATUS, DISPATCH, SERVICES, OFFERS };
