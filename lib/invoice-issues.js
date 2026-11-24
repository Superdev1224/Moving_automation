// Invoice Auditor issues (client spec "Invoice Auditor | Complete Developer Specification", 2026-11-16).
// Turns one invoice audit (#5 math + deposit, #5b estimate + crew report) into:
//   - an outcome: Passed / Needs Review / Incomplete (a required record missing; never "Passed")
//   - issues with a deterministic id (<invoice>:<category>), category, $ impact, direction and confidence
// Isolated differences of $3 or less are routine noise and dropped (spec 8).
// The issue workflow (#32) posts one thread per issue in #invoices and tracks its claim and status.

const NOISE = 3;
const dollars = s => (String(s).match(/-?\$[\d,]+(?:\.\d+)?/g) || []).map(x => parseFloat(x.replace(/[$,]/g, '')));

function classifyIssue(text, kind) {
  const t = String(text);
  let cat = 'other', impact = null, dir = '';
  let m;
  if (/not a quarter hour/.test(t)) { cat = 'hours-rounding'; m = t.match(/\+\$([\d,.]+)/); dir = 'under'; }
  else if (/crew's times give/.test(t)) { cat = 'hours'; m = t.match(/(under|over)charged \$([\d,.]+)/); if (m) { dir = m[1]; m = [0, m[2]]; } }
  else if (/signed estimate .* says \$/.test(t)) { cat = 'rate'; }
  else if (/-hour minimum/.test(t)) { cat = 'minimum'; dir = 'under'; }
  else if (/^Card fee/.test(t)) { cat = 'card-fee'; m = t.match(/difference (-?\$[\d,.]+)/); if (m) { const d = dollars(m[1])[0]; dir = d > 0 ? 'over' : 'under'; m = [0, String(Math.abs(d))]; } }
  else if (/deposit/i.test(t)) {
    // deposit: $50 credited once (client 2026-11-17). Impact = how far the credit is from $50.
    cat = 'deposit'; const d = dollars(t);
    if (/takes off \$/.test(t) && d.length >= 2) { impact = Math.abs(d[0] - d[d.length - 1]); dir = d[0] > d[d.length - 1] ? 'under' : 'over'; }
    else if (/taken off twice/.test(t)) { impact = Math.min(...d.slice(0, 2)); dir = 'under'; }
    else { impact = 50; dir = 'over'; }
  }
  else if (/^Materials/.test(t)) { cat = 'materials'; dir = /not billed/.test(t) ? 'under' : 'over'; }
  else if (/Line "|Lines add up|Sub Total|Total should|Grand Total/.test(t)) {
    cat = 'math'; const d = dollars(t); if (d.length >= 2) { impact = Math.abs(d[d.length - 2] - d[d.length - 1]); }
  }
  if (m && m[1] != null) impact = parseFloat(String(m[1]).replace(/,/g, ''));
  if (impact != null) impact = Math.round(impact * 100) / 100;
  const confidence = kind === 'error' ? 'High' : /estimated finish/.test(t) ? 'Low' : 'Medium';
  return { cat, impact, dir, confidence };
}

// chk = #5 math + deposit ({problems, notes}); a = #5b audit ({problems, review, notes, lines}) or null if it failed
function auditOutcome(inv, chk, a) {
  const raw = [...(chk.problems || []).map(t => ['error', t]), ...((a && a.problems) || []).map(t => ['error', t]),
               ...((a && a.review) || []).map(t => ['review', t])];
  const issues = [];
  for (const [kind, text] of raw) {
    const c = classifyIssue(text, kind);
    if (c.impact != null && c.impact <= NOISE && c.cat !== 'deposit') continue;   // routine noise (deposits always count)
    const id = `${inv.number || 'unknown'}:${c.cat}`;
    if (issues.some(i => i.id === id)) { issues.find(i => i.id === id).text += ` · ${text}`; continue; }
    issues.push({ id, kind, text: String(text).replace(/\s*_\(Fix:[^)]*\)_/, ''), ...c });
  }
  const notes = [...(chk.notes || []), ...((a && a.notes) || [])];
  // what could not be checked (spec 8: Incomplete, never Passed)
  const missing = [];
  if (!a) missing.push('estimate/crew audit failed to run');
  else {
    if (notes.some(n => /No signed estimate found/.test(n))) missing.push('signed estimate not found');
    if (notes.some(n => /not found on the Connecteam schedule/.test(n))) missing.push('job not found on the schedule');
    else if (notes.some(n => /No crew report found/.test(n))) missing.push('no crew start/finish times found');
  }
  if (notes.some(n => /No Movegistics estimate found/.test(n))) missing.push('deposit not checked (no Movegistics estimate)');
  const flat = notes.some(n => /Flat-rate job/.test(n)) || (inv.lines || []).some(l => /flat/i.test(l.desc));
  const needed = flat ? missing.filter(x => !/crew start|schedule/.test(x)) : missing;   // a flat job does not need hours
  const outcome = issues.length ? 'Needs Review' : needed.length ? 'Incomplete' : 'Passed';
  const confidence = !issues.length ? '' : issues.some(i => i.confidence === 'High') ? 'High'
    : issues.some(i => i.confidence === 'Medium') ? 'Medium' : 'Low';
  return { outcome, confidence, issues, missing: needed, notes, lines: (a && a.lines) || [] };
}

const CAT_NAME = { 'hours-rounding': 'Hours not in quarter hours', hours: 'Billed hours vs crew times', rate: 'Hourly rate vs estimate',
  minimum: 'Minimum hours', 'card-fee': 'Card fee', deposit: 'Deposit', materials: 'Materials', math: 'Invoice math', other: 'Other' };
const usdI = n => '$' + (+n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// assignee: the one person who handles every issue (client 2026-11-17: no claiming), already formatted
// 2026-11-18 client: "more organized, it's really hard to read" - one fact per line, the checks as a list
function issueText(issue, audit, live, assignee) {
  const dot = issue.kind === 'error' ? ':red_circle:' : ':large_yellow_circle:';
  const impact = issue.impact != null && +issue.impact
    ? `*${usdI(issue.impact)} ${issue.dir === 'under' ? 'underbilled' : issue.dir === 'over' ? 'overbilled' : ''}*`.replace(' *', '*') : 'amount not known';
  const checks = (Array.isArray(audit.lines) ? audit.lines : String(audit.lines || '').split(' · ')).filter(Boolean);
  const refs = [audit.gmailId ? `<https://mail.google.com/mail/u/?authuser=contact%40expresshaulers.com#all/${audit.gmailId}|Invoice email>` : '',
    audit.estimate ? `Estimate ${audit.estimate}` : '', audit.jobDay ? `Job ${audit.jobDay}` : ''].filter(Boolean).join('  ·  ');
  return [
    `${live ? '' : ':test_tube: _Preview_  '}${dot} *${audit.invoice}  ·  ${audit.customer || '?'}*`,
    `*${CAT_NAME[issue.cat] || issue.cat}*  ·  ${impact}  ·  ${issue.confidence} confidence`,
    `>${issue.text}`,
    ...(checks.length ? ['', '*What was checked*', ...checks.map(c => `• ${c}`)] : []),
    ...(refs ? [`:paperclip: ${refs}`] : []),
    '',
    `:bust_in_silhouette: ${assignee || '_not assigned yet_'}`,
    ':white_check_mark: fixed (reply here with a short note)  ·  :no_entry_sign: not a problem  ·  :alarm_clock: still open after 3 h → Diego',
  ].join('\n');
}

if (typeof module !== 'undefined') module.exports = { classifyIssue, auditOutcome, issueText, CAT_NAME, NOISE };
