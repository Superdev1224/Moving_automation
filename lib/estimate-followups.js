// Estimates nobody has responded to (Additional Integrations #6).
// Once a morning: estimates sent more than 24 h ago that are still not signed, where the customer
// has not called, texted or emailed us since we sent it. One list in #sales, not a ping each time.

const QUIET_H = 24;             // how long silence has to last before it is worth chasing
const CHASE_DAYS = 7;           // after a week it is cold, and Movegistics only returns 100 rows
const MAX_ROWS = 10;

const tenDigits = s => String(s || '').replace(/\D/g, '').slice(-10);

// rows: Movegistics estimate/list; uses estimateCustomer() from lib/estimates.js
function quietEstimates(rows, nowMs) {
  return (rows || [])
    .filter(e => !e.isDeleted && e.sentAt && String(e.status || '').toLowerCase() !== 'signed')
    .filter(e => nowMs - +e.sentAt > QUIET_H * 3600000 && nowMs - +e.sentAt < CHASE_DAYS * 86400000)
    .map(e => {
      const c = estimateCustomer(e);
      return { id: e.displayId, sentAt: +e.sentAt, status: e.status, total: +e.total || 0,
        opportunity: e.opportunityDisplayId || '', pdf: e.documentName || '',
        name: c.name, email: (c.email || '').toLowerCase(), phone: tenDigits(c.phone) };
    });
}

// Did the customer come back to us after the estimate went out?
function heardBack(row, calls, texts, emailCount) {
  if (emailCount > 0) return 'email';
  const after = ms => ms > row.sentAt;
  const theirs = n => row.phone && tenDigits(n) === row.phone;
  if ((calls || []).some(c => c.direction === 'Inbound' && after(Date.parse(c.startTime)) && theirs(c.from?.phoneNumber))) return 'call';
  if ((calls || []).some(c => c.direction === 'Outbound' && c.result === 'Call connected' && after(Date.parse(c.startTime)) && theirs(c.to?.phoneNumber))) return 'call';
  if ((texts || []).some(m => m.direction === 'Inbound' && after(Date.parse(m.creationTime)) && theirs((m.from || {}).phoneNumber))) return 'text';
  return '';
}

function quietEstimateSlack(rows, nowMs) {
  if (!rows.length) return '';
  const money = n => '$' + Math.round(n).toLocaleString('en-US');
  const days = r => Math.max(1, Math.round((nowMs - r.sentAt) / 86400000));
  const sorted = [...rows].sort((a, b) => a.sentAt - b.sentAt);
  const head = `:page_facing_up: *${rows.length} estimate${rows.length > 1 ? 's' : ''} with no reply yet*`;
  const lines = sorted.slice(0, MAX_ROWS).map(r => {
    const seen = String(r.status).toLowerCase() === 'viewed' ? 'opened it' : 'not opened';
    const card = r.cardUrl ? ` · <${r.cardUrl}|card>` : '';
    return `• *${r.name}* — ${money(r.total)} · sent ${days(r)}d ago · ${seen}${card}`;
  });
  if (sorted.length > MAX_ROWS) lines.push(`• …and ${sorted.length - MAX_ROWS} more`);
  return [head, ...lines, '_No call, text or email back since we sent it._'].join('\n');
}

if (typeof module !== 'undefined') module.exports = { quietEstimates, heardBack, quietEstimateSlack, QUIET_H, CHASE_DAYS };
