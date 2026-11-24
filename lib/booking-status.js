// Booking status from Movegistics (rebuild of #7/#8, 2026-09-25).
//
// The first version remembered "deposit paid" and "signature received" between runs and combined
// them. That memory sits in the workflow's static data, which n8n's Gmail polling trigger overwrites,
// so the two halves stopped meeting and no card has moved since 21 September.
//
// This version keeps no memory. Movegistics already knows both facts for every estimate
// (status Signed, deposit.isPaid), so each run reads them and puts the card where it belongs.
// Doing nothing when the card is already right makes it safe to run as often as we like.

const BOOKED = 'moves booked';
const PENDING = 'pending';
// Judge by when the estimate was SENT, not when Movegistics last touched the record: it re-saves
// old estimates (one from last December showed "updated today"), which made us chase customers
// whose move happened months ago.
const FRESH_DAYS = 14;
// statuses the automation may move a card OUT of; everything else is the team's business
const MAY_MOVE_FROM = ['new leads', 'opportunities', 'scheduled follow ups', 'realtors w customers',
  'quote sent', 'negotiatie/review', 'negotiation review',  // the team's October 2026 statuses (renamed ~11/15)
  'onsite estimate', 'date on hold', 'unresponsive leads', 'pending'];

const digitsOnly = s => String(s || '').replace(/\D/g, '').slice(-10);

// What does Movegistics say this customer's card should look like?
function wantedStatus(e) {
  const signed = String(e.status || '').toLowerCase() === 'signed';
  const paid = !!(e.deposit && e.deposit.isPaid);
  if (signed && paid) return { status: BOOKED, why: 'signed and deposit paid' };
  if (signed) return { status: PENDING, why: 'signed, deposit not paid yet' };
  if (paid) return { status: PENDING, why: 'deposit paid, not signed yet' };
  return null;                    // neither: nothing to say about it
}

function bookingMoves(estimates, tasks, nowMs = Date.now()) {
  const fresh = (estimates || []).filter(e => !e.isDeleted && e.sentAt
    && nowMs - +e.sentAt < FRESH_DAYS * 86400000);
  const byCustomer = new Map();                 // newest estimate per customer wins
  for (const e of fresh) {
    const c = estimateCustomer(e);
    const key = (c.email || '').toLowerCase() || digitsOnly(c.phone) || (c.name || '').toLowerCase();
    if (!key) continue;
    const prev = byCustomer.get(key);
    if (!prev || (+e.updatedAt || 0) > (+prev.e.updatedAt || 0)) byCustomer.set(key, { e, c });
  }

  const moves = [], unmatched = [];
  for (const { e, c } of byCustomer.values()) {
    const want = wantedStatus(e);
    if (!want) continue;
    const match = matchCard(tasks, { email: c.email, phone: c.phone, name: c.name });
    if (!match) {
      if (want.status === BOOKED) unmatched.push({ who: c.name || c.email, estimate: e.displayId, want: want.status });
      continue;
    }
    const now = String(match.task.status?.status || '').toLowerCase();
    if (now === want.status) continue;                       // already right
    if (now === 'moves lost' && want.status === BOOKED) {    // paid after being written off: a person decides
      unmatched.push({ who: `${c.name || c.email} - card is in Moves Lost`, estimate: e.displayId, want: want.status });
      continue;
    }
    if (!MAY_MOVE_FROM.includes(now)) continue;              // booked/complete/lost: leave it alone
    if (now === PENDING && want.status === PENDING) continue;
    moves.push({ id: match.task.id, name: match.task.name, url: match.task.url,
      from: now, to: want.status, why: want.why, estimate: e.displayId,
      total: +e.total || 0, matchedBy: match.how });
  }
  return { moves, unmatched };
}

// A booked influencer goes to the influencer board, status "influencers content wait" (client,
// 2026-11-01: "for the influencers it moves it to moves booked but it should be moved to content wait").
// It passes through Moves Booked first ON PURPOSE: that is what puts the job on the crew's Connecteam
// schedule. A few minutes later, once that has happened, the card is handed over.
const ALL_LEADS_LIST = '901705949004';
const INFLUENCER_BOARD = '901705958022';
const CONTENT_WAIT = 'influencers content wait';
const HANDOFF_AFTER_MS = 3 * 60000;
const isInfluencerCard = t => !/\breferr?(ed|al)\b/i.test(String(t.name || ''))
  && (/\binfluencer\b/i.test(String(t.name || '')) || (t.tags || []).some(g => /influencer/i.test(String(g.name || ''))));
function influencerHandoffs(tasks, nowMs = Date.now()) {
  return (tasks || []).filter(t => String(t.status?.status || '').toLowerCase() === 'moves booked'
    && String(t.list?.id || ALL_LEADS_LIST) === ALL_LEADS_LIST && isInfluencerCard(t)
    && nowMs - +(t.date_updated || 0) > HANDOFF_AFTER_MS)
    .map(t => ({ isHandoff: true, id: t.id, name: t.name, url: t.url, toList: INFLUENCER_BOARD, toStatus: CONTENT_WAIT,
      comment: '📋 Influencer booking: moved to All Influencer Leads → Influencers Content Wait automatically. '
        + 'It went through Moves Booked first so the job is on the Connecteam schedule.' }));
}
const handoffSlack = rows => (rows.length
  ? `:sparkles: *${rows.length} booked influencer${rows.length > 1 ? 's' : ''} moved to Influencers Content Wait*\n`
    + rows.map(r => `• <${r.url}|${String(r.name).trim()}>`).join('\n')
  : '');

const bookingComment = m =>
  `📋 Moved to ${m.to === 'moves booked' ? 'Moves Booked' : 'Pending'} automatically: estimate ${m.estimate} is ${m.why}.`;

function bookingSlackReport(moves, unmatched, live) {
  const lines = [];
  if (moves.length) {
    lines.push(`:card_index_dividers: *${moves.length} card${moves.length > 1 ? 's' : ''} ${live ? 'moved' : 'would move'}*`);
    lines.push(...moves.slice(0, 10).map(m =>
      `• <${m.url}|${String(m.name).slice(0, 44)}> — ${m.from} → *${m.to}* (${m.why})`));
    if (moves.length > 10) lines.push(`• …and ${moves.length - 10} more`);
  }
  if (unmatched.length) {
    lines.push(`:mag: *${unmatched.length} signed and paid, but no card to move*`);
    lines.push(...unmatched.slice(0, 5).map(u => `• ${u.who} (${u.estimate})`));
  }
  return lines.join('\n');
}

if (typeof module !== 'undefined') module.exports = { influencerHandoffs, handoffSlack, isInfluencerCard, bookingMoves, wantedStatus, bookingComment, bookingSlackReport, MAY_MOVE_FROM };
