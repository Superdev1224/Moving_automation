// Reads the text of a Movegistics invoice PDF and checks its math (items #5 / #12).
// Checks: qty × rate = amount per line; lines add up to "Line Item Total"; the convenience fee
// matches its % (Movegistics applies it either before or after the deposit, both are accepted
// but reported); Total / Grand Total / Balance Due follow from those numbers.

const money = s => (s == null ? null : parseFloat(String(s).replace(/[$,\s]/g, '')));
const usd = n => (n == null ? '?' : (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const near = (a, b) => a != null && b != null && Math.abs(a - b) <= 0.011;

function parseInvoice(text) {
  const t = text.replace(/\r/g, '');
  const L = t.split('\n').map(l => l.trim()).filter(Boolean);
  const after = label => { const i = L.findIndex(l => l.startsWith(label)); return i < 0 ? null : (L[i].slice(label.length).trim() || L[i + 1] || null); };
  const num = re => { const m = t.match(re); return m ? money(m[1]) : null; };

  const billTo = (() => { const i = L.indexOf('BILL TO'); return i < 0 ? '' : L[i + 1] || ''; })();
  const email = (billTo.match(/[\w.+-]+@[\w-]+\.[\w.]+/) || [''])[0];
  const inv = {
    number: (t.match(/Invoice #\s*(IN\d+)/) || [])[1] || null,
    date: (t.match(/Invoice Date\s*([\d/]+)/) || [])[1] || null,
    customer: billTo.replace(email, '').trim() || null,
    email: email || null,
    quote: after('Quote'),
    lineTotal: num(/Line Item Total\s*\$?(-?[\d,]+\.\d\d)/),
    discount: num(/Discount:[^\n]*\(-\s*\$(-?[\d,]+\.\d\d)\)/),
    subTotal: num(/Sub Total\s*\$?(-?[\d,]+\.\d\d)/),
    deposits: [...t.matchAll(/\(PT\d+\)\s*[—-]\s*\$(-?[\d,]+\.\d\d)/g)].map(m => money(m[1])),
    feePct: num(/Convenience Fees?\s*([\d.]+)\s*%/),
    fee: num(/Convenience Fees?[^\n]*\(\+\s*\$(-?[\d,]+\.\d\d)\)/),
    total: num(/\nTotal\s*\n?\s*\$(-?[\d,]+\.\d\d)/),
    grandTotal: num(/Grand Total\s*\n?\s*\$(-?[\d,]+\.\d\d)/),
    balance: num(/Balance Due:\s*\$(-?[\d,]+\.\d\d)/),
    paymentReceived: num(/Payment Received:\s*\$(-?[\d,]+\.\d\d)/),
    lines: [],
  };
  if (!inv.deposits.length) { const d = num(/Deposit Received:\s*\$(-?[\d,]+\.\d\d)/); if (d) inv.deposits = [d]; }

  // Line items: rows end with "<unit> <qty> $<rate> $<amount>"; the description may span earlier lines.
  const start = L.findIndex(l => /^#\s*Service/.test(l));
  const end = L.findIndex(l => /^Line Item Total/.test(l));
  let desc = [];
  for (const l of L.slice(start + 1, end < 0 ? undefined : end)) {
    // an item-level discount sits between rate and amount: "3.85 $195.00 10.00% $675.67"
    const m = l.match(/^(.*?)\s*(-?[\d.]+)\s+\$(-?[\d,]+\.\d\d)\s+(?:(-?[\d.]+)\s*(%)?\s+)?\$(-?[\d,]+\.\d\d)$/);
    if (m) {
      const text = [...desc, m[1]].join(' ').replace(/^\d+\s*-?\s*/, '').replace(/\s+/g, ' ').trim();
      inv.lines.push({ desc: text, qty: +m[2], rate: money(m[3]), amount: money(m[6]),
        ...(m[4] ? (m[5] ? { discPct: +m[4] } : { discAmt: +m[4] }) : {}) });
      desc = [];
    } else desc.push(l);
  }
  return inv;
}

function checkInvoice(inv) {
  const problems = [], notes = [];
  if (!inv.lines.length || inv.lineTotal == null) {
    return { ok: false, problems: ['Could not read the invoice lines. Please check it by hand.'], notes };
  }
  for (const l of inv.lines) {
    // valuation coverage is priced "per $100": $2,000 of cover at $3.50 per $100 is $70, not $7,000
    const per = /per \$?100\b/i.test(l.desc) ? 100 : 1;
    const keep = 1 - (l.discPct || 0) / 100;
    const want = l.qty * l.rate / per * keep - (l.discAmt || 0);
    const off = l.discPct ? ` − ${l.discPct}%` : l.discAmt ? ` − ${usd(l.discAmt)}` : '';
    if (Math.abs(want - l.amount) > (l.discPct ? 0.015 : 0.011)) problems.push(`Line "${l.desc}": ${l.qty}${per > 1 ? ' / 100' : ''} × ${usd(l.rate)}${off} = ${usd(want)}, but invoice says ${usd(l.amount)}`);
    else if (off) notes.push(`${l.discPct ? l.discPct + '%' : usd(l.discAmt)} discount on "${l.desc}"`);
  }
  const sum = inv.lines.reduce((a, l) => a + l.amount, 0);
  if (!near(sum, inv.lineTotal)) problems.push(`Lines add up to ${usd(sum)}, but Line Item Total says ${usd(inv.lineTotal)}`);

  const dep = inv.deposits.reduce((a, b) => a + b, 0);
  // an invoice-level discount turns Line Item Total into Sub Total; fees/totals work from that
  let lt = inv.lineTotal;
  if (inv.discount) {
    lt = Math.round((inv.lineTotal - inv.discount) * 100) / 100;
    if (inv.subTotal != null && !near(inv.subTotal, lt)) problems.push(`Sub Total should be ${usd(lt)} (after ${usd(inv.discount)} discount), invoice says ${usd(inv.subTotal)}`);
    notes.push(`Discount ${usd(inv.discount)} applied`);
  }
  let expectedGrand = lt - dep;
  if (inv.fee != null && inv.feePct != null) {
    const pct = inv.feePct / 100;
    const onFull = Math.round(lt * pct * 100) / 100, onNet = Math.round((lt - dep) * pct * 100) / 100;
    if (near(inv.fee, onFull)) {
      // company rule (Invoice Auditor spec, 2026-11-16): 3.5% of the card-funded FULL moving cost, deposit included
      if (dep) notes.push(`Card fee on the full ${usd(lt)} (deposit included) - correct`);
      expectedGrand = lt + inv.fee - dep;
      if (inv.total != null && !near(inv.total, lt + inv.fee)) problems.push(`Total should be ${usd(lt + inv.fee)}, invoice says ${usd(inv.total)}`);
    } else if (near(inv.fee, onNet)) {
      // fee left the deposit out: under by 3.5% of the deposit ($1.75) - routine noise under the $3 rule
      notes.push(`Card fee on ${usd(lt - dep)} only (the ${usd(dep)} deposit left out: ${usd(onFull - onNet)} under, ignored as under $3)`);
      expectedGrand = lt - dep + inv.fee;
      if (inv.total != null && !near(inv.total, lt - dep)) problems.push(`Total should be ${usd(lt - dep)}, invoice says ${usd(inv.total)}`);
    } else if (Math.abs(inv.fee - onFull) <= 3) {
      notes.push(`Card fee ${usd(inv.fee)} vs ${usd(onFull)} expected (${inv.feePct}% of ${usd(lt)}): under $3, ignored`);
    } else {
      problems.push(`Card fee ${usd(inv.fee)} should be ${inv.feePct}% of the card-paid ${usd(lt)} = ${usd(onFull)} (difference ${usd(inv.fee - onFull)}) - or part was paid in cash?`);
      expectedGrand = lt - dep + inv.fee;
    }
  }
  if (inv.grandTotal != null && !near(inv.grandTotal, expectedGrand)) problems.push(`Grand Total should be ${usd(expectedGrand)}, invoice says ${usd(inv.grandTotal)}`);
  const explained = inv.paymentReceived && inv.balance != null && inv.grandTotal != null
    && near(inv.balance, inv.grandTotal - inv.paymentReceived);              // the payment shown on the invoice
  if (!explained && inv.balance != null && inv.grandTotal != null && !near(inv.balance, inv.grandTotal)) notes.push(`Balance Due ${usd(inv.balance)} differs from Grand Total ${usd(inv.grandTotal)} (other payments?)`);
  return { ok: problems.length === 0, problems, notes };
}

function invoiceSlackText(inv, chk, audit) {
  const dep = inv.deposits.reduce((a, b) => a + b, 0);
  let s = `:page_facing_up: *Invoice ${inv.number || '?'}*: ${inv.customer || 'Unknown customer'}${inv.email ? ` (${inv.email})` : ''}\n`;
  if (inv.date) s += `*Date:* ${inv.date}\n`;
  s += `*Items:* ${usd(inv.lineTotal)}`;
  if (inv.discount) s += `  ·  *Discount:* −${usd(inv.discount)}`;
  if (dep) s += `  ·  *Deposit:* −${usd(dep)}`;
  if (inv.fee) s += `  ·  *Card fee (${inv.feePct}%):* ${usd(inv.fee)}`;
  s += `\n*Balance due:* ${usd(inv.balance ?? inv.grandTotal)}\n`;
  const hours = inv.lines.filter(l => /per hour|hourly rate|move charges/i.test(l.desc) && !/tape|container|box|wrap|discount|travel/i.test(l.desc) && l.rate > 0);
  // "1 x $612.50" is a minimum written as one amount, not 1 hour
  if (hours.length) s += `*Labor:* ${hours.map(l => (l.qty === 1 && !/per hour/i.test(l.desc) ? `${usd(l.amount)} (as one amount)` : `${l.qty} hrs × ${usd(l.rate)}`)).join(', ')}\n`;
  const problems = [...chk.problems, ...((audit && audit.problems) || [])];
  const review = (audit && audit.review) || [];
  const notes = [...chk.notes, ...((audit && audit.notes) || [])];
  const checked = audit ? 'Math, deposit, card fee, rate, hours and materials check out' : 'Math and deposit check out';
  if (problems.length) s += `:red_circle: *Billing error:*\n${problems.map(p => `• ${p}`).join('\n')}`;
  if (review.length) s += `${problems.length ? '\n' : ''}:large_yellow_circle: *Needs review:*\n${review.map(p => `• ${p}`).join('\n')}`;
  if (!problems.length && !review.length) s += `:white_check_mark: ${checked}`;
  if (audit && audit.lines && audit.lines.length) s += `\n${audit.lines.map(l => `› ${l}`).join('\n')}`;
  if (notes.length) s += `\n_${notes.join('; ')}_`;
  return s;
}

// The deposit check (client, 2026-09-30 / 10-01 / 10-02). The customer pays $52 = a $50 deposit + a $2
// card fee, and the invoice takes off ONLY the $50. Since Oct 1 the team takes it off as a "$50
// Discount" rather than as a payment, which is fine; since Oct 3 also as a "Deposit Actuals -$50" line
// item. Wrong: no deposit taken off, the full $52 taken off (the $2 card fee is not refundable), any
// other amount, or the deposit taken off twice. A deposit paid long before the invoice belongs to an
// earlier move (e.g. a storage bill months later), so it is only noted.
const DEPOSIT_CREDIT = 50;
const DEPOSIT_MAX_AGE_DAYS = 120;
function checkDeposit(inv, estimates) {
  const problems = [], notes = [];
  const email = String(inv.email || '').toLowerCase();
  const mine = (estimates || []).filter(e => !e.isDeleted
    && (!email || String(((e.contacts || [])[0] || {}).email || '').toLowerCase() === email));
  const paid = mine.filter(e => e.deposit && e.deposit.isPaid)
    .sort((a, b) => (+b.updatedAt || 0) - (+a.updatedAt || 0))[0];
  const asPayment = (inv.deposits || []).reduce((a, b) => a + b, 0) + (+inv.paymentReceived || 0);
  if (!mine.length) {
    notes.push('No Movegistics estimate found for this customer, so the deposit was not checked');
    return { problems, notes };
  }
  if (!paid) {
    if (asPayment) notes.push(`${usd(asPayment)} taken off, but Movegistics shows no deposit paid on the estimate - paid another way?`);
    return { problems, notes };
  }
  const amount = parseFloat(paid.deposit.total) || 0;                       // what they paid, e.g. $52
  const fee = Math.max(0, Math.round((amount - DEPOSIT_CREDIT) * 100) / 100); // e.g. $2 card fee
  const looksLikeDeposit = x => near(x, DEPOSIT_CREDIT) || near(x, amount);
  const where = `estimate ${paid.displayId}`;
  const paidAt = +paid.deposit.paymentDate || +paid.signedAt || +paid.updatedAt || 0;
  const [mm, dd, yy] = String(inv.date || '').split('/').map(Number);
  const invAt = yy ? Date.UTC(yy, mm - 1, dd) : Date.now();
  if (paidAt && invAt - paidAt > DEPOSIT_MAX_AGE_DAYS * 864e5) {
    notes.push(`The only deposit in Movegistics is from ${where} (${new Date(paidAt).toLocaleDateString('en-US', { timeZone: 'America/New_York' })}), `
      + 'an earlier move, so the deposit was not checked');
    return { problems, notes };
  }
  // the deposit as a line item ("Deposit Actuals -$50") or a "$50 Discount" line, besides the invoice-level discount
  const lineOff = re => (inv.lines || []).filter(l => l.amount < 0 && re(l)).reduce((a, l) => a - l.amount, 0);
  const asLine = Math.round(lineOff(l => /deposit/i.test(l.desc)) * 100) / 100;
  const discLine = lineOff(l => /discount/i.test(l.desc) && !/deposit|item level/i.test(l.desc) && looksLikeDeposit(-l.amount));
  const asDiscount = (inv.discount && looksLikeDeposit(inv.discount) ? inv.discount : 0) + discLine;
  const judge = (x, how) => {
    if (near(x, DEPOSIT_CREDIT)) return notes.push(`Deposit ${usd(DEPOSIT_CREDIT)} taken off ${how}`
      + (fee ? `; the other ${usd(fee)} was the card fee` : '') + ` (${where})`);
    // client 2026-11-17 (2nd answer): the deposit is $50, there is no extra $2 - the 3.5% fee is on the combined
    // total, so an invoice that takes off $52 credits $2 too much (always flagged, not $1-3 noise)
    problems.push(`The invoice takes off ${usd(x)} ${how}, but the deposit to take off is ${usd(DEPOSIT_CREDIT)} (${where})`);
  };
  const ways = [[asPayment, 'received'], [asLine, 'deposit line'], [asDiscount, 'discount']].filter(w => w[0]);
  if (ways.length > 1) {
    problems.push(`The deposit may be taken off twice: ${ways.map(([x, w]) => `${usd(x)} ${w}`).join(' AND ')} (${where}).`
      + (asDiscount ? ` If the ${usd(asDiscount)} discount is a separate promotion, ignore this.` : ''));
  } else if (asPayment) judge(asPayment, 'as a payment');
  else if (asLine) judge(asLine, 'as a line item');
  else if (asDiscount) judge(asDiscount, 'as a discount');
  else problems.push(`The customer paid a ${usd(amount)} deposit (${where}), but this invoice does not take off the ${usd(DEPOSIT_CREDIT)}`);
  return { problems, notes };
}

// One verdict for the post: the invoice's own math plus the deposit.
function mergeChecks(chk, dep) {
  const problems = [...chk.problems, ...dep.problems];
  // a $50 discount that IS the deposit is said once, by the deposit note
  const depIsDiscount = dep.notes.some(n => /as a discount/.test(n)) || dep.problems.some(n => /taken off twice/.test(n));
  const notes = [...chk.notes.filter(n => !(depIsDiscount && /^Discount .* applied$/.test(n))), ...dep.notes];
  return { ok: problems.length === 0, problems, notes };
}

if (typeof module !== 'undefined') module.exports = { parseInvoice, checkInvoice, checkDeposit, mergeChecks, invoiceSlackText };
