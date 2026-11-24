// The customer who only exists in Movegistics (asked for on 2026-09-29).
//
// Nearly every Movegistics customer reaches the board as a lead first - 185 of the 188 in the three
// weeks to 2026-09-29 already had a card. The handful that do not are the ones somebody typed
// straight into Movegistics, and they are invisible to everything that runs off ClickUp: the
// follow-up reminders, the booking note, the day-before confirmation, the lost-lead reporting.
// This makes their card, filled from the work request, so they are treated like any other lead.

const MG_CARD_DAYS = 7;      // work requests opened in the last week; anything older is history
// The team's own habit for phone leads is to open the work request in Movegistics first and make the
// ClickUp card by hand a little later: of the 100 work requests to 2026-10-04, 30 got a card after,
// 26 of them within 30 minutes and 4 between 40 and 109 minutes. Until 2026-10-05 we waited 3 hours
// so as never to beat them to it; the client wanted the card sooner, so we now wait 30 minutes and
// the few the team still makes by hand later are merged into theirs (duplicateMerges below).
const MG_CARD_WAIT_MIN = 30;
// judged by createdAt, never updatedAt: Movegistics re-saves old records, and one from last
// December once looked like it had been touched today

const STATE_SHORT = { 'new jersey': 'NJ', 'new york': 'NY', pennsylvania: 'PA', connecticut: 'CT',
  delaware: 'DE', maryland: 'MD', massachusetts: 'MA', florida: 'FL', 'north carolina': 'NC',
  'south carolina': 'SC', virginia: 'VA', georgia: 'GA', texas: 'TX', california: 'CA' };
const stateShort = s => STATE_SHORT[String(s || '').toLowerCase()] || String(s || '');
const noHtml = s => String(s || '').replace(/<br\s*\/?>/gi, ' ').replace(/<\/(p|div|li)>/gi, ' ')
  .replace(/<[^>]+>/g, '').replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/\s+/g, ' ').trim();

// Movegistics keeps the address in origin[0] / destination[0], prefixed by which end it is.
function mgAddress(list, kind) {
  const a = (list || [])[0] || {};
  const g = f => String(a[kind + f] || '').trim();
  const line = [g('AddressLineOne'), g('AddressLineTwo')].filter(Boolean).join(' ');
  const unit = g('UnitNumber');
  const city = g('City'), state = stateShort(g('State')), zip = g('ZipCode');
  // the city is often already typed into line one ("32 Elberon Ave Atlantic City")
  const tail = [line.toLowerCase().includes(city.toLowerCase()) ? '' : city, [state, zip].filter(Boolean).join(' ')]
    .filter(Boolean).join(', ');
  return [line + (unit ? ` #${unit}` : ''), tail].filter(Boolean).join(', ');
}

const nyIso = ms => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms));   // YYYY-MM-DD

// A work request in the shape the client's lead template expects.
function mgLead(opp, est) {
  const c = ((est || {}).contacts || [])[0] || {};
  const name = String((opp.contactName || {}).name || '').trim()
    || String(opp.title || '').replace(/\s*-\s*Work Request$/i, '').trim();
  const stage = (opp.stage || {}).name || '';
  const src = (opp.source || {}).sourceName || '';
  const facts = [`Added from Movegistics ${opp.displayId || ''}`.trim(), stage && `stage: ${stage}`,
    +opp.amount ? `quote: $${(+opp.amount).toFixed(2)}` : '', opp.menSize ? `${opp.menSize} men` : '']
    .filter(Boolean).join(' · ');
  const notes = [facts, noHtml(opp.customerNote), noHtml(opp.internalNote)].filter(Boolean).join(' · ');
  return {
    name, email: String(c.email || '').trim(), phone: String(opp.phoneNumber || c.phoneNumber || '').trim(),
    source: src || 'Movegistics', cardStatus: 'opportunities',
    date: +opp.serviceDate ? nyIso(+opp.serviceDate) : '',
    time: '', from: mgAddress(opp.origin, 'origin'), to: mgAddress(opp.destination, 'destination'),
    details: [['message', notes], ['special items', noHtml((est || {}).workNote)]].filter(d => d[1]),
    wr: opp.displayId || '', stage,
  };
}

