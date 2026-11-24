// Finished moves go to "Moves Complete" on their own (client's status rules, 2026-09-25).
// A booked card - "moves booked", the two upsell steps or "upsold customers" - whose move date has
// passed belongs in "moves complete". The upsell steps were added on 2026-10-05 (Kristy Schmidt and
// Carol Herman sat in "upsell in progess" after their 10/3 moves). "pending" is left out on purpose:
// deposit or signature missing, so a passed date does not prove the move happened.
// Cards whose date cannot be read are never touched: they are reported instead.
// Uses findDate / field / nyToday from lib/parse-card.js.

const FROM_STATUS = ['moves booked', 'upsell in progess', 'interested upselling', 'upsold customers'];
const TO_STATUS = 'moves complete';
const GRACE_DAYS = 1;           // wait a full day after the move before closing it off
// A finished INFLUENCER move does not go to Moves Complete: it goes to the influencer board, status
// "influencers content wait" - we now wait for their video (client, 2026-11-02: "they should only be
// moved there once they are done").
const INFLUENCER_LIST = '901705958022';
const CONTENT_WAIT = 'influencers content wait';
// ... but a customer REFERRED by an influencer is not an influencer job ("Alvin Pinchi referred by Bri
// Hoffman Influencer", "Bryce Jacob referred by Lara Philibosian | Influencer"): they make no content and
// go to Moves Complete like everyone else.
const isInfluencerMove = t => !/\breferr?(ed|al)\b/i.test(String(t.name || ''))
  && (/\binfluencer\b/i.test(String(t.name || '')) || (t.tags || []).some(g => /influencer/i.test(String(g.name || ''))));

// The move date, from the ClickUp due date, the "Date of Move" line, or the card name.
// Cards often disagree with themselves (name says 9/27, description says August 27). Closing a move
// off is not reversible by the automation, so any disagreement means "do not touch, tell the team".
function moveDateOf(task, nowMs) {
  const desc = task.markdown_description || task.description || '';
  const fromDesc = findDate(field(desc, 'Date of Move'), nowMs);
  const fromName = findDate(nameForMoveDate(task.name || ''), nowMs);   // not a callback date
  const due = task.due_date
    ? (d => ({ y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate() }))(new Date(+task.due_date))
    : null;
  const same = (a, b) => !a || !b || (a.m === b.m && a.d === b.d);
  if (!same(fromDesc, fromName) || !same(due, fromDesc) || !same(due, fromName)) {
    const seen = [due && `due ${due.m}/${due.d}`, fromDesc && `description ${fromDesc.m}/${fromDesc.d}`,
                  fromName && `name ${fromName.m}/${fromName.d}`].filter(Boolean).join(' vs ');
    return { conflict: `the dates on the card disagree (${seen})` };
  }
  if (due) return { ...due, from: 'due date' };
  if (fromDesc) return { ...fromDesc, from: 'Date of Move' };
  return fromName ? { ...fromName, from: 'card name' } : null;
}

const daysApart = (a, b) => Math.round((Date.UTC(a.y, a.m - 1, a.d) - Date.UTC(b.y, b.m - 1, b.d)) / 86400000);

// Returns { done: [...], unreadable: [...] } — done are the cards to move.
function finishedMoves(tasks, nowMs = Date.now()) {
  const today = nyToday(nowMs);
  const done = [], unreadable = [];
  for (const t of tasks || []) {
    const status = String(t.status?.status || '').toLowerCase();
    if (!FROM_STATUS.includes(status)) continue;
    const date = moveDateOf(t, nowMs);
    const row = { id: t.id, name: (t.name || '').slice(0, 70), url: t.url, status, influencer: isInfluencerMove(t) };
    if (!date) { unreadable.push(row); continue; }
    if (date.conflict) { unreadable.push({ ...row, note: date.conflict }); continue; }
    const ago = -daysApart(date, today);                       // positive = in the past
    // a date far in the past is usually a typo on the card, not a move from last year
    if (ago >= GRACE_DAYS && ago <= 120) done.push({ ...row, moveDate: `${date.m}/${date.d}`, daysAgo: ago, readFrom: date.from });
    else if (ago > 120) unreadable.push({ ...row, note: `date reads ${date.m}/${date.d}/${date.y}, ${ago} days ago` });
  }
  return { done, unreadable };
}

const completeComment = row => (row.influencer
  ? `✅ Influencer move done (${row.moveDate}, from the ${row.readFrom}): moved to All Influencer Leads → Influencers Content Wait automatically - waiting for their content.`
  : `✅ Moved to Moves Complete automatically: the move date (${row.moveDate}, from the ${row.readFrom}) has passed.`);

function completeSlack(done, unreadable, live) {
  const lines = [];
  if (done.length) {
    lines.push(`:white_check_mark: *${done.length} finished move${done.length > 1 ? 's' : ''} ${live ? 'moved to' : 'would move to'} Moves Complete*`);
    lines.push(...done.slice(0, 10).map(r => `• <${r.url}|${r.name}> — moved ${r.moveDate} (${r.daysAgo}d ago)${r.influencer ? ' → *Influencers Content Wait*' : ''}`));
    if (done.length > 10) lines.push(`• …and ${done.length - 10} more`);
  }
  if (unreadable.length) {
    lines.push(`:grey_question: *${unreadable.length} booked card${unreadable.length > 1 ? 's' : ''} with no readable move date* (left alone)`);
    lines.push(...unreadable.slice(0, 5).map(r => `• <${r.url}|${r.name}>${r.note ? ` — ${r.note}` : ''}`));
    if (unreadable.length > 5) lines.push(`• …and ${unreadable.length - 5} more`);
  }
  return lines.join('\n');
}

if (typeof module !== 'undefined') module.exports = { isInfluencerMove, INFLUENCER_LIST, CONTENT_WAIT, finishedMoves, moveDateOf, completeComment, completeSlack, FROM_STATUS, TO_STATUS };
