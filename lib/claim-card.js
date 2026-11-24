// A claim card in the team's own claims section: the "Damage Claims" space (client, 2026-09-30).
// Email claims (#6) and claims raised on calls or texts (#12) both hand over to this one step, which
// fills the fields of the team's "Damage / Loss Claim Form" instead of writing a long description.
// CLAIM_FIELD_IDS (field and dropdown ids of that list) is pasted in by the builder.

const CLAIM_OPEN = ['open claim', 'under review'];          // a claim still being worked
// Which existing claim does a new message belong to? (2026-10-10: "ClickUp keeps making new damage
// claims even though it's an old one".) Only "open claim" / "under review" counted, so a follow-up on
// a claim in "onsite inspection" (Bob Cohen) or "no longer priority" (Yasmine's chair) made a new
// card. Now every status counts except the closed two, and a claim closed in the last 60 days still
// takes the follow-up - it is far more likely the same claim coming back than a new one.
const CLAIM_DONE = ['complete', 'no longer priority'];
const REOPEN_DAYS = 60;
const claimStatus = t => String(t.status?.status || '').toLowerCase();
function findClaim(tasks, who, nowMs = Date.now()) {
  const live = t => !CLAIM_DONE.includes(claimStatus(t))
    || nowMs - +(t.date_updated || t.date_created || 0) < REOPEN_DAYS * 86400000;
  return findCard((tasks || []).filter(live), who, false);
}
const ny = ms => new Date(ms).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'numeric', day: 'numeric' });

// Anything a card says about the customer, custom fields included (the team types claims into fields).
const cardText = t => [t.name, t.markdown_description || t.description || '',
  ...(t.custom_fields || []).map(f => (typeof f.value === 'string' || typeof f.value === 'number') ? String(f.value) : '')]
  .join('\n').replace(/\\(?=[^A-Za-z0-9\s])/g, '').toLowerCase();   // ClickUp escapes "_" as "\\_" in markdown
const tenOf = s => String(s || '').replace(/\D/g, '').slice(-10);

function findCard(tasks, who, onlyOpen) {
  const email = String(who.email || '').toLowerCase().trim();
  const phone = tenOf(who.phone);
  const name = String(who.customerName || '').toLowerCase().replace(/\s+/g, ' ').trim();
  const pool = (tasks || []).filter(t => !onlyOpen || CLAIM_OPEN.includes(String(t.status?.status || '').toLowerCase()))
    .sort((a, b) => +(b.date_updated || 0) - +(a.date_updated || 0));
  return (email && pool.find(t => cardText(t).includes(email)))
    || (phone.length === 10 && pool.find(t => cardText(t).replace(/\D/g, '').includes(phone)))
    // a name alone must be a full name, and must be in the card's title
    || (name.split(' ').length >= 2 && pool.find(t => String(t.name || '').toLowerCase().includes(name)))
    || null;
}

// A claim is about a move that already happened: "8/21" written without a year is last August, not
// next August (the date reader assumes the future, which is right for bookings, wrong here).
function pastMoveDate(d, nowMs) {
  if (!d) return null;
  let ms = toEpoch(d.y, d.m, d.d, 0, 0) * 1000;
  if (ms > nowMs + 7 * 86400000) ms = toEpoch(d.y - 1, d.m, d.d, 0, 0) * 1000;
  return ms;
}

