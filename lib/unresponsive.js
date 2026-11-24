// Unresponsive leads (client's status rules, 2026-09-25):
// "no response after more than 5 texts, or 5 unanswered calls" — then the card belongs in
// "Unresponsive Leads" so the team can try a different number.
// Anything the customer does (answering, calling back, texting) resets the count to zero.

const CALL_TRIES = 5;           // unanswered outbound calls in a row
const TEXT_TRIES = 5;           // outbound texts in a row with no reply
const ANSWERED_SEC = 45;        // a "connected" call this short is voicemail or a dropped ring
const LOOK_BACK_DAYS = 30;
const CHASEABLE = ['new leads', 'opportunities', 'scheduled follow ups', 'realtors w customers'];

const ten = s => String(s || '').replace(/\D/g, '').slice(-10);
const answered = c => c.result === 'Call connected' && (+c.duration || 0) >= ANSWERED_SEC;

// One entry per customer number: when they last engaged, and how many tries since.
function chaseIndex(calls, texts) {
  const at = r => Date.parse(r.startTime || r.creationTime);
  const idx = {};
  const touch = n => (idx[n] = idx[n] || { lastResponse: 0, callTries: [], textTries: [] });
  for (const c of calls || []) {
    const inbound = c.direction === 'Inbound';
    const n = ten(inbound ? c.from?.phoneNumber : c.to?.phoneNumber);
    if (!n) continue;
    const e = touch(n);
    // the customer ringing us, even a missed call, counts as them responding
    if (inbound) e.lastResponse = Math.max(e.lastResponse, at(c));
    else if (answered(c)) e.lastResponse = Math.max(e.lastResponse, at(c));
    else e.callTries.push(at(c));
  }
  for (const m of texts || []) {
    const inbound = m.direction === 'Inbound';
    const n = ten(inbound ? (m.from || {}).phoneNumber : ((m.to || [])[0] || {}).phoneNumber);
    if (!n) continue;
    const e = touch(n);
    if (inbound) e.lastResponse = Math.max(e.lastResponse, at(m));
    else e.textTries.push(at(m));
  }
  for (const e of Object.values(idx)) {
    e.calls = e.callTries.filter(t => t > e.lastResponse).length;
    e.texts = e.textTries.filter(t => t > e.lastResponse).length;
    e.lastTry = Math.max(0, ...e.callTries, ...e.textTries);
  }
  return idx;
}

// Which lead cards have gone quiet despite repeated tries?
function unresponsiveLeads(tasks, idx) {
  const out = [];
  for (const t of tasks || []) {
    const status = String(t.status?.status || '').toLowerCase();
    if (!CHASEABLE.includes(status)) continue;
    const text = `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '');
    const phones = [...new Set([...text.matchAll(/(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)/g)]
      .map(m => m[1] + m[2] + m[3]))];
    let best = null;
    for (const p of phones) {
      const e = idx[p];
      if (!e) continue;
      if (!best || e.calls + e.texts > best.calls + best.texts) best = { ...e, phone: p };
    }
    if (!best) continue;
    if (best.calls < CALL_TRIES && best.texts < TEXT_TRIES) continue;
    out.push({ id: t.id, name: (t.name || '').split('|')[0].trim(), url: t.url, status,
      phone: best.phone, calls: best.calls, texts: best.texts, lastTry: best.lastTry,
      everResponded: best.lastResponse > 0 });
  }
  return out.sort((a, b) => (b.calls + b.texts) - (a.calls + a.texts));
}

const unresponsiveComment = r =>
  `🔕 No response after ${r.calls} unanswered call${r.calls === 1 ? '' : 's'} and ${r.texts} text${r.texts === 1 ? '' : 's'}`
  + ` since they last replied. Worth trying from a different number, or moving this card to Unresponsive Leads.`;

function unresponsiveSlack(rows) {
  if (!rows.length) return '';
  const head = `:no_bell: *${rows.length} lead${rows.length > 1 ? 's' : ''} not responding*`;
  const lines = rows.slice(0, 10).map(r =>
    `• <${r.url}|${r.name || 'Unnamed'}> — ${r.calls} unanswered call${r.calls === 1 ? '' : 's'}, ${r.texts} text${r.texts === 1 ? '' : 's'}`
    + `${r.everResponded ? '' : ' (never responded)'} · _${r.status}_`);
  if (rows.length > 10) lines.push(`• …and ${rows.length - 10} more`);
  return [head, ...lines, '_Try a different number, or move them to Unresponsive Leads._'].join('\n');
}

if (typeof module !== 'undefined') module.exports = { chaseIndex, unresponsiveLeads, unresponsiveComment, unresponsiveSlack, CALL_TRIES, TEXT_TRIES, LOOK_BACK_DAYS };