function mgCardBody(lead) {
  const d = String(lead.date || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  const short = d ? `${+d[2]}/${+d[3]}` : '';
  return {
    name: `${lead.name} | Movegistics Lead${short ? ' | ' + short : ''}`,
    markdown_content: templateMarkdown(lead,
      `Added automatically from Movegistics ${lead.wr}. Nobody had made a card for this customer.`),
    status: lead.cardStatus,
  };
}

// Work requests opened recently that nobody has made a card for.
function movegisticsCards(opps, estimates, tasks, nowMs = Date.now()) {
  const newest = new Map();                       // the latest estimate per work request, for the email
  for (const e of estimates || []) {
    if (!e.opportunityId || e.isDeleted) continue;
    const prev = newest.get(e.opportunityId);
    if (!prev || (+e.updatedAt || 0) > (+prev.updatedAt || 0)) newest.set(e.opportunityId, e);
  }
  const out = [];
  for (const o of opps || []) {
    if (o.isDeleted || !+o.createdAt) continue;
    if (nowMs - +o.createdAt > MG_CARD_DAYS * 86400000) continue;
    if (nowMs - +o.createdAt < MG_CARD_WAIT_MIN * 60000) continue;    // the team may still be on it
    // a move that has already happened is history, not an opportunity
    if (+o.serviceDate && +o.serviceDate < nowMs - 86400000) continue;
    const lead = mgLead(o, newest.get(o._id));
    if (!lead.name) continue;
    if (!lead.email && digits(lead.phone).length !== 10) continue;   // no way to reach them anyway
    if (matchCard(tasks, { email: lead.email, phone: lead.phone, name: lead.name })) continue;
    out.push({ isNew: true, wr: lead.wr, name: lead.name, email: lead.email, phone: lead.phone,
      moveDate: lead.date, stage: lead.stage, amount: +o.amount || 0, cardBody: mgCardBody(lead) });
  }
  return out;
}

function mgCardSlack(rows, live) {
  if (!rows.length) return '';
  const head = live
    ? `:new: *${rows.length} customer${rows.length > 1 ? 's' : ''} in Movegistics had no card - added to Opportunities*`
    : `:new: *${rows.length} customer${rows.length > 1 ? 's' : ''} in Movegistics with no card* - _dry run, nothing created_`;
  const pretty = iso => { const d = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return d ? new Date(Date.UTC(+d[1], +d[2] - 1, +d[3], 12)).toLocaleString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : ''; };
  return [head, ...rows.map(r => `• *${r.name}* - ${r.wr}${r.moveDate ? ` - moving ${pretty(r.moveDate)}` : ' - no move date'}`
    + `${r.amount ? ` - $${r.amount.toFixed(2)}` : ''} - ${r.email || r.phone}`)].join('\n');
}

// ---- the duplicate catcher (2026-10-05) --------------------------------------------------------
// With a 30-minute wait, somebody will now and then make the card by hand after ours exists. The
// team's card is the one they work from, so it is kept: our automatic card's comments are copied onto
// it as one note and the automatic card is archived (not deleted - it can be brought back).
// Same person = same email, same phone, or the same full name. Not merged: two cards with different
// move dates (the team keeps one card per job - a storage pickup and its delivery, say), a card made
// well before ours (that is an older card for this person, not the copy), or when there are several.
const MG_AUTO_MARK = 'Added automatically from Movegistics';
const MERGE_RACE_MS = 10 * 60000;   // a card typed while ours was being made still counts as the copy

const cardText = t => String(t.markdown_description || t.description || '').replace(/\\(?=[^A-Za-z0-9\s])/g, '');
const isAutoCard = t => cardText(t).includes(MG_AUTO_MARK);
function cardField(t, label) {
  const m = cardText(t).match(new RegExp(`^\\**${label}\\s*-\\**\\s*(.*)$`, 'im'));
  return m ? m[1].replace(/\*+/g, '').trim() : '';
}
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
// "2026-10-11", "10/11", "10/11/26", "Oct 11th" -> "10/11"; anything else -> ''
function monthDay(s) {
  const t = String(s || '').toLowerCase();
  let m = t.match(/\b\d{4}-(\d{1,2})-(\d{1,2})\b/);
  if (m) return `${+m[1]}/${+m[2]}`;
  m = t.match(/\b(\d{1,2})\/(\d{1,2})\b/);
  if (m) return `${+m[1]}/${+m[2]}`;
  m = t.match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})/);
  return m ? `${MONTHS.indexOf(m[1]) + 1}/${+m[2]}` : '';
}
const moveDay = t => monthDay(cardField(t, 'Date of Move')) || monthDay(String(t.name || '').split('|').slice(1).join(' '));
const autoName = t => norm(String(t.name || '').split('|')[0]);
const autoEmail = t => (cardText(t).match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [''])[0].toLowerCase();

