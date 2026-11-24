// Confirmation 24 hours before the move (Additional Integrations #11).
// Wording approved by the client on 2026-09-24, changed by them on 2026-09-29 — do not reword
// without asking them. No arrival time is promised any more: the crew calls the customer when
// they are on their way, which is always right, while a quoted window can be overtaken by the
// schedule. The times below are still worked out, but only for the team's own Slack summary.
// Tomorrow's moves come from the Connecteam schedule: that is the board the crews actually work
// from, and the client asked for it to be the source of truth. No payment or balance is mentioned:
// the client asked for that to be left out.

const NY_TZ = 'America/New_York';   // parse-card.js already defines TZ
const MORNING_WINDOW_H = 1;      // a morning start gets a 1-hour window...
const AFTERNOON_WINDOW_H = 2;    // ...an afternoon start gets 2 hours
const NOTE_DRIFT_MIN = 90;       // note says 8 AM but the shift was moved to 2 PM: trust the shift

const nyParts = ms => new Intl.DateTimeFormat('en-US', { timeZone: NY_TZ, year: 'numeric', month: '2-digit', day: '2-digit' })
  .format(new Date(ms)).split('/');                         // [mm, dd, yyyy]
const nyDay = ms => { const p = nyParts(ms); return `${p[2]}-${p[0]}-${p[1]}`; };
const nyHour = ms => +new Intl.DateTimeFormat('en-US', { timeZone: NY_TZ, hour: 'numeric', hourCycle: 'h23' }).format(new Date(ms));
const nyMinute = ms => +new Intl.DateTimeFormat('en-US', { timeZone: NY_TZ, minute: 'numeric' }).format(new Date(ms));
const clock = ms => new Date(ms).toLocaleString('en-US', { timeZone: NY_TZ, hour: 'numeric', minute: '2-digit' });
const longDate = ms => new Date(ms).toLocaleString('en-US', { timeZone: NY_TZ, weekday: 'long', month: 'long', day: 'numeric' });
const firstNameOf = full => String(full || '').trim().split(/\s+/)[0] || 'there';
const tenOf = s => String(s || '').replace(/\D/g, '').slice(-10);
const ten = s => { const d = tenOf(s); return d.length === 10 ? d : ''; };
const EMAIL_RE = /[\w.+-]+@[\w.-]+\.\w{2,}/;
const PHONE_RE = /\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/;

function arrivalWindow(startMs) {
  if (!startMs) return '';
  const hours = nyHour(startMs) < 12 ? MORNING_WINDOW_H : AFTERNOON_WINDOW_H;
  return `${clock(startMs)} and ${clock(startMs + hours * 3600000)}`;
}

