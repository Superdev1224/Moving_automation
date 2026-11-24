// Invoice audit (client, 2026-10-02): beyond the math and the deposit, check the invoice against
//   - the signed estimate: hourly rate, crew size, minimum hours
//   - the crew lead's texts from the job: hours on site (start to finish minus breaks - that is how
//     the office bills: Maxine Cummings 8:10-13:40 = 5.5 h billed 5.5 h), extra crew, materials used.
// Needs parse-card.js (nothing) and crew-report.js (MATERIALS). Every number it reports is shown with
// both sides so the auditor can judge; only clear differences are "problems".

const NY = 'America/New_York';
const nyDayOf = ms => new Intl.DateTimeFormat('en-CA', { timeZone: NY }).format(new Date(ms));            // YYYY-MM-DD
const nyClock = ms => new Date(ms).toLocaleTimeString('en-US', { timeZone: NY, hour: '2-digit', minute: '2-digit', hour12: false });
const toMin = s => { const m = String(s || '').match(/(\d{1,2}):(\d{2})/); return m ? +m[1] * 60 + +m[2] : null; };
const hrs = h => `${Math.round(h * 100) / 100} h`;
const ampm = s => { const m = toMin(s); if (m == null) return '?'; const h = Math.floor(m / 60), mm = m % 60;
  return `${((h + 11) % 12) + 1}:${String(mm).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`; };
const lc = s => String(s || '').toLowerCase().replace(/[^a-z ]/g, ' ').replace(/\s+/g, ' ').trim();

// ---- labor on the invoice ------------------------------------------------------------------------
const isLabor = l => /hourly|per hour|move charges|hourly charges/i.test(l.desc)
  && !/travel|discount|container|stairs|truck|fuel/i.test(l.desc) && l.rate > 0;
const isFlat = l => /flat\s*rate/i.test(l.desc) && !/truck|fuel/i.test(l.desc);

function invoiceLabor(inv, estRate) {
  const lines = (inv.lines || []).filter(isLabor).map(l => {
    // "1 x $612.50" is the minimum written as one amount: 612.50 / $175 = 3.5 h
    const lump = l.qty === 1 && estRate > 0 && !/per hour/i.test(l.desc) && l.rate !== estRate;
    const hours = lump ? Math.round(l.amount / estRate * 100) / 100 : l.qty;
    const rate = lump ? estRate : l.rate;
    const men = +((l.desc.match(/(\d+)\s*men/i) || [])[1]) || null;
    return { hours, rate, men, lump, desc: l.desc };
  });
  return { lines, hours: lines.reduce((a, l) => a + l.hours, 0), flat: (inv.lines || []).some(isFlat) };
}

// ---- the signed estimate ----------------------------------------------------------------------------
// The minimum is the one in the customer's terms ("Estimate Hourly ($2 Mileage)(5 Hour Minimum)"): the
// pricing "min hours" box is sometimes filled with the expected hours instead (8 vs a 5-hour minimum).
function estimateTerms(est) {
  const t = (((est || {}).movePricing || {}).transportLabor || {});
  const d = (t.data || [])[0] || {};
  const m = String((est || {}).termTemplate || '').match(/\(\s*(\d+(?:\.\d+)?)\s*hours?\s*minimum\s*\)/i);
  return { id: est && est.displayId, hourly: t.transportLaborType === 'HOURLY_RATE' && +d.rate > 0,
    rate: +d.rate || 0, men: +d.men || 0, minHours: m ? +m[1] : +d.minHours || 0, start: +d.startDate || 0,
    made: +(est || {}).createdAt || +(est || {}).sentAt || 0 };
}

