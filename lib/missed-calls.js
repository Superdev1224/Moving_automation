// Missed call rescue (Additional Integrations #8).
// A customer's inbound call was missed (or went to voicemail) and nobody has called or texted them
// back within GRACE_MIN minutes → one alert in #sales. Several missed calls from the same number are
// one alert. Outside business hours nothing is posted; calls still unanswered are alerted at 8 AM.
// Crew (Connecteam staff phones, "| Mover" contacts) and our own lines are never alerted.

const GRACE_MIN = 5;
const MIN_RING_SEC = 15;        // a ring under 15 s is an auto-dialler or a mis-dial, not a customer
const NUMBER_QUIET_H = 24;      // the same number is never chased twice in a day
const OPEN_HOUR = 8, CLOSE_HOUR = 21;             // work hours 8 AM - 9 PM New York, every day (client, 2026-10-06)
const LOOKBACK_H = 14;                            // covers the whole night before the 8 AM round
const CREW_NAME = /\|\s*mover\b|\bcrew\b|\bdriver\b/i;

const last10 = p => String(p || '').replace(/\D/g, '').slice(-10);

function nyHour(ms) {
  return +new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }).format(new Date(ms));
}
const isOpen = ms => { const h = nyHour(ms); return h >= OPEN_HOUR && h < CLOSE_HOUR; };
const nyClock = ms => new Date(ms).toLocaleString('en-US', { timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' });

// A call that is still ringing, or being spoken to right now, is logged as "Missed" until it ends.
// That is how we once told the team nobody had called back while they were six minutes into a
// 34-minute call with her. So an answered leg, or a call in progress, counts as handled.
const answeredLeg = c => (c.legs || []).some(l => ['Accepted', 'Call connected'].includes(l.result));

// calls: call-log records (view=Detailed, so legs are included); texts: message-store records;
// staffPhones / ourNumbers: numbers that are never customers; activeNumbers: on a call right now.
function findUnanswered({ calls, texts, staffPhones, ourNumbers, activeNumbers = [], seen, seenNumbers = {}, nowMs }) {
  const onACallNow = new Set((activeNumbers || []).map(last10).filter(Boolean));
  const internal = new Set([...staffPhones, ...ourNumbers].map(last10).filter(Boolean));
  const at = r => Date.parse(r.startTime || r.creationTime);
  const byNumber = {};
  for (const c of calls) {
    if (c.direction !== 'Inbound' || !['Missed', 'Voicemail'].includes(c.result)) continue;
    const n = last10(c.from?.phoneNumber);
    if (!n || internal.has(n) || c.from?.extensionId || CREW_NAME.test(c.from?.name || '')) {
      if (n && !seen[c.id]) seen[c.id] = nowMs;      // crew / internal: never look at it again
      continue;
    }
    if (seen[c.id]) continue;
    if (answeredLeg(c)) { seen[c.id] = nowMs; continue; }        // somebody picked this call up
    if (onACallNow.has(n)) continue;                             // they are on the phone with us now
    // a voicemail is always a real customer; a very short ring almost never is
    if (c.result !== 'Voicemail' && (+c.duration || 0) < MIN_RING_SEC) { seen[c.id] = nowMs; continue; }
    if (nowMs - (seenNumbers[n] || 0) < NUMBER_QUIET_H * 3600000) { seen[c.id] = nowMs; continue; }
    (byNumber[n] = byNumber[n] || []).push(c);
  }

  const due = [], handled = [];
  for (const [n, missed] of Object.entries(byNumber)) {
    missed.sort((a, b) => at(a) - at(b));
    const first = at(missed[0]), latest = at(missed[missed.length - 1]);
    // any contact after the FIRST missed call counts: an answered call-back, an outbound call or text
    // an answered call with this customer at ANY point in the window means they were spoken to:
    // RingCentral replaces the missed leg with a single "Accepted" record once the call ends
    const reached = calls.some(c => c.direction === 'Inbound' && last10(c.from?.phoneNumber) === n
        && (c.result === 'Accepted' || answeredLeg(c)))
      || calls.some(c => at(c) > first && c.direction === 'Outbound' && last10(c.to?.phoneNumber) === n)
      || texts.some(m => m.direction === 'Outbound' && at(m) > first && (m.to || []).some(t => last10(t.phoneNumber) === n));
    if (reached) { handled.push(...missed.map(c => c.id)); continue; }
    if (nowMs - latest < GRACE_MIN * 60000 || !isOpen(nowMs)) continue;     // wait, or hold overnight
    due.push({ number: n, name: missed[missed.length - 1].from?.name || '', calls: missed,
      voicemail: missed.some(c => c.result === 'Voicemail'),
      overnight: missed.every(c => !isOpen(at(c))) });
  }
  return { due, handled };
}

function missedCallSlack(d, match, nowMs) {
  const at = c => Date.parse(c.startTime);
  const last = at(d.calls[d.calls.length - 1]);
  const mins = Math.round((nowMs - last) / 60000);
  const ago = mins < 90 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`;
  const pretty = `(${d.number.slice(0, 3)}) ${d.number.slice(3, 6)}-${d.number.slice(6)}`;
  // "EH - " is RingCentral's prefix for calls through the company queue; "DOVER NJ" is a city, not a name
  // a forwarded call is named "BROOKLYN NY - Fwd Express Haulers Moving 1st Number, Ext. 101": keep the caller part
  const name = d.name.replace(/^EH\s*-\s*/i, '').replace(/\s*-\s*Fwd\b.*$/i, '').replace(/\s+/g, ' ').trim();
  const caller = /^[A-Z .]+ [A-Z]{2}$/.test(name) ? '' : name;
  let s = `:telephone_receiver: *Missed call, no call-back yet*: ${match ? match.task.name.split('|')[0].trim() : caller || 'Unknown caller'} ${pretty}\n`;
  s += `${d.calls.length > 1 ? `Called ${d.calls.length} times, last` : 'Called'} ${nyClock(last)} (${ago})`
    + `${d.voicemail ? ', *left a voicemail*' : ''}${d.overnight ? ' (after hours)' : ''}.`;
  s += match ? `\nCard: <${match.task.url}|${match.task.name}> (${match.task.status?.status || ''})` : '\nNo ClickUp card: probably a new lead.';
  return s;
}

// Forget alerts older than 2 days so staticData stays small.
function pruneSeen(seen, nowMs) {
  for (const [k, v] of Object.entries(seen)) if (nowMs - v > 2 * 86400000) delete seen[k];
  return seen;
}

if (typeof module !== 'undefined') module.exports = { findUnanswered, missedCallSlack, pruneSeen, isOpen, GRACE_MIN, LOOKBACK_H };
