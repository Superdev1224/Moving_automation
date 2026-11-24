// New leads → Movegistics lead (source of truth) + ClickUp card (the team's working board).
// Used with parse-lead.js (which reads the lead) and booking.js (matchCard for duplicates).

const MG_SOURCES = {
  Website: { id: '68dbdcecac546f0001b573c4', sourceName: 'Website Quote Request' },
  Yelp: { id: '68cc1890ac546f000143f0bc', sourceName: 'Yelp' },
  Thumbtack: { id: '68dbdd45ac546f0001b57401', sourceName: 'Thumbtack' },
};
const MG_OWNER = { id: 101410, firstName: 'Lynn', lastName: 'C', email: 'contact@expresshaulers.com' };

const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
const e164 = p => { const d = String(p || '').replace(/\D/g, '').slice(-10); return d.length === 10 ? '+1' + d : ''; };
const zipOf = s => (String(s || '').match(/\b\d{5}\b/) || [''])[0];

function splitName(full) {
  const parts = clean(full).split(' ').filter(Boolean);
  if (!parts.length) return { firstName: 'Unknown', lastName: '' };
  return { firstName: parts[0], lastName: parts.slice(1).join(' ') };
}

// "2026-09-27" (website form) → epoch ms at noon New York time; anything else → ''
function serviceDateMs(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? String(Date.UTC(+m[1], +m[2] - 1, +m[3], 16, 0)) : '';
}

// What to search Movegistics and ClickUp with, best key first.
function searchKey(lead) {
  return clean(lead.email).toLowerCase() || e164(lead.phone).slice(2) || clean(lead.name);
}

function movegisticsLead(lead) {
  const { firstName, lastName } = splitName(lead.name);
  const body = {
    firstName, lastName,
    moveType: 'Residential', hasStorage: 'no',
    volume: '0', volumeUnit: 'CFT/FT3', weight: '0', weightUnit: 'LBS',
    leadStatus: 'New',
    owner: MG_OWNER,
    customerNote: clean(lead.details && lead.details.map(([k, v]) => `${k}: ${v}`).join(' | ')),
    internalNote: `Added automatically from the ${lead.source.toLowerCase()} on ${new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' })}.${lead.returning ? ' Returning customer: new move date.' : ''}`,
    externalReference: lead.gmailId || '',
  };
  if (MG_SOURCES[lead.source]) body.source = MG_SOURCES[lead.source];
  if (clean(lead.email)) body.emailAddress = clean(lead.email);
  if (e164(lead.phone)) body.contactNumberMobile = e164(lead.phone);
  const date = serviceDateMs(lead.date);
  if (date) body.serviceDate = date;
  if (clean(lead.from)) body.origin = [{ originAddressLineOne: clean(lead.from), originZipCode: zipOf(lead.from) }];
  if (clean(lead.to)) body.destination = [{ destinationAddressLineOne: clean(lead.to), destinationZipCode: zipOf(lead.to) }];
  return body;
}

// An Instagram lead has no email or phone, so their handle goes on the card: it is the only thing
// that lets the next message from the same person find this card instead of making a new one.
const handleOf = lead => {
  const h = (String((lead.details || []).find(([k]) => /instagram/i.test(k))?.[1] || '').match(/@[\w.]+/) || [''])[0];
  return h ? ` Instagram: ${h}.` : '';
};

// A Yelp lead's chat id, written as plain text (not an address, so no tool emails it): our automatic
// first reply rebuilds the Yelp relay address from it, and a second payload for the same lead finds it.
const yelpChatOf = lead => (lead.yelpChat ? ` Yelp chat id: ${lead.yelpChat}.` : '');

