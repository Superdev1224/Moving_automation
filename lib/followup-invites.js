// Customer Follow-Up Scheduler (#29, 2026-11-16) — client PDF "AI Automations", item 2.
// Needs parse-card.js and followups.js loaded first (followUpWhen, findDate, findTime, toEpoch, nyToday, tzOffsetMin).
//
// A card in "Scheduled Follow Ups" with a DAY in its name (or a ClickUp due date) gets a Google Calendar
// invite on contact@'s main calendar, with the customer as a guest:
//   - a time on the card ("Callback 12/01 @11AM") is used as written
//   - no time ("Call Back 11/25"): the first free half hour from 10 AM, last start 6 PM
//   - the time changes on the card: the same invite is moved (the customer gets the update)
//   - one future invite per customer: an invite already on the calendar (ours, or the old AI tool's) is reused
//   - never invites Yelp/Thumbtack relay addresses (they post into the customer's Yelp chat - Jessica Jabr, 10/11)
//   - never books a time that has already passed
// Replaces the client's other AI tool, which did this from 10/6 to 11/15 with those problems.

const FI_DURATION_MIN = 30;
const FI_FIRST_START = { hh: 10, mm: 0 };
const FI_LAST_START = { hh: 18, mm: 0 };
const FI_STEP_MIN = 30;
const FI_CALENDAR = 'contact@expresshaulers.com';
const FI_TEXT = 'We’ll use this time to discuss your move details, confirm the timeline and requirements, answer any questions, and agree on next steps.';

const fiUnescape = s => String(s || '').replace(/\\(?=[^A-Za-z0-9\s])/g, '');
const fiLine = (desc, re) => (fiUnescape(desc).replace(/\*\*/g, '').match(re) || [])[1] || '';

function fiEmail(desc) {
  const line = fiLine(desc, /^[^\S\n]*e-?mail(?:\s+address)?[^\S\n]*[-:–]([^\n]*)$/im);
  const email = (line.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/) || [])[0] || '';
  if (!email) return { email: '', why: 'no email on the card' };
  if (/@(?:[\w-]+\.)*(yelp|thumbtack)\.com$/i.test(email)) return { email: '', why: 'only a Yelp/Thumbtack relay address (would post in their chat)' };
  if (/@expresshaulers\.com$/i.test(email) || /^(no-?reply|mailer)/i.test(email)) return { email: '', why: 'no customer email on the card' };
  return { email: email.toLowerCase(), why: '' };
}

function fiPhone(desc) {
  const digits = fiLine(desc, /^[^\S\n]*phone(?:\s+number)?[^\S\n]*[-:–]([^\n]*)$/im).replace(/\D/g, '');
  const ten = digits.length === 11 && digits[0] === '1' ? digits.slice(1) : digits;
  return ten.length === 10 ? `(${ten.slice(0, 3)}) ${ten.slice(3, 6)}-${ten.slice(6)}` : '';
}

const fiName = task => String(task.name || '').split('|')[0].replace(/\s+/g, ' ').trim() || 'Customer';

// Name inside an event title, for both title styles the old tool used.
function fiEventName(ev) {
  const s = String(ev.summary || '');
  const m = s.match(/^Express Haulers Moving\s*[—-]\s*(.+?)\s+Scheduled Call$/i) || s.match(/^Customer Follow-Up\s*[—-]\s*(.+)$/i);
  return m ? m[1].trim().toLowerCase() : '';
}