// Policy (the rules the client's ChatGPT reviewer applied in #invoices, Oct-Nov 2026):
//   - labor is billed in quarter hours, rounded UP: 2h55 → 3.0, 5h20 → 5.5 (start to finish minus breaks)
//   - 3 movers are $195/hour on estimates made since Oct 3, unless an exception was approved
const RATE_STANDARD = { men: 3, rate: 195, since: Date.UTC(2026, 9, 3, 4) };
const quarterUp = h => Math.ceil(Math.round(h * 600) / 150) / 4;            // 2.9167 → 3.0 (minute-precise)
const isQuarter = h => Math.abs(h * 4 - Math.round(h * 4)) < 0.01;
const usd2 = n => '$' + (Math.round(n * 100) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// The customer's signed estimate for this move: same email, signed, closest move date before the invoice.
function pickEstimate(estimates, email, invoiceMs) {
  const mine = (estimates || []).filter(e => !e.isDeleted && String(e.status) === 'Signed'
    && String(((e.contacts || [])[0] || {}).email || '').toLowerCase() === String(email || '').toLowerCase());
  return mine.sort((a, b) => Math.abs(invoiceMs - (+a.sentAt || 0)) - Math.abs(invoiceMs - (+b.sentAt || 0)))[0] || null;
}

// ---- which job on the schedule this invoice is for ----------------------------------------------------
const shiftText = s => [s.title, ...(s.notes || []).map(n => n.html || '')].join(' ').toLowerCase();
function findJob(shifts, customer, email, invoiceMs) {
  const name = lc(customer), first = name.split(' ')[0], last = name.split(' ').slice(-1)[0];
  const byJob = new Map();
  for (const s of shifts || []) {
    const k = `${s.title}|${s.startTime}`;
    const j = byJob.get(k) || { title: s.title, start: s.startTime * 1000, end: s.endTime * 1000, users: new Set(), text: shiftText(s) };
    (s.assignedUserIds || []).forEach(u => j.users.add(u));
    byJob.set(k, j);
  }
  const jobs = [...byJob.values()].filter(j => j.start <= invoiceMs + 86400000);
  const score = j => (email && j.text.includes(String(email).toLowerCase()) ? 3 : 0)
    + (first && lc(j.title).includes(first) ? 1 : 0) + (last && last !== first && lc(j.title).includes(last) ? 1 : 0);
  const best = jobs.map(j => ({ j, s: score(j) })).filter(x => x.s >= 2 || (x.s === 1 && first === last))
    .sort((a, b) => b.s - a.s || b.j.start - a.j.start)[0];
  if (!best) return null;
  const job = best.j, day = nyDayOf(job.start);
  const sameDay = [...byJob.values()].filter(j => nyDayOf(j.start) === day).sort((a, b) => a.start - b.start);
  const crew = [...job.users];
  const theirJobs = crew.length ? sameDay.filter(j => [...j.users].some(u => job.users.has(u))) : sameDay;
  return { title: job.title, day, start: job.start, crew, dayJobs: theirJobs.map(j => ({ title: j.title, time: nyClock(j.start) })) };
}

// ---- materials ------------------------------------------------------------------------------------------
function materialOf(desc) {
  const d = String(desc).toLowerCase();
  if (/tape/.test(d)) return 'Tape';
  if (/shrink|stretch/.test(d)) return 'Shrink Wrap';
  if (/bubble/.test(d)) return 'Bubble Wrap';
  if (/paper/.test(d)) return 'Packing Paper';
  if (/mattress/.test(d)) return 'Mattress Bag';
  if (/wardrobe/.test(d)) return 'Wardrobe Box';
  if (/dish/.test(d)) return 'Dish Pack';
  if (/tv/.test(d) && /box/.test(d)) return 'TV Box';
  if (/blanket|pad/.test(d)) return 'Blanket';
  if (/box/.test(d)) return /x-?large|extra large/.test(d) ? 'Extra Large Box' : /large/.test(d) ? 'Large Box'
    : /small/.test(d) ? 'Small Box' : 'Medium Box';
  return null;
}
function invoiceMaterials(inv) {
  const out = {};
  for (const l of inv.lines || []) {
    const m = materialOf(l.desc);
    if (!m || l.rate <= 0) continue;
    const q = /half|little/i.test(l.desc) && l.qty === 1 ? 0.5 : l.qty;
    out[m] = (out[m] || 0) + q;
  }
  return out;
}

// ---- the audit --------------------------------------------------------------------------------------------
function auditInvoice({ inv, est, report, job }) {
  const problems = [], review = [], notes = [], lines = [];
  const terms = estimateTerms(est);
  const labor = invoiceLabor(inv, terms.rate);

  // 0) quarter hours: 2.33 or 4.67 hours is not a billable amount
  for (const l of labor.lines) {
    if (l.lump || isQuarter(l.hours)) continue;
    const up = quarterUp(l.hours);
    problems.push(`${l.hours} hours is not a quarter hour: billing rounds up to ${up} h `
      + `(${usd2(up * l.rate)} instead of ${usd2(l.hours * l.rate)}, +${usd2((up - l.hours) * l.rate)})`);
  }
  // 0b) the 3-mover rate standard on new estimates
  const rate = terms.rate || (labor.lines[0] || {}).rate || 0;
  const men = terms.men || (labor.lines[0] || {}).men || 0;
  // Invoice Auditor spec 5: a negotiated rate below the reference price is NOT underbilling - check disabled
  if (false && men === RATE_STANDARD.men && rate && rate < RATE_STANDARD.rate && !labor.flat
      && (!est || terms.made >= RATE_STANDARD.since))
    review.push(`$${rate}/hr for ${men} movers${est ? ` on estimate ${terms.id}` : ''}: the standard since Oct 3 is `
      + `$${RATE_STANDARD.rate}/hr - was a lower rate approved?`);

  // 1) the signed estimate
  if (est && terms.hourly && labor.lines.length && !labor.flat) {
    // Client rule (pricing rules, 2026-11-20 Isa Cejku): an additional mover is +$50/hour, sometimes negotiated
    // to +$40/hour, on top of the crew rate - $245/hr for 4 men + 1 mover = $295/hr for 5 men is correct.
    const crewChange = l => {
      const dr = l.rate - terms.rate;
      if (Math.abs(dr) < 0.01) return null;
      const dm = l.men && terms.men ? l.men - terms.men : 0;
      if (dm) { const per = dr / dm; return per >= 39.99 && per <= 50.01 ? { dm, per, guessed: false } : null; }
      for (const k of [1, 2, 3]) for (const per of [50, 40]) if (Math.abs(Math.abs(dr) - k * per) < 0.01) return { dm: Math.sign(dr) * k, per, guessed: true };
      return null;
    };
    const wrong = labor.lines.filter(l => Math.abs(l.rate - terms.rate) > 0.01 && !crewChange(l));
    const changed = labor.lines.filter(l => crewChange(l));
    if (wrong.length) problems.push(`Billed ${wrong.map(l => `$${l.rate}/hr${l.men ? ` (${l.men} men)` : ''}`).join(' and ')}, `
      + `but the signed estimate ${terms.id} says $${terms.rate}/hr for ${terms.men} men`
      + ' (and the difference is not an extra mover at +$40-50/hr)');
    else if (!changed.length) lines.push(`Rate $${terms.rate}/hr × ${terms.men} men matches estimate ${terms.id}`);
    for (const l of changed) {
      const c = crewChange(l), n = Math.abs(c.dm), word = n === 1 ? 'mover' : 'movers';
      lines.push(`Rate $${l.rate}/hr${l.men ? ` (${l.men} men)` : ''} = estimate ${terms.id} $${terms.rate}/hr (${terms.men} men) `
        + `${c.dm > 0 ? '+' : '-'} ${n} ${word} at $${c.per}/hr - allowed`);
      if (c.guessed) notes.push(`The invoice does not say how many men; $${l.rate}/hr fits ${n} ${c.dm > 0 ? 'extra' : 'fewer'} ${word} at $${c.per}/hr`);
      if (c.dm > 0 && !(report && report.extraCrew)) notes.push(`Check the extra ${word} worked the whole time: the +$${c.per}/hr only applies while they were there`);
    }
    if (terms.minHours && labor.hours + 0.01 < terms.minHours)
      problems.push(`Only ${hrs(labor.hours)} billed, but the estimate has a ${terms.minHours}-hour minimum`);
    else if (terms.minHours) lines.push(`${terms.minHours}-hour minimum respected (${hrs(labor.hours)} billed)`);
  } else if (labor.flat) notes.push('Flat-rate job: hours and rate not compared');
  // a discount on the labor itself is worth a look (Alisa Ormova: $350 minimum, -$225 labor discount)
  const laborDiscount = (inv.lines || []).filter(l => /discount/i.test(l.desc) && /transport|labor|move/i.test(l.desc) && l.amount < 0)
    .reduce((a, l) => a + l.amount, 0);
  if (laborDiscount) notes.push(`Labor discount of $${Math.abs(laborDiscount).toFixed(2)} on this invoice`);
  else if (!est) notes.push('No signed estimate found to compare the rate with');

  // 2) the crew's report from the job
  if (!job) notes.push('Job not found on the Connecteam schedule, so the crew report was not checked');
  else if (!report) notes.push(`No crew report found in the texts for this job (${job.day})`);
  else {
    const s = toMin(report.start), e = toMin(report.end), brk = +report.breakMinutes || 0;
    const head = `Crew report: ${report.start ? `started ${ampm(report.start)}` : 'no start time'}`
      + `${report.end ? `, ${report.endExact ? 'finished' : 'expected to finish'} ${ampm(report.end)}` : ''}`
      + `${brk ? `, ${brk} min break` : ''}${report.extraCrew ? `, ${report.extraCrew}` : ''}`;
    if (s != null && e != null && labor.lines.length && !labor.flat) {
      // start to finish minus breaks, rounded UP to the quarter hour, never below the minimum
      const onsite = (e - s - brk) / 60;
      const due = Math.max(quarterUp(onsite), terms.minHours || 0);
      const diff = Math.round((labor.hours - due) * 100) / 100;
      const r = (labor.lines[0] || {}).rate || terms.rate || 0;
      lines.push(`${head} = ${hrs(onsite)} on site → ${hrs(due)} to bill${terms.minHours && due === terms.minHours ? ' (minimum)' : ''} · ${hrs(labor.hours)} billed${labor.lines.some(l => l.lump) ? ` (as one amount, ${usd2(labor.hours * r)})` : ''}`);
      const money = r ? ` (${diff < 0 ? 'under' : 'over'}charged ${usd2(Math.abs(diff) * r)})` : '';
      // 2026-11-18 (Jennifer IN001011): the invoice had no hours, only "Estimated Move Charges 1 x $437.50",
      // and "2.5 h billed" read as if someone had typed 2.5 hours. Say what is really on the invoice.
      const lump = labor.lines.find(l => l.lump);
      const billedTxt = lump ? `The invoice bills ${usd2(labor.hours * r)} of labor ("${(String(lump.desc).match(/(estimated )?(move|hourly) charges/i) || [lump.desc])[0]}" = ${hrs(labor.hours)} at ${usd2(r)}/hr)`
        : `${hrs(labor.hours)} billed`;
      const sameAsEst = lump && est && +est.total > 0 && Math.abs(+est.total - (+inv.lineTotal || 0)) < 0.01
        ? ` - the invoice is the same amount as estimate ${est.displayId}, so the real hours may not have been put on it` : '';
      const say = `${billedTxt}, but the crew's times give ${hrs(due)}${money}${sameAsEst}`;
      if (Math.abs(diff) >= 0.25) (report.endExact ? problems : review).push(say + (report.endExact ? '' : ' - the crew only gave an estimated finish time'));
      else if (!report.endExact) notes.push('The crew only gave an estimated finish time, so the hours are approximate');
    } else lines.push(head);
    if (report.extraCrew) notes.push(`Extra crew reported (${report.extraCrew}) - check it is billed`);

    // materials, both ways
    const billed = invoiceMaterials(inv), used = {};
    for (const m of report.materials || []) if (m && m.item && +m.qty > 0) used[m.item] = (used[m.item] || 0) + +m.qty;
    const names = [...new Set([...Object.keys(used), ...Object.keys(billed)])];
    const missing = [], extra = [];
    for (const n of names) {
      const u = used[n] || 0, b = billed[n] || 0, tol = n === 'Shrink Wrap' ? 0.5 : 0;
      if (u - b > tol) missing.push(`${u - b} ${n}`);
      else if (b - u > tol && Object.keys(used).length) extra.push(`${b - u} ${n}`);
    }
    // a flat rate usually includes the packing materials (Jodi Cortes, 11/6): only a note there
    if (missing.length) (labor.flat ? notes : review).push(`Materials the crew used but not billed: ${missing.join(', ')}${labor.flat ? ' (flat-rate job - probably included)' : ''}`);
    if (extra.length) review.push(`Materials billed but not in the crew's report: ${extra.join(', ')}`);
    // 2026-11-18 (Alina Bowe): with nothing listed by the crew, billed materials were reported as "matching"
    const billedList = Object.keys(billed).map(n => `${billed[n]} ${n}`).join(', ');
    if (!Object.keys(used).length && Object.keys(billed).length) {
      if (report.noMaterials) review.push(`Materials billed (${billedList}), but the crew's report says no materials were used`);
      else notes.push(`The crew did not list materials, so the billed materials (${billedList}) were not checked`);
    } else if (!missing.length && !extra.length && names.length) lines.push(`Materials match the crew's report (${names.map(n => `${used[n] || billed[n]} ${n}`).join(', ')})`);
    if (report.noMaterials && !names.length) lines.push('No materials used or billed');
  }
  return { problems, review, notes, lines };
}

if (typeof module !== 'undefined') module.exports = { auditInvoice, invoiceLabor, estimateTerms, pickEstimate, findJob, invoiceMaterials, materialOf, nyDayOf, quarterUp, isQuarter };
