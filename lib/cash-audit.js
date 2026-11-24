// Cash Payment Audit (#30, 2026-11-16) — client PDF "AI Automations", item 1; replaces ChatGPT's auditor.
// Crews collect the balance in cash at the end of a job and tell the office by text, by phone, or the
// office notes it in #invoices. Claude reads one day of that evidence (with the day's jobs, invoices and
// the Cash Payments rows already there) and lists every cash payment. Then:
//   - verified (full name, amount, mover, time and total job cost all clear) and not in the sheet → "please add"  🔵
//     (REPORT ONLY since 2026-11-17: the client wants the sheet never changed by automation)
//   - a cash payment with something missing or unclear → "review needed" in #invoices                    🟡
//   - a row already in the sheet that the evidence contradicts (amount, mover)                             🔴
// The Cash Payments sheet is the company's PROOF of cash received (client 2026-11-17); staff add rows by hand.
// Card / Zelle / Venmo payments are not cash and are ignored. Every item is said once.

const CASH_MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const cashTab = day => `${CASH_MONTHS[+day.slice(5, 7) - 1]} ${day.slice(0, 4)}`;          // "NOV 2026"
const cashMoney = v => { const n = parseFloat(String(v ?? '').replace(/[$,\s]/g, '')); return Number.isFinite(n) ? n : null; };
const cashUsd = n => '$' + (+n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cashWords = s => String(s || '').toLowerCase().replace(/[^a-z ]/g, ' ').split(/\s+/).filter(w => w.length > 1);

// Sheet rows → {name, day "M/D", time, mover, cash, total, note}. Column A has no header; B..F do.
function cashRows(values) {
  return (values || []).slice(1).filter(r => r && String(r[0] || '').trim())
    .map(r => ({ name: String(r[0]).trim(), day: String(r[1] || '').trim(), time: String(r[2] || '').trim(),
                 mover: String(r[3] || '').trim(), cash: cashMoney(r[4]), total: String(r[5] || '').trim(), note: String(r[6] || '').trim() }));
}

const mdOf = day => `${+day.slice(5, 7)}/${+day.slice(8, 10)}`;                          // "2026-11-01" → "11/1"

function cashSystem() {
  return [
    'You audit CASH payments for Express Haulers Moving (New Jersey). At the end of a job the crew lead collects',
    'the balance, often in cash, and tells the office by text message or phone call; office staff also post it in',
    'Slack (#invoices). You get one day of that evidence, the jobs on the schedule that day, the invoices sent, and',
    'the rows already in the "Cash Payments" sheet (the company\'s proof of cash received, kept by hand).',
    'List every cash payment that was ACTUALLY COLLECTED that day (not quoted, promised or planned). Card, Zelle,',
    'Venmo, check and online payments are NOT cash - leave them out. A deposit is not a cash payment.',
    'For each: customer full name as written on the schedule or invoice (fix spelling from those), cash amount,',
    'the mover who collected it (first name as the office writes it, e.g. Carlos, Junior, David, Robert, Edin,',
    'William), the time it was collected (h:mm AM/PM, New York), and the total job cost: the sheet\'s "Total Cost" is',
    'the whole job price, usually the cash plus the $50 deposit, or the invoice\'s Line Item Total / Grand Total plus',
    'deposit. Give totalCost only when the evidence or the invoice supports it.',
    'status "verified" only if the customer is clearly identified, the amount and mover are clear, it was really',
    'collected, and totalCost is known. Otherwise "unclear", and say in "missing" exactly what to confirm.',
    'Also compare with the sheet rows given: if a row for that day contradicts the evidence (different amount or',
    'mover), list it in "conflicts". A payment already in the sheet with matching amount is still listed (status',
    'verified) - the code checks the sheet itself.',
    'Cite evidence by the ids shown in brackets.',
    'Answer with JSON only: {"payments":[{"customer":"","amount":0,"mover":"","time":"h:mm AM","totalCost":0|null,',
    '"status":"verified"|"unclear","missing":"","evidence":["id"],"summary":"<one short sentence>"}],',
    '"conflicts":[{"customer":"","sheet":"<what the sheet says>","evidence":"<what the evidence says>","ids":["id"]}]}',
  ].join(' ');
}

function cashBody({ day, jobs, texts, calls, slack, invoices, sheet }, model = 'claude-opus-5') {
  const sec = (title, lines) => `${title}:\n${lines.length ? lines.join('\n') : '(none)'}`;
  const user = [
    `Day: ${day} (${mdOf(day)})`,
    sec('Jobs on the schedule', jobs.map(j => `- ${j.title} at ${j.time}${j.crew ? ` (crew: ${j.crew})` : ''}`)),
    sec('Invoices', invoices.map(i => `- ${i.number} ${i.customer}: line items ${cashUsd(i.lineTotal || 0)}, deposit ${cashUsd(i.deposit || 0)}, `
      + `card fee ${i.fee ? cashUsd(i.fee) : 'none'}, balance ${cashUsd(i.balance || 0)}${i.cashLine ? ' (has a "Paid via Cash" line)' : ''}`)),
    sec('Cash Payments sheet rows already there for this month (name | date | time | mover | cash | total)',
      sheet.map(r => `- ${r.name} | ${r.day} | ${r.time} | ${r.mover} | ${r.cash == null ? '' : cashUsd(r.cash)} | ${r.total}`)),
    sec('Text messages from the crew', texts.map(t => `[sms:${t.id}] ${t.time} ${t.from}: ${t.text}`)),
    sec('Phone calls (transcripts, crew and office)', calls.map(c => `[call:${c.id}] ${c.time} ${c.who}: ${c.text}`)),
    sec('Slack #invoices posts', slack.map(s => `[slack:${s.id}] ${s.time}: ${s.text}`)),
  ].join('\n\n');
  return { model, max_tokens: 4000, system: cashSystem(), messages: [{ role: 'user', content: user }] };
}

function parseCash(res) {
  const txt = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  const m = txt.match(/\{[\s\S]*\}/);
  try { const j = JSON.parse(m ? m[0] : '{}'); return { payments: j.payments || [], conflicts: j.conflicts || [] }; }
  catch { return { payments: [], conflicts: [], error: 'could not read the AI answer' }; }
}

// Is this payment already in the sheet? Same day (±1), a shared name word, amount within $1.
function inSheet(p, rows, day) {
  const md = [mdOf(day), mdOf(new Date(Date.parse(day + 'T12:00:00Z') - 864e5).toISOString().slice(0, 10)),
              mdOf(new Date(Date.parse(day + 'T12:00:00Z') + 864e5).toISOString().slice(0, 10))];
  const words = new Set(cashWords(p.customer));
  return rows.find(r => md.includes(r.day.split('/').slice(0, 2).map(Number).join('/')) && r.cash != null && Math.abs(r.cash - +p.amount) <= 1
    && cashWords(r.name).some(w => words.has(w))) || null;
}

// Turn the AI's list into actions. `said` remembers what was already posted (key → time).
function cashActions(result, rows, day, said) {
  const add = [], review = [], conflict = [];
  const key = (...a) => a.map(x => String(x || '').toLowerCase().replace(/\s+/g, ' ').trim()).join('|');
  for (const p of result.payments || []) {
    if (!(+p.amount > 0)) continue;
    if (inSheet(p, rows, day)) continue;
    const k = key(day, p.customer, Math.round(+p.amount));
    if (said[k]) continue;
    said[k] = Date.now();
    const ok = p.status === 'verified' && p.customer && cashWords(p.customer).length >= 2 && p.mover && +p.totalCost > 0;
    if (ok) add.push({ ...p, _key: k, row: [p.customer, mdOf(day), p.time || '', p.mover, cashUsd(p.amount), cashUsd(p.totalCost), ''] });
    else review.push({ ...p, _key: k });
  }
  for (const c of result.conflicts || []) {
    const k = key(day, 'conflict', c.customer, c.sheet);
    if (said[k]) continue;
    said[k] = Date.now();
    conflict.push({ ...c, _key: k });
  }
  for (const [k, v] of Object.entries(said)) if (Date.now() - v > 45 * 864e5) delete said[k];
  return { add, review, conflict };
}

// ---- hourly reminders (client 2026-11-18: "a reminder every hour until they do it") ---------------
const DONE_HINT = '\nReact :white_check_mark: when done · :no_entry_sign: if it is not a problem · reminder every hour until then';
const DONE_REACTIONS = ['white_check_mark', 'heavy_check_mark', 'no_entry_sign', 'x'];

// What is tracked for each post, in the same order as cashMessages' texts.
function cashTracked({ add, review, conflict }, day) {
  const t = (type, p) => ({ key: p._key, type, day, tab: cashTab(day), customer: p.customer || '', amount: +p.amount || 0 });
  return [...add.map(p => t('add', p)), ...review.map(p => t('review', p)), ...conflict.map(c => t('conflict', c))];
}

// Done? A ✅/🚫 reaction on the post, or (for a payment) a matching row in the sheet now.
function cashDone(item, reactions, rows) {
  const r = (reactions || []).find(x => DONE_REACTIONS.includes(x.name));
  if (r) return { done: true, how: r.name === 'no_entry_sign' || r.name === 'x' ? 'marked not a problem' : 'marked done' };
  if (item.type !== 'conflict' && inSheet(item, rows || [], item.day)) return { done: true, how: 'now in the sheet' };
  return { done: false };
}

function cashReminder(item, n, who) {
  const what = item.type === 'add' ? 'please add this cash payment to the Cash Payments sheet'
    : item.type === 'review' ? 'please confirm this cash payment and add it to the sheet'
    : 'please check this against the Cash Payments sheet and correct it';
  return `:alarm_clock: *Reminder ${n}:* ${item.customer}${item.amount ? ` (${cashUsd(item.amount)})` : ''} is still open - ${who} ${what}. `
    + 'React :white_check_mark: on the message above when done, :no_entry_sign: if it is not a problem.';
}

function cashMessages({ add, review, conflict }, day, live, sheetUrl) {
  const out = [], ids = p => {
    // 2026-11-18: a conflict's "evidence" is a sentence and its sources are in "ids" - the run crashed on it
    const list = [p.ids, p.evidence].find(Array.isArray) || [];
    return list.slice(0, 6).map(x => `\`${x}\``).join(', ') || 'see the texts and #invoices';
  };
  const ping = live ? '<!channel> ' : '';
    // REPORT ONLY (client, 2026-11-17): the Cash Payments sheet is never changed by the automation
  for (const p of add) out.push(`${ping}:large_blue_circle: *Cash payment not in the Cash Payments sheet - please add it by hand:* ${p.customer} — ${cashUsd(p.amount)} cash `
    + `paid ${mdOf(day)}${p.time ? ` at ${p.time}` : ''} to ${p.mover}; total job cost ${cashUsd(p.totalCost)}. Sources: ${ids(p)} · <${sheetUrl}|sheet>${DONE_HINT}`);
  for (const p of review) out.push(`${ping}:large_yellow_circle: *Cash payment review needed:* ${p.summary || `${p.customer || 'A customer'} paid ${cashUsd(p.amount)} cash`} `
    + `${p.missing ? `— please confirm: ${p.missing}. ` : ''}No row was added. Sources: ${ids(p)}${DONE_HINT}`);
  for (const c of conflict) out.push(`${ping}:red_circle: *Does not match the Cash Payments sheet:* ${c.customer} — the sheet says ${c.sheet}, but ${c.evidence}. Please check which is right and correct it by hand. Sources: ${ids(c)} · <${sheetUrl}|sheet>${DONE_HINT}`);
  return out;
}

if (typeof module !== 'undefined') module.exports = { cashTracked, cashDone, cashReminder, DONE_REACTIONS, cashTab, cashRows, cashBody, parseCash, inSheet, cashActions, cashMessages, mdOf, cashSystem };