const plain = s => String(s || '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/\*\*/g, '').trim();

// ---- the Connecteam schedule ------------------------------------------------------------------
// Every job is scheduled in Connecteam and the whole card is pasted into the shift note, so the
// schedule sees moves the ClickUp board has not caught up with (6 shifts for 2026-09-29 against 4
// booked cards) and it always has a start time, which the cards often do not.

const NOT_A_MOVE = /\b(cancel|cancell?ed|postponed?|office|training|meeting|holiday|day off)\b/i;

// The note is pasted HTML: the card's own layout survives, so parse-card's field() reads it.
function shiftNotes(shift) {
  return (shift.notes || []).map(n => n.html || n.text || '').join('\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(div|p|li|tr|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;| /gi, ' ')
    .replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&amp;/gi, '&');
}

// The customer is told "we will arrive between X and Y", so every window is written that way.
// The note is typed by hand: "9:00  AM and 10:00 AM", "8:00. AM - 9:00. AM", "8-9am", "11:30am - 1:30pm".
const clockOf = (hh, mm) => `${((hh + 11) % 12) + 1}:${String(mm).padStart(2, '0')} ${hh < 12 ? 'AM' : 'PM'}`;

function cleanWindow(t) {
  const txt = String(t || '').replace(/\s+/g, ' ').replace(/\.\s*(am|pm)/gi, ' $1').trim();
  if (!txt) return null;
  const halves = txt.split(/\s+(?:-|–|—|and|to|until|till)\s+/i);
  const a = findTime(halves[0], true);
  if (!a) return null;
  const b = halves[1] ? findTime(halves[1], true) : null;
  const startMin = a.hh * 60 + a.mm;
  // "8 - 9am": a bare start takes the meridiem of the end, as it does on the cards
  const start = b && b.hh * 60 + b.mm < startMin && a.guessedMeridiem ? { hh: (a.hh + 12) % 24, mm: a.mm } : a;
  const endMin = start.hh * 60 + start.mm + (start.hh < 12 ? MORNING_WINDOW_H : AFTERNOON_WINDOW_H) * 60;
  const end = b && b.hh * 60 + b.mm > start.hh * 60 + start.mm ? b : { hh: Math.floor(endMin / 60) % 24, mm: endMin % 60 };
  return { text: `${clockOf(start.hh, start.mm)} and ${clockOf(end.hh, end.mm)}`, startMin: start.hh * 60 + start.mm };
}

// "300 Riverfront Blvd Elmwood Park, NJ 07407," -> "Elmwood Park": the town, for the Slack line.
const STREET = /\b(ave|avenue|st|street|rd|road|blvd|dr|drive|ln|lane|ct|court|tpke|turnpike|pkwy|parkway|way|pl|place|ter|terrace|hwy|highway|cir|circle)\b\.?/i;
function town(addr) {
  const parts = String(addr || '').replace(/,?\s*USA\s*$/i, '').split(',').map(x => x.trim()).filter(Boolean);
  if (!parts.length) return '';
  let city = parts.length > 1 && /^[A-Za-z]{2}\b|\d{5}/.test(parts[parts.length - 1])
    ? parts[parts.length - 2] : parts[parts.length - 1];
  if (/^\d/.test(city)) {                       // the town is stuck on the end of the street line
    const bits = city.split(STREET);
    city = (bits.length > 1 ? bits[bits.length - 1] : '').trim();
  }
  return city.replace(/^\d+\s*/, '').trim();
}

const tidyAddress = a => String(a || '').replace(/,?\s*USA\s*$/i, '').replace(/[,\s]+$/, '').trim();

// Match a shift back to its ClickUp card, for the link and for anything the note is missing.
function matchCardFor(tasks, who) {
  const hay = t => `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '').toLowerCase();
  const name = String(who.name || '').toLowerCase().trim();
  const pick = list => list.sort((a, b) => +(b.date_updated || 0) - +(a.date_updated || 0))[0] || null;
  return (who.email && pick((tasks || []).filter(t => hay(t).includes(who.email))))
    || (who.phone && pick((tasks || []).filter(t => hay(t).replace(/\D/g, '').includes(who.phone))))
    || (name && pick((tasks || []).filter(t => String(t.name || '').toLowerCase().includes(name))))
    || null;
}

// Every move on the schedule for the given New York day. A shift is repeated once per crew member,
// so identical title + start time is one job. Moves with no way to reach the customer come back
// with a skipReason instead of being dropped silently.
function movesFromShifts(shifts, dayKey, cards = [], nowMs = Date.now()) {
  const seen = new Set();
  const out = [];
  for (const s of shifts || []) {
    if (s.isPublished === false) continue;                   // a draft shift is not a confirmed job
    const title = String(s.title || '').trim();
    if (!title || NOT_A_MOVE.test(title)) continue;
    const startMs = (+s.startTime || 0) * 1000;
    if (!startMs || nyDay(startMs) !== dayKey) continue;
    const key = `${title.toLowerCase()}|${s.startTime}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const text = shiftNotes(s);
    const name = (title.split('|')[0].trim() || plain(field(text, 'Contact Name'))).trim();
    const email = ((field(text, 'Email').match(EMAIL_RE) || text.match(EMAIL_RE) || [''])[0]).toLowerCase();
    const phone = ten(field(text, 'Phone Number')) || ten(field(text, 'Contact Name'))
      || ten((text.match(PHONE_RE) || [''])[0]);
    const card = matchCardFor(cards, { email, phone, name });
    const desc = card ? (card.markdown_description || card.description || '') : '';

    // the note carries the window the customer was promised; the shift carries the time the crew
    // is actually booked for. They normally agree - when they do not, the schedule is the truth.
    const noteWin = cleanWindow(plain(field(text, 'Starting Time')));
    const agrees = noteWin && Math.abs(noteWin.startMin - (nyHour(startMs) * 60 + nyMinute(startMs))) <= NOTE_DRIFT_MIN;
    const window = agrees ? noteWin.text : arrivalWindow(startMs);

    const m = {
      key: card ? card.id : `${name.toLowerCase()}|${dayKey}`,
      id: card ? card.id : s.id, url: card ? card.url : '', shiftId: s.id, startMs,
      name: name || 'there', first: firstNameOf(name),
      email, phone,
      moveDate: longDate(startMs),
      window,
      from: tidyAddress(plain(field(text, 'Moving From')) || plain(field(desc, 'Moving From'))),
      to: tidyAddress(plain(field(text, 'Moving To')) || plain(field(desc, 'Moving To'))),
      movedFromNote: !!noteWin && !agrees,
    };
    // the schedule says when, the board says whether it is really booked - both must agree
    if (!card) m.skipReason = 'no card on the board for this job';
    else if (!isBooked(card)) m.skipReason = `card is still in "${String(card.status?.status || '?')}"`;
    else if (!m.email && !m.phone) m.skipReason = 'no email or phone on the shift';
    m.cardStatus = card ? String(card.status?.status || '') : '';
    out.push(m);
  }
  return out.sort((a, b) => a.startMs - b.startMs);
}