// The team's own card template, so the other automations can read the date, email and phone.
function clickupCard(lead, mgLeadId) {
  const d = String(lead.date || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  const shortDate = d ? `${+d[2]}/${+d[3]}` : '';
  const tag = clean(lead.tag) ? ` | ${clean(lead.tag)}` : '';
  return {
    name: `${clean(lead.name) || 'Unknown'} | ${lead.source} Lead${tag}${shortDate ? ' | ' + shortDate : ''}`,
    markdown_content: templateMarkdown(lead, `Added automatically from ${lead.source}.${handleOf(lead)}${yelpChatOf(lead)} Movegistics lead: ${mgLeadId || 'already existed'}${lead.returning ? '. Returning customer: this is a new move date, their earlier card is separate' : ''}`),
    status: clean(lead.cardStatus) || 'new leads',
  };
}

// Does Movegistics already know this person? (lead or contact with the same email/phone/name)
function foundInMovegistics(lead, leadRows, contactRows) {
  const email = clean(lead.email).toLowerCase(), phone = e164(lead.phone).slice(2);
  const rowPhones = r => [r.contactNumberMobile, r.contactNumberWork, r.contactNumberHome, r.phoneNumber,
    ...(r.phoneNumbers || []).map(x => x.number || x)].map(x => String(x || '').replace(/\D/g, '').slice(-10));
  const hit = r => (email && String(r.emailAddress || r.email || '').toLowerCase() === email)
    || (phone && rowPhones(r).includes(phone));
  const l = (leadRows || []).find(hit), c = (contactRows || []).find(hit);
  return l ? { kind: 'lead', id: l.displayId || l.id || l._id } : c ? { kind: 'contact', id: c.displayId || c.id || c._id } : null;
}

// a name as cards are compared on it: lower case, letters and digits only ("Gab 🫶🏽🇹🇹✨" -> "gab")
const cardKey = s => String(s || '').toLowerCase().normalize('NFKD').replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();

// ---- Duplicate rule (Additional Integrations #2) --------------------------------------------
// Same person + same move date = the same move (duplicate). Same person + a different move date
// = a NEW move (repeat customer). When either side has no date, it's the same enquiry only if the
// earlier one is recent (30 days). Finished jobs (complete / lost) never absorb a new enquiry.
const SAME_ENQUIRY_DAYS = 30;
const FINISHED = ['moves complete', 'moves complete not paid', 'moves complete + paid', 'moves lost', 'affrim offered'];

function leadDate(lead, nowMs) {
  const iso = String(lead.date || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return { y: +iso[1], m: +iso[2], d: +iso[3] };
  return lead.date ? findDate(String(lead.date), nowMs) : null;
}
const sameDay = (a, b) => a && b && a.m === b.m && a.d === b.d;

function sameMove(theirDate, updatedMs, leadD, nowMs) {
  if (theirDate && leadD) return sameDay(theirDate, leadD);
  return nowMs - (updatedMs || 0) < SAME_ENQUIRY_DAYS * 86400000;
}

// Returns { task } when an open card is this same move, or { returning: true } when the person
// is known but this is a new move, or null when they're new.
// ClickUp's markdown escapes "_" as "\\_", so "@hidden_feeling1312" or "kisha_lofton@yahoo.com" never
// matched until the escapes were stripped (Bhawna Gupta got a 4th card the next morning, 2026-10-04).
function existingCardForMove(tasks, lead, nowMs = Date.now()) {
  const email = clean(lead.email).toLowerCase(), phone = e164(lead.phone).slice(2);
  // an Instagram lead often has neither: their handle is the only thing that identifies them
  const handle = (String((lead.details || []).find(([k]) => /instagram/i.test(k))?.[1] || '')
    .match(/@[\w.]+/) || [''])[0].toLowerCase();
  // ... or the display name the team typed when they made the card by hand ("The Modest Explorer |
  // Influencer" on All Realtors). Full names of two words or more only, and only for Instagram.
  const igName = lead.source === 'Instagram' && !email && !phone && !/^instagram\b/i.test(clean(lead.name)) ? cardKey(lead.name) : '';
  const yelp = String(lead.yelpChat || '').toLowerCase();
  if (!email && !phone && !handle && !igName && !yelp) return null;
  const hay = t => `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '').toLowerCase();
  const mine = tasks.filter(t => (email && hay(t).includes(email))
    || (phone && hay(t).replace(/\D/g, '').includes(phone))
    || (handle && hay(t).includes(handle))
    || (yelp && hay(t).includes(yelp))
    || (igName.includes(' ') && cardKey(String(t.name || '').split('|')[0]) === igName));
  if (!mine.length) return null;
  const leadD = leadDate(lead, nowMs);
  const open = mine.filter(t => !FINISHED.includes(String(t.status?.status || t.status || '').toLowerCase()))
    .sort((a, b) => +(b.date_updated || 0) - +(a.date_updated || 0));
  for (const t of open) {
    const desc = t.markdown_description || t.description || '';
    const cardD = findDate(field(desc, 'Date of Move'), nowMs) || findDate(t.name || '', nowMs);
    if (sameMove(cardD, +t.date_updated, leadD, nowMs)) return { task: t };
  }
  return { returning: true };
}

// After we made a card: was another card for the same enquiry made just before ours? Several DMs in a
// row run side by side, each reads the board (~30 s) before the others' cards exist, and each made
// a card (Bhawna Gupta: 3 in one minute; The Modest Explorer: 2 in 13 seconds). Every run looks again
// a minute later and the OLDEST card wins: the others archive themselves and leave their message on
// it. All runs agree on which card is oldest, so exactly one survives.
function earlierCardFor(tasks, mine, lead, nowMs = Date.now()) {
  const before = t => +t.date_created < +mine.date_created || (+t.date_created === +mine.date_created && t.id < mine.id);
  return (tasks || []).filter(t => t.id !== mine.id && !t.archived && before(t))
    .filter(t => existingCardForMove([t], lead, nowMs)?.task)
    .sort((a, b) => +a.date_created - +b.date_created)[0] || null;
}

// Same rule for Movegistics: a lead with the same move date (or a recent undated one) is a duplicate.
// A person known only as a contact, or with a different date, is a returning customer: new lead.
function existingMovegisticsMove(lead, leadRows, contactRows, nowMs = Date.now()) {
  const found = foundInMovegistics(lead, leadRows, []);
  const email = clean(lead.email).toLowerCase(), phone = e164(lead.phone).slice(2);
  const leadD = leadDate(lead, nowMs);
  for (const r of leadRows || []) {
    const rowPhones = [r.contactNumberMobile, r.contactNumberWork, r.contactNumberHome].map(x => String(x || '').replace(/\D/g, '').slice(-10));
    const isThem = (email && String(r.emailAddress || '').toLowerCase() === email) || (phone && rowPhones.includes(phone));
    if (!isThem) continue;
    const ms = +r.serviceDate || 0;
    const rowD = ms ? (() => { const t = new Date(ms); return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }; })() : null;
    if (sameMove(rowD, +r.createdAt || +r.updatedAt, leadD, nowMs)) return { kind: 'lead', id: r.displayId || r.id || r._id };
  }
  const known = found || foundInMovegistics(lead, [], contactRows);
  return known ? { returning: true, kind: known.kind, id: known.id } : null;
}

// ---- follow-up messages: fill the blanks instead of starting over ------------------------------
// An Instagram customer often writes first and sends their number later. The second message used
// to leave the number in a comment and open a second Movegistics lead (Luisa's test, 2026-09-29).

// the card's footer records its Movegistics lead: "Movegistics lead: 6abbf5d1e03dd8000167dc2f"
const mgIdOnCard = task => ((task && (task.markdown_description || task.description) || '')
  .match(/Movegistics lead:\s*([0-9a-f]{24})/i) || [])[1] || null;

// Write what we now know into the card's empty template lines. Only blanks are touched.
function fillCardBlanks(md, lead) {
  const d = String(lead.date || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  const values = [['Phone Number', clean(lead.phone)], ['Email', clean(lead.email)],
    ['Date of Move', d ? `${+d[2]}/${+d[3]}` : ''], ['Moving From', clean(lead.from)], ['Moving To', clean(lead.to)]];
  let out = String(md || ''), filled = [];
  for (const [label, value] of values) {
    if (!value) continue;
    const re = new RegExp('^(\\*\\*' + label + ' -\\*\\*)[^\\S\\n]*$', 'm');     // "**Phone Number -**" and nothing after
    if (re.test(out)) { out = out.replace(re, `$1 ${value}`); filled.push(label); }
  }
  return filled.length ? { markdown: out, filled } : null;
}

// Same for the Movegistics lead: read its full record, fill only what is empty, send it all back
// (their update replaces the whole lead, so a partial body would wipe the rest).
const MG_READ_ONLY = ['_id', 'displayId', 'createdAt', 'updatedAt', 'updatedBy', 'internalNoteId', 'tenantId', 'companyId', 'locationId'];
function fillMgBlanks(row, lead) {
  if (!row) return null;
  const body = Object.fromEntries(Object.entries(row).filter(([k]) => !MG_READ_ONLY.includes(k)));
  body.id = row._id;
  const filled = [];
  if (!row.contactNumberMobile && e164(lead.phone)) { body.contactNumberMobile = e164(lead.phone); filled.push('phone'); }
  if (!row.emailAddress && clean(lead.email)) { body.emailAddress = clean(lead.email); filled.push('email'); }
  const when = serviceDateMs(lead.date);
  if (!row.serviceDate && when) { body.serviceDate = when; filled.push('move date'); }
  const hasFrom = ((row.origin || [])[0] || {}).originAddressLineOne, hasTo = ((row.destination || [])[0] || {}).destinationAddressLineOne;
  if (!hasFrom && clean(lead.from)) { body.origin = [{ originAddressLineOne: clean(lead.from), originZipCode: zipOf(lead.from) }]; filled.push('moving from'); }
  if (!hasTo && clean(lead.to)) { body.destination = [{ destinationAddressLineOne: clean(lead.to), destinationZipCode: zipOf(lead.to) }]; filled.push('moving to'); }
  return filled.length ? { body, filled } : null;
}

if (typeof module !== 'undefined') module.exports = { mgIdOnCard, fillCardBlanks, fillMgBlanks, movegisticsLead, clickupCard, searchKey, foundInMovegistics, existingCardForMove, earlierCardFor, existingMovegisticsMove, splitName, e164, MG_SOURCES };
