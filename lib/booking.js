// Booking status (items #7 and #8).
//   deposit paid (Stripe $52) AND estimate signed  -> ClickUp card to "moves booked"
//   only one of the two, 30 min later              -> ClickUp card to "pending"
// The signed-estimate email only carries the customer name, so Movegistics (isSigned)
// is used to confirm signatures and to fill in contact details.

const DEPOSIT_CENTS = 5200;          // $52 deposit (was $50 until 2026-09-13)
const WAIT_MS = 30 * 60 * 1000;      // grace period before a card goes to "pending"
const EXPIRE_MS = 7 * 24 * 3600e3;   // forget half-finished bookings after a week

const norm = s => (s || '').toLowerCase().normalize('NFKD').replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
const digits = s => (s || '').replace(/\D/g, '').slice(-10);

// Stripe charge.succeeded -> deposit event (null when it isn't a deposit payment)
function depositFromStripe(event, depositCents = DEPOSIT_CENTS) {
  const c = event?.data?.object ?? event;
  if (!c || c.object !== 'charge' || !c.paid || c.amount !== depositCents) return null;
  const bd = c.billing_details || {};
  return {
    kind: 'deposit',
    name: bd.name || '',
    email: (bd.email || c.receipt_email || '').toLowerCase(),
    phone: bd.phone || '',
    customerId: c.customer || null,
    amount: c.amount / 100,
    at: (c.created || Math.floor(Date.now() / 1000)) * 1000,
  };
}

// "Express Haulers Moving Estimate for Julie Permuy Has Been Signed"
function signatureFromEmail(msg) {
  const subject = msg?.subject || '';
  const m = subject.match(/Estimate for (.+?) Has Been Signed/i);
  if (!m) return null;
  return { kind: 'signature', name: m[1].trim(), email: '', phone: '', at: Date.parse(msg.date || '') || Date.now() };
}

// One entry per customer, keyed by name (the only field both sources share).
function recordEvent(state, ev, nowMs = Date.now()) {
  const key = norm(ev.name) || norm(ev.email);
  if (!key) return null;
  // firstSeen is when n8n saw the first event for this customer: the grace period counts
  // from then, so a replayed or delayed event can't instantly park a card in "pending".
  const e = state[key] || (state[key] = { key, name: ev.name, firstSeen: nowMs });
  e[ev.kind] = ev.at;
  if (ev.email) e.email = ev.email;
  if (ev.phone) e.phone = ev.phone;
  if (ev.customerId) e.customerId = ev.customerId;
  e.name = e.name || ev.name;
  return e;
}

// What to do with one customer right now.
//   signedInMovegistics: true/false/null (null = not checked)
// entry.applied holds the status already set ("pending" / "moves booked"), so a customer
// who paid first and signs later still gets upgraded from Pending to Moves Booked.
function decide(entry, nowMs, signedInMovegistics = null) {
  if (entry.applied === 'moves booked') return null;
  const signed = !!entry.signature || signedInMovegistics === true;
  const paid = !!entry.deposit;
  if (signed && paid) return { action: 'book', status: 'moves booked', entry, reason: 'deposit paid and estimate signed' };
  if (entry.applied === 'pending') return null;                        // already parked, nothing new yet
  const waitedFrom = entry.firstSeen || entry.deposit || entry.signature;
  if (nowMs - waitedFrom < WAIT_MS) return null;                       // still inside the grace period
  return {
    action: 'pending', status: 'pending', entry,
    reason: paid ? 'deposit paid, estimate not signed yet' : 'estimate signed, $52 deposit not paid yet',
  };
}

// Customers whose grace period has expired and who still need a Movegistics signature check.
function needsSignatureCheck(state, nowMs) {
  return Object.values(state).filter(e => e.applied !== 'moves booked' && !e.signature && e.deposit
    && nowMs - (e.firstSeen || e.deposit) >= WAIT_MS);
}

function expire(state, nowMs) {
  for (const [k, e] of Object.entries(state)) if (nowMs - (e.firstSeen || 0) > EXPIRE_MS) delete state[k];
}

// Find the customer's ClickUp card. Email is the most reliable, then phone, then name.
function matchCard(tasks, who) {
  const email = (who.email || '').toLowerCase(), phone = digits(who.phone), name = norm(who.name);
  const hay = t => `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '').toLowerCase();
  const pick = (list, how) => {
    if (!list.length) return null;
    const sorted = [...list].sort((a, b) => +(b.date_updated || 0) - +(a.date_updated || 0));
    return { task: sorted[0], how, duplicates: sorted.slice(1) };
  };
  return (email && pick(tasks.filter(t => hay(t).includes(email)), 'email'))
    || (phone.length === 10 && pick(tasks.filter(t => hay(t).replace(/\D/g, '').includes(phone)), 'phone'))
    || (name && pick(tasks.filter(t => norm(t.name).includes(name)), 'name'))
    || null;
}

const SKIP_STATUSES = ['moves complete', 'moves complete not paid', 'moves complete + paid', 'moves lost', 'upsold customers'];

function bookingSlack(action, match) {
  const e = action.entry;
  const who = `${e.name}${e.email ? ` (${e.email})` : ''}`;
  if (!match) return `:mag: *Booking update with no ClickUp card found*: ${who}\n${action.reason} → should go to *${action.status.toUpperCase()}*. Please move the card by hand.`;
  let s = `:card_index_dividers: *${action.status === 'moves booked' ? 'Moves Booked' : 'Pending'}*: ${who}\n${action.reason}\nCard: <${match.task.url}|${match.task.name}> (matched by ${match.how})`;
  if (match.duplicates.length) s += `\n:warning: ${match.duplicates.length} other card(s) match this customer — check for duplicates`;
  return s;
}

if (typeof module !== 'undefined') module.exports = { depositFromStripe, signatureFromEmail, recordEvent, decide, needsSignatureCheck, expire, matchCard, bookingSlack, norm, digits, SKIP_STATUSES, DEPOSIT_CENTS, WAIT_MS };
