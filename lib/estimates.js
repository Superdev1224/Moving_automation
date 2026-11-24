// Movegistics estimates → ClickUp card comments (Additional Integrations #4).
// Movegistics has no webhooks, so the workflow polls the (undocumented) estimate/list and compares
// each estimate with what it saw last time. One comment per estimate per change:
// sent / re-sent (with total + PDF link), opened by the customer, signed, deposit paid.

const EST_KEEP_DAYS = 60;

function estimateSnapshot(e) {
  return { status: e.status || '', sentAt: +e.sentAt || 0, total: +e.total || 0, paid: !!e.deposit?.isPaid, at: +e.updatedAt || 0 };
}

// prev: {estimateId: snapshot} from staticData (null on the very first run = just take a baseline).
function estimateEvents(rows, prev) {
  const events = [];
  for (const e of rows) {
    if (e.isDeleted || !e.sentAt) continue;                       // drafts: nothing the customer has seen
    const now = estimateSnapshot(e), was = prev && prev[e._id];
    if (!prev) continue;
    const what = [];
    if (!was) what.push('sent');
    else {
      if (now.sentAt !== was.sentAt) what.push('resent');
      if (now.status !== was.status) what.push(now.status.toLowerCase());
      if (now.paid && !was.paid) what.push('paid');
    }
    // a brand-new estimate that is already opened/signed/paid: say so in the same comment
    if (!was && now.status !== 'Sent') what.push(now.status.toLowerCase());
    if (!was && now.paid) what.push('paid');
    if (what.length) events.push({ estimate: e, what: [...new Set(what)] });
  }
  return events;
}

function rememberEstimates(rows, prev, nowMs) {
  const next = { ...(prev || {}) };
  for (const e of rows) if (e.sentAt && !e.isDeleted) next[e._id] = estimateSnapshot(e);
  for (const [k, v] of Object.entries(next)) if (nowMs - v.at > EST_KEEP_DAYS * 86400000) delete next[k];
  return next;
}

const money = n => '$' + (+n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function estimateCustomer(e) {
  const c = (e.contacts || [])[0] || {};
  return { name: c.name || [c.firstName, c.lastName].filter(Boolean).join(' ') || (e.opportunityName || '').replace(/\s*-\s*Work Request$/i, ''),
    email: c.email || '', phone: c.phoneNumber || '' };
}

function estimateComment(ev) {
  const e = ev.estimate, w = ev.what;
  const job = [e.jobType?.name, e.distance ? `${e.distance} ${String(e.distanceUnit || 'miles').toLowerCase()}` : ''].filter(Boolean).join(', ');
  const head = w.includes('signed') ? `✍️ Estimate ${e.displayId} SIGNED`
    : w.includes('sent') ? `📄 Estimate ${e.displayId} sent`
    : w.includes('resent') ? `📄 Estimate ${e.displayId} re-sent`
    : w.includes('viewed') ? `👀 Customer opened estimate ${e.displayId}`
    : w.includes('paid') ? `💳 Deposit paid on estimate ${e.displayId}`
    : `📄 Estimate ${e.displayId} is now ${e.status}`;
  let s = `${head}: ${e.isRange ? 'from ' : ''}${money(e.total)}${job ? ` (${job})` : ''}.`;
  const extra = [];
  if (w.includes('sent') && w.includes('viewed')) extra.push('The customer has already opened it');
  if (w.includes('signed')) extra.push(e.deposit?.isPaid ? `${money(e.deposit.total || 52)} deposit paid` : 'Deposit NOT paid yet');
  else if (w.includes('paid')) extra.push(`${money(e.deposit?.total || 52)} deposit paid, signature still needed`);
  if (extra.length) s += ` ${extra.join('. ')}.`;
  if (e.documentName) s += `\nEstimate PDF: ${e.documentName}`;
  s += `\nMovegistics: ${e.opportunityDisplayId || e.opportunityName || ''}`;
  return s;
}

// The card itself shows the latest estimate (client, 2026-09-30: "the part where we send the estimate
// link to ClickUp is not complete" - it was only in a comment). "Estimated Cost" gets the total and an
// "Estimate" line links it. Only a blank line, or one we wrote before, is ever changed.
function withEstimate(md, e) {
  if (!e || !e.documentName) return null;
  const total = `${e.isRange ? 'from ' : ''}${money(e.total)}`;
  const cost = `${total} (${e.displayId})`;
  const link = `[${e.displayId} - ${total} - open the estimate](${e.documentName})`;
  const before = String(md || '');
  let out = before;
  const costRe = /^(\*\*Estimated Cost -\*\*)[^\S\n]*(.*)$/m;
  const m = out.match(costRe);
  // function replacements: a "$474" in the text must not be read as a regex group
  if (m && (!m[2].trim() || /\(ES\d+\)/.test(m[2]))) out = out.replace(costRe, (_, label) => `${label} ${cost}`);
  const linkRe = /^\*\*Estimate -\*\*.*$/m;
  if (linkRe.test(out)) out = out.replace(linkRe, () => `**Estimate -** ${link}`);
  else if (m) out = out.replace(/^(\*\*Estimated Cost -\*\*.*)$/m, line => `${line}\n**Estimate -** ${link}`);
  else out = out.replace(/\s*$/, '') + `\n\n**Estimate -** ${link}`;
  return out === before ? null : out;
}

if (typeof module !== 'undefined') module.exports = { withEstimate, estimateEvents, rememberEstimates, estimateComment, estimateCustomer, estimateSnapshot };
