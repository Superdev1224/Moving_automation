// Parses a ClickUp "All Leads" card into a Connecteam shift.
// Dates/times are free text: move cards keep them in the description
// ("Date of Move - 9/22", "Starting Time - 7:00 am - 8:00 am"); follow-up and
// onsite cards keep them in the name ("Callback 9/19 @10AM", "8/25 between 1 and 2 PM").
// Pure JS (no luxon) so the same code runs in an n8n Code node and in tests.

const TZ = 'America/New_York';

// Offset in minutes of TZ at a given UTC instant (handles DST).
function tzOffsetMin(utcMs) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(utcMs)).reduce((o, p) => (o[p.type] = p.value, o), {});
  const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
  return (asUtc - utcMs) / 60000;
}

// Local wall-clock time in TZ → epoch seconds.
function toEpoch(y, m, d, hh, mm) {
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  const off = tzOffsetMin(guess - tzOffsetMin(guess) * 60000);
  return Math.round((guess - off * 60000) / 1000);
}

function nyToday(nowMs) {
  const [m, d, y] = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric', month: 'numeric', day: 'numeric' })
    .format(new Date(nowMs)).split('/').map(Number);
  return { y, m, d };
}

// "9/22", "09/22/26", "10/1/2026" → {m,d,y}. Without a year, pick the next occurrence
// (dates up to 30 days in the past stay in the current year — cards get moved late).
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

function findDate(text, nowMs) {
  const numeric = /(?<![\d/])(1[0-2]|0?[1-9])\s*\/\s*(3[01]|[12]\d|0?[1-9])(?:\s*\/\s*(\d{4}|\d{2}))?(?![\d/])/g;
  const named = /\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(3[01]|[12]\d|0?[1-9])(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?/gi;
  const all = [
    ...[...text.matchAll(numeric)].map(mt => ({ index: mt.index, m: +mt[1], d: +mt[2], y: mt[3], raw: mt[0] })),
    ...[...text.matchAll(named)].map(mt => ({ index: mt.index, m: MONTHS.indexOf(mt[1].toLowerCase().slice(0, 3)) + 1, d: +mt[2], y: mt[3], raw: mt[0] })),
  ].sort((a, b) => a.index - b.index);
  if (!all.length) return null;
  const mt = all[all.length - 1];
  const m = mt.m, d = mt.d;
  let y;
  if (mt.y) y = mt.y.length === 2 ? 2000 + +mt.y : +mt.y;
  else {
    const t = nyToday(nowMs);
    y = t.y;
    const diffDays = (Date.UTC(y, m - 1, d) - Date.UTC(t.y, t.m - 1, t.d)) / 86400000;
    if (diffDays < -30) y += 1;
  }
  return { y, m, d, raw: mt.raw };
}

// "7:00 am - 8:00 am", "10AM", "@4:30pm", "between 1:00 and 2:00 PM", "2pm-4pm", "noon"
// lenient: text is a dedicated time field ("Starting Time - 8-9am"), so bare numbers count.
// Otherwise (card names) a bare number needs a cue like "@", "at", "between".
function findTime(text, lenient = false) {
  if (!text) return null;
  const t = text.toLowerCase();
  if (/\bnoon\b/.test(t)) return { hh: 12, mm: 0, guessedMeridiem: false };
  const re = /(?<![\d/])(1[0-2]|0?\d)(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)?(?![\d/])/g;
  const cands = [];
  for (const mt of t.matchAll(re)) {
    // skip numbers that are clearly not times: "3 men", "20ft", "1 flight", "4.57"
    const after = t.slice(mt.index + mt[0].length, mt.index + mt[0].length + 8);
    const before = t.slice(Math.max(0, mt.index - 1), mt.index);
    if (!mt[2] && !mt[3] && /^\s*(men|man|movers?|ft|feet|flights?|hours?|hrs?|boxes|bed|br|truck|'|x|st|nd|rd|th|\.\d)/.test(after)) continue;
    if (before === '.' || before === '$') continue;
    if (!lenient && !mt[2] && !mt[3] && !/(@|at|between|time|-|–)\s*$/.test(t.slice(Math.max(0, mt.index - 10), mt.index))) continue;
    cands.push({ h: +mt[1], mm: +(mt[2] || 0), mer: mt[3] ? mt[3][0] : null, end: mt.index + mt[0].length });
  }
  if (!cands.length) return null;
  const first = cands[0];
  // meridiem of a range end applies to its start: "between 1:00 and 2:00 PM"
  let mer = first.mer || (cands[1] && cands[1].mer) || null;
  let guessed = false;
  if (!mer) { mer = first.h >= 7 && first.h <= 11 ? 'a' : 'p'; guessed = true; }
  let hh = first.h % 12 + (mer === 'p' ? 12 : 0);
  if (first.h === 0) hh = 0;
  return { hh, mm: first.mm, guessedMeridiem: guessed };
}

function field(desc, label) {
  // "**Starting Time - 7:00 am - 8:00 am**" / "Time - 11AM - 12PM"
  const clean = desc.replace(/\*\*|__|\\/g, '');
  // \s would match the newline too, so an empty field ("Name -") swallowed the next line
  const sp = '[^\\S\\n]*';
  const mt = clean.match(new RegExp('^' + sp + label + sp + '[-:–]' + sp + '(.*)$', 'im'));
  return mt ? mt[1].trim() : '';
}

function mdToHtml(md) {
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return esc(md)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])_(.+?)_(?!\w)/g, '$1<i>$2</i>')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '$1')
    .replace(/\\-/g, '-')
    .replace(/^#{1,6}\s*/gm, '')
    .split('\n').join('<br>');
}

