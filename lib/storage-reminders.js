// Storage payment reminders (#28, 2026-11-16) — replaces the client's other AI tool, which posted these in
// #invoices until 10/21. Same rules it used (client PDF "AI Automations", item 3):
//   - "Paid Up To" in the Customers With Storage tracker is the due date
//   - remind 7, 3 and 1 day before it (and on the day itself)
//   - overdue customers are grouped on Mondays only, so once a week
// A customer paid up to their Storage End Date owes nothing more: they are listed as "storage ends"
// on the same 7/3/1 days so the team books the delivery instead of chasing a payment.

const REMIND_DAYS = [7, 3, 1, 0];
const TRACKER_URL = 'https://docs.google.com/spreadsheets/d/1gwG1fFk8Jr2KNI8ur_bo67pUs49u82W7/edit';

// Excel stores dates as day numbers since 1899-12-30; people sometimes type "11/22" or "Paid up to 11/22/2026".
function storageDate(v, todayIso) {
  if (v === undefined || v === null || v === '') return '';
  if (typeof v === 'number' || /^\d{5}(\.\d+)?$/.test(String(v).trim())) {
    const d = new Date(Date.UTC(1899, 11, 30) + Math.floor(Number(v)) * 86400000);
    return d.toISOString().slice(0, 10);
  }
  const s = String(v);
  let m = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`;
  m = s.match(/(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?/);
  if (!m) return '';
  let year = m[3] ? Number(m[3].length === 2 ? '20' + m[3] : m[3]) : Number(todayIso.slice(0, 4));
  const iso = y => `${y}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}`;
  // no year typed: the nearest one (a date 6+ months back in December is next year's)
  if (!m[3] && daysBetween(todayIso, iso(year)) < -183) year += 1;
  return iso(year);
}

function daysBetween(fromIso, toIso) {
  return Math.round((Date.parse(toIso + 'T00:00:00Z') - Date.parse(fromIso + 'T00:00:00Z')) / 86400000);
}

function etToday(now) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(now);
}

function niceDate(iso) {
  const d = new Date(iso + 'T12:00:00Z');
  return d.toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

function money(n) {
  const v = Number(String(n ?? '').replace(/[$,\s]/g, ''));
  return Number.isFinite(v) && v > 0 ? '$' + v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : '';
}

// Rows come from the "Storage Tracker" tab, header on row 10. The tab ends with a "HOW TO USE" block.
function storageCustomers(rows, todayIso) {
  const out = [];
  for (const r of rows) {
    const name = String(r['Customer'] || '').trim();
    if (!name) continue;
    if (/^how to use/i.test(name)) break;
    if (name.startsWith('•')) continue;
    const status = String(r['Status'] || '').trim();
    out.push({
      name, status,
      paidTo: storageDate(r['Paid Up To'], todayIso),
      endDate: storageDate(r['Storage End Date'], todayIso),
      fee: money(r['Monthly Fee']),
      units: String(r['Storage Unit #s'] || '').trim(),
      facility: String(r['Storage Facility Address'] || '').trim(),
    });
  }
  return out;
}

function storageReminders(customers, todayIso) {
  const monday = new Date(todayIso + 'T12:00:00Z').getUTCDay() === 1;
  const upcoming = [], ending = [], overdue = [], noDate = [];
  for (const c of customers) {
    if (/^ended$/i.test(c.status)) continue;
    if (c.endDate && daysBetween(todayIso, c.endDate) < 0 && (!c.paidTo || c.paidTo >= c.endDate)) continue;   // gone
    if (!c.paidTo) { if (monday) noDate.push(c); continue; }
    const days = daysBetween(todayIso, c.paidTo);
    const paidToEnd = c.endDate && c.paidTo >= c.endDate;
    if (paidToEnd) {
      const toEnd = daysBetween(todayIso, c.endDate);
      if (REMIND_DAYS.includes(toEnd)) ending.push({ ...c, days: toEnd });
    } else if (REMIND_DAYS.includes(days)) upcoming.push({ ...c, days });
    else if (days < 0 && monday) overdue.push({ ...c, days });
  }
  overdue.sort((a, b) => a.days - b.days);
  return { upcoming, ending, overdue, noDate };
}

function inDays(d) {
  return d === 0 ? 'due TODAY' : `due in ${d} day${d === 1 ? '' : 's'}`;
}

function storageMessage(r, todayIso, live) {
  const total = r.upcoming.length + r.ending.length + r.overdue.length + r.noDate.length;
  if (!total) return '';
  const extra = c => [c.fee && `Monthly fee: ${c.fee}`, c.units].filter(Boolean).join(' · ');
  const lines = [];
  if (live) lines.push('<!channel>');
  else lines.push(':test_tube: _Preview - this is what #invoices would get today_');
  lines.push(`*Storage payment reminders — ${niceDate(todayIso)}*`);
  if (r.upcoming.length) {
    lines.push('', '*Payment coming up*');
    for (const c of r.upcoming) lines.push(`• ${c.name} — Paid up to ${niceDate(c.paidTo)} — ${inDays(c.days)} — ${extra(c)}`);
  }
  if (r.overdue.length) {
    lines.push('', '*Overdue* (listed on Mondays)');
    for (const c of r.overdue) lines.push(`• ${c.name} — Paid up to ${niceDate(c.paidTo)} — *${-c.days} day${c.days === -1 ? '' : 's'} overdue* — ${extra(c)}`);
  }
  if (r.ending.length) {
    lines.push('', '*Storage ending* (paid through the end, nothing to collect - make sure the delivery is booked)');
    for (const c of r.ending) lines.push(`• ${c.name} — ends ${niceDate(c.endDate)}${c.days === 0 ? ' (today)' : ` (in ${c.days} day${c.days === 1 ? '' : 's'})`} — ${c.units || c.facility}`);
  }
  if (r.noDate.length) {
    lines.push('', '*No "Paid Up To" date in the tracker* - please fill it in');
    for (const c of r.noDate) lines.push(`• ${c.name}`);
  }
  lines.push('', `<${TRACKER_URL}|Open storage tracker>`);
  return lines.join('\n');
}

if (typeof module !== 'undefined') module.exports = { storageDate, storageCustomers, storageReminders, storageMessage, etToday, daysBetween };