function duplicateMerges(tasks, nowMs = Date.now()) {
  const merges = [], leftAlone = [];
  const autos = (tasks || []).filter(isAutoCard);
  for (const a of autos) {
    const email = autoEmail(a), phone = digits(cardField(a, 'Phone Number')), name = autoName(a);
    const hay = t => `${t.name || ''}\n${cardText(t)}`.toLowerCase();
    const twins = (tasks || []).filter(t => t.id !== a.id && !isAutoCard(t)
      && +t.date_created > +a.date_created - MERGE_RACE_MS
      && ((email && hay(t).includes(email))
        || (phone.length === 10 && hay(t).replace(/\D/g, '').includes(phone))
        || (name.split(' ').length >= 2 && norm(t.name).startsWith(name))));
    if (!twins.length) continue;
    const label = String(a.name || '').split('|')[0].trim();
    // said once, in the run after the other card appears, not every 5 minutes from then on
    const fresh = twins.some(t => nowMs - +t.date_created < 5 * 60000);
    if (twins.length > 1) { if (fresh) leftAlone.push({ name: label, why: `${twins.length + 1} cards for this customer - sort by hand` }); continue; }
    const keep = twins[0];
    const dA = moveDay(a), dK = moveDay(keep);
    if (dA && dK && dA !== dK) { if (fresh) leftAlone.push({ name: label, why: `different move dates (${dA} and ${dK}) - two jobs?` }); continue; }
    const wr = (cardText(a).match(/\bWR\d+/) || [''])[0];
    const est = cardText(a).match(/^\**Estimate -\**.*\b(ES\d+)\b.*$/m);
    merges.push({ isMerge: true, name: label, wr, autoId: a.id, autoUrl: a.url, autoStatus: (a.status || {}).status || '',
      keepId: keep.id, keepUrl: keep.url, keepName: keep.name, keepStatus: (keep.status || {}).status || '',
      estimateLine: est && !cardText(keep).includes(est[1]) ? est[0].trim() : '' });
  }
  return { merges, leftAlone };
}

// The note left on the kept card: what the automatic card held, oldest comment first.
function mergeNote(m, comments, nowMs = Date.now()) {
  const when = ms => new Date(+ms).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'numeric',
    day: 'numeric', hour: 'numeric', minute: '2-digit' });
  const lines = [`🔁 Duplicate card merged: there was also an automatic card for this customer from Movegistics`
    + `${m.wr ? ` ${m.wr}` : ''}. It has been archived so there is only one card - this one.`];
  if (m.autoStatus && m.autoStatus !== m.keepStatus) lines.push(`The automatic card was in ${m.autoStatus.toUpperCase()}.`);
  if (m.estimateLine) lines.push(m.estimateLine.replace(/\*\*/g, '').replace(/\[([^\]]*)\]\(([^)]*)\)/g, '$1: $2'));
  const notes = (comments || []).filter(c => String(c.comment_text || '').trim())
    .sort((x, y) => +x.date - +y.date).slice(-15);
  if (notes.length) {
    lines.push('', `Its comments (${notes.length}):`);
    for (const c of notes) {
      const text = String(c.comment_text).trim().replace(/\n{3,}/g, '\n\n');
      lines.push(`— ${(c.user || {}).username || 'someone'}, ${when(c.date)}: ${text.length > 700 ? text.slice(0, 700) + '…' : text}`);
    }
  }
  return lines.join('\n');
}

function mergeSlack(merges, leftAlone, live) {
  const out = [];
  if (merges.length) out.push(`:link: *${merges.length} duplicate card${merges.length > 1 ? 's' : ''}*${live ? ' merged' : ' found - _dry run, nothing changed_'}: `
    + 'the team made a card for a customer who already had an automatic one; kept the team\'s card, archived ours\n'
    + merges.map(m => `• *${m.name}* - kept <${m.keepUrl}|${m.keepName}>, archived <${m.autoUrl}|the automatic card>`).join('\n'));
  if (leftAlone.length) out.push(`:grey_question: *Possible duplicate, left alone*\n` + leftAlone.map(x => `• *${x.name}* - ${x.why}`).join('\n'));
  return out.join('\n\n');
}

if (typeof module !== 'undefined') module.exports = { movegisticsCards, mgLead, mgAddress, mgCardBody, mgCardSlack, MG_CARD_DAYS, MG_CARD_WAIT_MIN, duplicateMerges, mergeNote, mergeSlack, monthDay, isAutoCard };