const PRE_MOVE_SUBJECT = m => `Your move with Express Haulers Moving is confirmed for ${m.moveDate}`;

function preMoveEmail(m) {
  // the addresses are only named when we have them: an empty "Moving from:" line looks careless
  const trip = [m.from ? `Moving from: ${m.from}` : '', m.to ? `Moving to: ${m.to}` : ''].filter(Boolean).join('\n');
  return `Hi ${m.first},

Your move is confirmed for ${m.moveDate}! Your crew will call you when they are on their way, so you will know exactly when to expect them.
${trip ? `\n${trip}\n` : ''}
To help everything go smoothly on moving day, please:

• Parking & access: Reserve parking and elevators at both locations if required by your building
• Certificate of Insurance: Let us know in advance if either building requires a COI
• Packing: Have all items you're packing yourself boxed and ready before the crew arrives
• Personal items: Keep valuables, medications, important documents, keys, and other essentials with you
• Appliances: Empty and defrost refrigerators/freezers before moving day and disconnect any appliances that require professional servicing
• Building access: Make sure we have any necessary entry instructions, access codes, loading dock information, or building requirements ahead of time

If anything changes before your move, simply reply to this email or call us at (862) 379-9113.

Thank you for choosing Express Haulers Moving! We look forward to helping make your move as smooth as possible.

Express Haulers Moving
(862) 379-9113
contact@expresshaulers.com`;
}

function preMoveText(m) {
  return `Express Haulers Moving: Your move is confirmed for ${m.moveDate}. Your crew will call you when they are on their way. `
    + `If anything has changed, please call us at (862) 379-9113.

Reply STOP to opt out.`;
}

// Has the team already been in touch since yesterday? Then they have confirmed it themselves.
function confirmedByHand(m, texts, emailCount) {
  if (emailCount > 0) return true;
  return (texts || []).some(t => t.direction === 'Outbound'
    && Date.now() - Date.parse(t.creationTime) < 24 * 3600000
    && (t.to || []).some(x => tenOf(x.phoneNumber) === m.phone));
}

// One short line per move. The team does not need to read four full emails every morning: they
// need to see who is being written to, when the crew arrives, and whether anything looks wrong.
function preMoveSummary(rows, live, skipped = [], handled = []) {
  const head = live
    ? `:calendar: *Move confirmations sent for tomorrow* (${rows.length})`
    : `:calendar: *Move confirmations for tomorrow* (${rows.length}) - _nothing sent yet, checking with you first_`;
  const tight = w => String(w).replace(/:00 (AM|PM) and /, ':00 - ').replace(/ and /, ' - ');
  const lines = rows.map(m => {
    const to = [m.sendEmail ? m.email : '', m.sendText ? m.phone : ''].filter(Boolean).join(' · ') || '*no email or phone*';
    const trip = [town(m.from), town(m.to)].map(x => String(x).trim()).filter(Boolean).join(' → ');
    const who = m.url ? `<${m.url}|${String(m.name).trim()}>` : `*${String(m.name).trim()}*`;
    return `• ${who} - ${tight(m.window)}${trip ? ` - ${trip}` : ''}${m.movedFromNote ? ' _(time taken from the schedule)_' : ''}\n   ${to}`;
  });
  // the team asked what they were meant to do with this post, so the post says so
  const footer = (live
    ? '_Sent automatically. Nothing to do unless something looks wrong._'
    : '_Nothing to do - just tell us if any of these look wrong before we switch sending on._')
    + '\n_Times are from the schedule, for you - the customer is only told their crew will call when they are on the way._';
  // the team asked why people were missing from the list, so nobody is dropped silently
  const already = handled.length
    ? ['', `:speech_balloon: *${handled.length} already contacted by the team in the last 24 h* - left alone:`,
       ...handled.map(m => m.url ? `• <${m.url}|${String(m.name).trim()}> - ${tight(m.window)}` : `• ${String(m.name).trim()} - ${tight(m.window)}`)]
    : [];
  const missing = skipped.length
    ? ['', `:warning: *${skipped.length} on tomorrow's schedule we did not write to*:`,
       ...skipped.map(m => `${m.url ? `• <${m.url}|${String(m.name).trim()}>` : `• *${String(m.name).trim()}*`}`
         + ` - ${tight(m.window)} - _${m.skipReason}_`)]
    : [];
  return [head, ...lines, ...already, ...missing, footer].join('\n');
}

if (typeof module !== 'undefined') module.exports = { movesFromShifts, shiftNotes, cleanWindow, town, matchCardFor, arrivalWindow, preMoveEmail, preMoveText, confirmedByHand, preMoveSummary, PRE_MOVE_SUBJECT, nyDay, firstNameOf };