const fiStart = ev => Date.parse(ev.start?.dateTime || ev.start?.date + 'T00:00:00');
const fiEnd = ev => Date.parse(ev.end?.dateTime || ev.end?.date + 'T00:00:00');
const fiLocal = ms => {               // epoch ms → "2026-11-25T10:00:00" New York wall clock
  const d = new Date(ms + tzOffsetMin(ms) * 60000);
  return d.toISOString().slice(0, 19);
};
const fiWhen = ms => new Date(ms).toLocaleString('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

// This card's future invite already on the calendar: tagged with the card id, or the same guest email,
// or (no email) the same full name in the title.
function fiExisting(task, email, events, nowMs) {
  const name = fiName(task).toLowerCase();
  const future = events.filter(ev => ev.status !== 'cancelled' && ev.start?.dateTime && fiEnd(ev) > nowMs);
  return future.find(ev => ev.extendedProperties?.private?.clickupId === task.id)
    || (email && future.find(ev => (ev.attendees || []).some(a => String(a.email).toLowerCase() === email)))
    || (name.split(' ').length >= 2 && future.find(ev => fiEventName(ev) === name))
    || null;
}

function fiFreeSlot(date, busy, nowMs) {
  for (let min = FI_FIRST_START.hh * 60 + FI_FIRST_START.mm; min <= FI_LAST_START.hh * 60 + FI_LAST_START.mm; min += FI_STEP_MIN) {
    const start = toEpoch(date.y, date.m, date.d, Math.floor(min / 60), min % 60) * 1000;
    const end = start + FI_DURATION_MIN * 60000;
    if (start < nowMs + 30 * 60000) continue;
    if (!busy.some(b => start < b.end && end > b.start)) return start;
  }
  return null;
}

function fiEventBody(task, email, phone, startMs) {
  const name = fiName(task);
  return {
    summary: `Express Haulers Moving — ${name} Scheduled Call`,
    description: `<p>Customer: ${name}${phone ? `\nPhone: ${phone}` : ''}</p><p>Description: ${FI_TEXT}</p>`,
    start: { dateTime: fiLocal(startMs), timeZone: 'America/New_York' },
    end: { dateTime: fiLocal(startMs + FI_DURATION_MIN * 60000), timeZone: 'America/New_York' },
    attendees: [{ email: FI_CALENDAR, responseStatus: 'accepted' }, ...(email ? [{ email }] : [])],
    extendedProperties: { private: { clickupId: task.id } },
    guestsCanModify: false,
  };
}

// What to do with each card. `seen` = {cardId: signature} of cards already handled (so each is looked at once
// per date/time written on it). Returns actions: create | move | (note-only) skip.
function planInvites(tasks, events, seen, nowMs) {
  const busy = events.filter(ev => ev.status !== 'cancelled' && ev.start?.dateTime && ev.transparency !== 'transparent')
    .map(ev => ({ start: fiStart(ev), end: fiEnd(ev) }));
  const actions = [];
  for (const t of tasks) {
    const w = followUpWhen(t, nowMs);
    if (!w) continue;                                           // no day on the card: nothing to book
    const sig = w.at ? `at:${w.at}` : `day:${w.dayKey}`;
    if (seen[t.id] === sig) continue;
    const desc = t.markdown_description || t.description || '';
    const { email, why } = fiEmail(desc);
    const phone = fiPhone(desc);
    const base = { cardId: t.id, sig, name: fiName(t), url: t.url || `https://app.clickup.com/t/${t.id}`, email, noEmailWhy: why };
    const existing = fiExisting(t, email, events, nowMs);
    const dayStart = toEpoch(w.date.y, w.date.m, w.date.d, 0, 0) * 1000, dayEnd = dayStart + 86400000;
    if (existing) {
      const s = fiStart(existing);
      const fits = w.at ? Math.abs(s - w.at) < 60000 : (s >= dayStart && s < dayEnd);
      if (fits) { actions.push({ ...base, action: 'already', start: s, eventId: existing.id }); continue; }
    }
    let start = w.at, picked = false;
    if (!start) { start = fiFreeSlot(w.date, busy, nowMs); picked = true; }
    if (!start || start < nowMs - 15 * 60000) {
      // the day/time has passed (or no free half hour is left today): nothing to book
      actions.push({ ...base, action: 'skip', reason: start ? 'time already passed' : 'no free time left that day' });
      continue;
    }
    busy.push({ start, end: start + FI_DURATION_MIN * 60000 });
    const body = fiEventBody(t, email, phone, start);
    if (existing) actions.push({ ...base, action: 'move', eventId: existing.id, from: fiStart(existing), start, picked, body });
    else actions.push({ ...base, action: 'create', start, picked, body });
  }
  return actions;
}

function fiNote(a, live) {
  const who = `*${a.name}*`;
  const when = `${fiWhen(a.start)}${a.picked ? ' _(no time on the card: first free slot)_' : ''}`;
  const guest = a.email ? `invite to ${a.email}` : `no invite sent: ${a.noEmailWhy}, calendar only`;
  const verb = a.action === 'move' ? (live ? 'Follow-up moved' : 'Would move follow-up') : (live ? 'Follow-up booked' : 'Would book follow-up');
  const from = a.action === 'move' ? ` (was ${fiWhen(a.from)})` : '';
  return `:calendar: ${verb}: ${who} — ${when}${from} — ${guest} · <${a.url}|ClickUp>`;
}

if (typeof module !== 'undefined') module.exports = { planInvites, fiNote, fiEmail, fiPhone, fiName, fiEventName, fiFreeSlot, fiEventBody };