// A date in the card NAME that belongs to a callback or follow-up is not the move date: the team writes
// "Maria Woodruff | 4MEN | Callback 10/30 @5:30PM", and that put her on the crew schedule for 10/30
// although the card says Nov 8 (2026-10-30). Those parts of the name are left out when looking for the
// move date. (The follow-up reminders read them on purpose and do not use this.)
const NOT_THE_MOVE = /\b(call\s*-?\s*back|callback|follow[\s-]*ups?|f\/u|cb|reminder|text\s*back|email\s*back)\b/i;
const nameForMoveDate = name => String(name || '').split('|').filter(seg => !NOT_THE_MOVE.test(seg)).join('|');

// kind: 'move' (Moves Booked → Pending job) or 'onsite' (Onsite Estimate job)
function parseCard(task, kind, nowMs = Date.now()) {
  const name = (task.name || '').trim();
  const desc = task.markdown_description || task.description || '';
  const warnings = [];

  let date = null, time = null, hours;
  if (task.due_date) {
    // Prefer ClickUp's real due date when the team sets it.
    const due = +task.due_date;
    const local = new Date(due + tzOffsetMin(due) * 60000);
    date = { y: local.getUTCFullYear(), m: local.getUTCMonth() + 1, d: local.getUTCDate(), raw: 'due date' };
    // ClickUp's GET task returns due_date_time as null even when a time was picked, so use the
    // clock time unless it is flagged date-only or sits on a date-only placeholder (00:00 / 04:00).
    const hh = local.getUTCHours(), mm = local.getUTCMinutes();
    const placeholder = mm === 0 && (hh === 0 || hh === 4);
    if (task.due_date_time !== false && !placeholder) time = { hh, mm, guessedMeridiem: false };
  }

  if (kind === 'move') {
    if (!date) {
      // The name and the description's "Date of Move" can disagree: the team updates the name when a
      // move is rescheduled (Abimbola: name 9/27, description "August 27"), but names have typos too
      // (Ellina DAY 2: name "8/8", description "October 8"). Pick the one that is a realistic upcoming
      // move date; if both are, trust the name. Either way, flag it on the shift.
      const fromDesc = findDate(field(desc, 'Date of Move'), nowMs);
      const fromName = findDate(nameForMoveDate(name), nowMs);
      if (fromDesc && fromName && (fromDesc.m !== fromName.m || fromDesc.d !== fromName.d)) {
        const t = nyToday(nowMs);
        const realistic = x => {
          const ahead = (Date.UTC(x.y, x.m - 1, x.d) - Date.UTC(t.y, t.m - 1, t.d)) / 86400000;
          return ahead >= -1 && ahead <= 120;
        };
        date = realistic(fromName) || !realistic(fromDesc) ? fromName : fromDesc;
        const other = date === fromName ? `the description says "${fromDesc.raw}"` : `the card name says ${fromName.m}/${fromName.d}`;
        warnings.push(`Dates on the card disagree: used ${date.m}/${date.d}, but ${other}. Please fix the card`);
      } else {
        date = fromDesc || fromName;
      }
    }
    if (!time) time = findTime(field(desc, 'Starting Time'), true);
    const est = parseFloat(field(desc, 'Estimated Time'));
    hours = est >= 1 && est <= 14 ? est : 4;
    if (!(est >= 1 && est <= 14)) warnings.push('No "Estimated Time" — shift length set to 4h');
  } else {
    // Visit date: due date → an "Estimate Date"-style line in the description → card name.
    // "Date of Move" is NOT the visit date.
    const visitDate = field(desc, '(?:onsite|on-site|estimate|visit|appointment)(?:\\s+(?:visit|estimate|appointment))?\\s*date');
    if (!date) date = findDate(visitDate, nowMs);
    if (date && !time) time = findTime(field(desc, '(?:onsite|on-site|estimate|visit|appointment)(?:\\s+(?:visit|estimate|appointment))?\\s*time'), true)
      || findTime(visitDate.slice(visitDate.indexOf(date.raw) + date.raw.length), true);
    if (!date) date = findDate(nameForMoveDate(name), nowMs);
    if (!time) time = findTime(name.slice(name.indexOf(date ? date.raw : '') + (date ? date.raw.length : 0))) || findTime(field(desc, 'Time'), true);
    hours = 1;
  }

  if (!date) return { ok: false, reason: kind === 'move' ? 'Could not find the move date' : 'Could not find the onsite visit date in the card name', name };
  if (!time) { time = { hh: kind === 'move' ? 8 : 10, mm: 0 }; warnings.push(`No start time found — set to ${kind === 'move' ? '8:00 AM' : '10:00 AM'}`); }
  else if (time.guessedMeridiem) warnings.push('AM/PM not written — assumed ' + (time.hh < 12 ? 'AM' : 'PM'));

  const startTime = toEpoch(date.y, date.m, date.d, time.hh, time.mm);
  // A booked move or visit in the past means the date on the card is wrong: never schedule it.
  if (startTime * 1000 < nowMs - 86400000) {
    return { ok: false, name, reason: `The date on the card (${date.m}/${date.d}/${date.y}) is in the past, so no shift was created. Please check the date on the card` };
  }
  const endTime = startTime + Math.round(hours * 3600);
  const url = task.url || `https://app.clickup.com/t/${task.id}`;
  const flag = warnings.filter(w => /dates on the card disagree/i.test(w)).map(w => `<b>⚠ ${w}</b><br><br>`).join('');
  const notesHtml = flag + mdToHtml(desc) + `<br><br>ClickUp card: <a href="${url}">${url}</a><br>clickup-id:${task.id}`;
  const pad = n => String(n).padStart(2, '0');
  return {
    ok: true, name, kind, taskId: task.id, url, startTime, endTime, hours,
    localStart: `${date.y}-${pad(date.m)}-${pad(date.d)} ${pad(time.hh)}:${pad(time.mm)}`,
    notesHtml, warnings,
  };
}

if (typeof module !== 'undefined') module.exports = { nameForMoveDate, parseCard, field, findDate, findTime, toEpoch, tzOffsetMin, nyToday };