// What the move card knows: date, addresses, contact details.
function fromMoveCard(task, nowMs) {
  if (!task) return {};
  const desc = task.markdown_description || task.description || '';
  const plain = s => String(s || '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/\*\*/g, '').trim();
  const d = findDate(field(desc, 'Date of Move'), nowMs) || findDate(task.name || '', nowMs);
  return {
    moveDate: pastMoveDate(d, nowMs),
    origin: plain(field(desc, 'Moving From')), destination: plain(field(desc, 'Moving To')),
    phone: plain(field(desc, 'Phone Number')), email: (plain(field(desc, 'Email')).match(/[\w.+-]+@[\w.-]+\.\w{2,}/) || [''])[0],
    moveCardUrl: task.url, moveCardName: task.name,
  };
}

// The crew on that move: the Connecteam shift whose title starts with the customer's name.
function crewFor(shifts, users, customerName) {
  const first = String(customerName || '').toLowerCase().split(/\s+/)[0];
  const last = String(customerName || '').toLowerCase().split(/\s+/).slice(-1)[0];
  if (!first) return '';
  const byId = Object.fromEntries((users || []).map(u => [u.userId, u.firstName]));
  const ids = new Set();
  for (const s of shifts || []) {
    const t = String(s.title || '').toLowerCase().split('|')[0];
    if (t.includes(first) && (t.includes(last) || first === last)) (s.assignedUserIds || []).forEach(id => ids.add(id));
  }
  return [...ids].map(id => byId[id]).filter(Boolean).join(', ');
}

const pick = (options, v) => {
  const key = Object.keys(options || {}).find(k => k.toLowerCase() === String(v || '').toLowerCase().trim());
  return key ? options[key] : null;
};
const phoneFmt = p => { const d = tenOf(p); return d.length === 10 ? `+1 ${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}` : null; };

// The form's fields, filled with what we know. Unknown values are left empty, never guessed.
function claimFields(c) {
  const F = CLAIM_FIELD_IDS, out = [];
  const put = (name, value, extra) => { if (F[name] && value !== null && value !== undefined && value !== '') out.push({ id: F[name].id, value, ...(extra || {}) }); };
  put('Customer Name', c.customerName);
  put('Email', c.email);
  put('Phone', phoneFmt(c.phone));
  put('Move Date', c.moveDate, { value_options: { time: false } });
  put('Origin Address', c.origin);
  put('Destination Address', c.destination);
  put('Crew Members', c.crew);
  put('Date Reported', c.reported, { value_options: { time: false } });
  put('Item / Area Damaged', c.item);
  put('What Happened', c.whatHappened);
  put('Estimated Value', +c.estimatedValue > 0 ? +c.estimatedValue : null);
  put('Condition Before Move', pick(F['Condition Before Move']?.options, c.condition));
  put('Customer Has Insurance', pick(F['Customer Has Insurance']?.options, c.insurance));
  put('Customer Request', pick(F['Customer Request']?.options, c.request));
  put('Additional Coverage Purchased', pick(F['Additional Coverage Purchased']?.options, c.coverage));
  return out;
}

function claimTitle(c) {
  return `${c.customerName || c.email || 'Unknown customer'} | ${c.item || 'Damage claim'}`
    + (c.moveDate ? ` | move ${ny(c.moveDate)}` : '');
}

function claimDescription(c) {
  return [`**Reported by:** ${c.sourceLabel}${c.subject ? ` - _${c.subject}_` : ''}`,
    c.summary ? `\n${c.summary}` : '',
    c.moveCardUrl ? `\n**Move card:** [${c.moveCardName}](${c.moveCardUrl})` : '',
    c.original ? `\n**What they wrote / said:**\n> ${String(c.original).slice(0, 1500).replace(/\n/g, '\n> ')}` : '',
    '\n_Created automatically. Decision, settlement and resolved date are for the team._'].join('\n');
}

function claimComment(c) {
  return `📧 ${c.sourceLabel}${c.subject ? ` - ${c.subject}` : ''} (${new Date(c.reported).toLocaleString('en-US', { timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' })}):\n`
    + (c.summary ? `${c.summary}\n\n` : '') + String(c.original || '').slice(0, 1500);
}

function claimAlert(c, url, isNew) {
  const closed = !isNew && CLAIM_DONE.includes(String(c.existingStatus || '').toLowerCase());
  const head = isNew ? ':rotating_light: *New damage claim*'
    : closed ? `:email: *More on a claim marked ${String(c.existingStatus).toUpperCase()}* - reopen it if this needs action`
    : ':email: *More on an open claim*';
  return [`${head}: ${c.customerName || c.email || c.phone || 'unknown customer'} · _${c.sourceLabel}_`,
    [c.item && `*Item:* ${c.item}`, c.estimatedValue && `*Value:* $${c.estimatedValue}`, c.request && `*Wants:* ${c.request}`].filter(Boolean).join('  ·  '),
    c.summary || '',
    (url ? `<${url}|Open the claim card>` : '_Could not create the claim card - please add it by hand._')
      + (c.moveCardUrl ? ` · <${c.moveCardUrl}|move card>` : '')].filter(Boolean).join('\n');
}

if (typeof module !== 'undefined') module.exports = { pastMoveDate, findCard, findClaim, fromMoveCard, crewFor, claimFields, claimTitle, claimDescription, claimComment, claimAlert, CLAIM_OPEN };
