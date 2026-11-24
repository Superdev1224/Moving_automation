// "You're booked with us" - the note that goes out when a job is booked (client, 2026-09-29).
//
// Since 2026-10-27 it is driven by Movegistics, not by the ClickUp card: "the source of truth is when
// the Movegistics estimate is signed and paid, or when it is changed but signed and paid". The card's
// date was often stale - Bruce Pugh was told Monday 11/2 from a voided estimate while the estimate he
// signed and paid says Saturday 10/31 - and a card status once counted an onsite estimate as booked.
//
// So: an estimate that is Signed with its deposit paid is a booking. Its date, addresses and email are
// the ones used. A later signed-and-paid estimate for the same work request with a different date
// sends an "updated" note; the same date sends nothing. It never promises an arrival time - the crew
// calls when they are on their way.

const NOTE_TZ = 'America/New_York';
const MIN_DAYS_AHEAD = 2;      // a new booking for today or tomorrow: the day-before confirmation covers it
const MIN_DAYS_UPDATED = 1;    // a changed date is worth telling them up to the day before
const MAX_DAYS_AHEAD = 180;

const noteDate = ms => new Date(ms).toLocaleString('en-US', { timeZone: NOTE_TZ, weekday: 'long', month: 'long', day: 'numeric' });
const shortDate = ms => new Date(ms).toLocaleString('en-US', { timeZone: NOTE_TZ, month: 'short', day: 'numeric' });
const etDay = ms => new Intl.DateTimeFormat('en-CA', { timeZone: NOTE_TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms));
const firstName = full => String(full || '').trim().split(/\s+/)[0] || 'there';
const YELP_RELAY = /@messaging\.yelp\.com$/i;   // posts into their Yelp chat - never a customer email

const NOTE_STATE_LETTERS = { 'new jersey': 'NJ', 'new york': 'NY', pennsylvania: 'PA', connecticut: 'CT',
  delaware: 'DE', maryland: 'MD', massachusetts: 'MA', florida: 'FL' };
function estAddress(list, kind) {
  const a = (list || [])[0] || {};
  const g = f => String(a[kind + f] || '').trim();
  const line = [g('AddressLineOne'), g('AddressLineTwo')].filter(Boolean).join(' ');
  const city = g('City'), st = NOTE_STATE_LETTERS[g('State').toLowerCase()] || g('State'), zip = g('ZipCode');
  const tail = [line.toLowerCase().includes(city.toLowerCase()) ? '' : city, [st, zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  return [line + (g('UnitNumber') ? ` #${g('UnitNumber')}` : ''), tail].filter(Boolean).join(', ');
}

const isSignedAndPaid = e => !e.isDeleted && String(e.status || '') === 'Signed' && !!(e.deposit && e.deposit.isPaid);
const touchedAt = e => Math.max(+e.signedAt || 0, +(e.deposit || {}).paymentDate || 0, +e.updatedAt || 0);

// Which estimates from the list need a closer look: signed and paid, and either new since we started
// watching or changed since we last read them. state = { floor, est: { id: { u, day } }, opp: { id: day } }
function estimatesToCheck(list, state) {
  return (list || []).filter(isSignedAndPaid).filter(e => {
    const seen = state.est[e._id];
    return seen ? (+e.updatedAt || 0) > seen.u : touchedAt(e) > state.floor;
  });
}

// The full estimate -> a note to send, or { skip } saying why not. Updates state as it goes.
function noteFromEstimate(full, listItem, state, nowMs = Date.now()) {
  const e = { ...listItem, ...(full || {}) };
  const tl = (((e.movePricing || {}).transportLabor || {}).data || [])[0] || {};
  const start = +tl.startDate || 0;
  const id = listItem._id, opp = listItem.opportunityId || id;
  state.est[id] = { u: +listItem.updatedAt || 0, day: start ? etDay(start) : '' };
  if (!start) return { skip: 'no move date on the estimate', id, estimate: listItem.displayId };
  const day = etDay(start);
  const before = state.opp[opp];
  if (before === day) return { skip: 'same date as already told', id, estimate: listItem.displayId };
  const updated = !!before;
  const daysOff = (start - nowMs) / 86400000;
  state.opp[opp] = day;                      // recorded even when not sent, so it is said only once
  if (daysOff < (updated ? MIN_DAYS_UPDATED : MIN_DAYS_AHEAD) || daysOff > MAX_DAYS_AHEAD)
    return { skip: daysOff < 0 ? 'move date has passed' : 'move is too soon - the day-before confirmation covers it', id, estimate: listItem.displayId };
  const c = (e.contacts || [])[0] || {};
  const email = String(c.email || '').trim().toLowerCase();
  if (!email || YELP_RELAY.test(email)) return { skip: 'no customer email in Movegistics', id, estimate: listItem.displayId };
  const name = [c.firstName, c.lastName].filter(Boolean).join(' ').trim()
    || String(e.opportunityName || '').replace(/\s*-\s*Work Request$/i, '').trim();
  return { id, opp, estimate: e.displayId, workRequest: e.opportunityDisplayId, updated,
    name: name || 'there', first: firstName(name), email, moveDate: noteDate(start), moveShort: shortDate(start),
    from: estAddress(e.origin, 'origin'), to: estAddress(e.destination, 'destination') };
}

const BOOKED_SUBJECT = m => (m.updated
  ? `Updated: your move with Express Haulers Moving is now ${m.moveDate}`
  : `You're booked with Express Haulers Moving - ${m.moveDate}`);

function bookedEmail(m) {
  const trip = [m.from ? `Moving from: ${m.from}` : '', m.to ? `Moving to: ${m.to}` : ''].filter(Boolean).join('\n');
  const opening = m.updated
    ? `Your move date has been updated. Your move is now scheduled for ${m.moveDate}.`
    : `Thank you for booking with Express Haulers Moving! Your move is scheduled for ${m.moveDate}.`;
  return `Hi ${m.first},

${opening}
${trip ? `\n${trip}\n` : ''}
We will email you the day before your move to confirm, and your crew will call you when they are on their way.

In the meantime, if anything changes - your date, either address, or what is coming with you - just reply to this email or call us at (862) 379-9113.

Thank you for choosing Express Haulers Moving!

Express Haulers Moving
(862) 379-9113
contact@expresshaulers.com`;
}

function bookedSummary(rows, skipped, live) {
  const sent = rows.map(m => `• ${m.updated ? '*Date changed* - ' : ''}${String(m.name).trim()} - moving *${m.moveShort}*`
    + ` - ${m.email} - _${m.estimate}${m.workRequest ? ' / ' + m.workRequest : ''}_`);
  const out = [];
  if (rows.length) out.push(`:incoming_envelope: *Booking note ${live ? 'sent' : 'ready'}* (${rows.length})${live ? '' : ' - _nothing sent yet, checking with you first_'}`, ...sent,
    '_From the Movegistics estimate that was signed and paid - its date, addresses and email._');
  const noEmail = (skipped || []).filter(s => /no customer email/.test(s.skip));
  if (noEmail.length) out.push(`:grey_question: Signed and paid but no customer email in Movegistics, so no note: ${noEmail.map(s => s.estimate || s.id).join(', ')}`);
  return out.join('\n');
}

if (typeof module !== 'undefined') module.exports = { estimatesToCheck, noteFromEstimate, bookedEmail, bookedSummary, BOOKED_SUBJECT, isSignedAndPaid, etDay, estAddress };
