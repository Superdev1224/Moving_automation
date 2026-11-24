// Turns a lead-notification email (Gmail node, simple=false) into a Slack message.
// Sources: website contact form (Shopify), Yelp "request a quote", Thumbtack "New direct lead".
// Yelp replies and Thumbtack "Great fit" opportunities are ignored (returns null).
// Links are never copied: Yelp's buttons are password-free logins to the business account.

function cleanText(s) {
  return (s || '')
    .replace(/[\u034f\u200b-\u200f\u2060\ufeff\u00ad]/g, '')   // invisible padding Yelp/Thumbtack use
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')                    // markdown links → label only
    .replace(/https?:\/\/\S+/g, '')                             // bare URLs
    .replace(/\r/g, '')
    .split('\n').map(l => l.replace(/\s+$/, '')).join('\n');
}

const lines = t => t.split('\n').map(l => l.trim()).filter(Boolean);
const oneLine = s => (s || '').replace(/\s+/g, ' ').trim();

// Website form: "Label:\nvalue" pairs.
function parseWebsite(text) {
  const labels = ['Country Code', 'Moving From', 'Moving To', 'Moving Date', 'Name', 'Phone', 'Email', 'Message'];
  const out = {};
  let key = null;
  for (const l of lines(text)) {
    if (/^-{5,}/.test(l)) { key = null; continue; }          // footer separator
    const m = l.match(/^([A-Za-z ]+):\s*(.*)$/);
    if (m && labels.includes(m[1].trim())) { key = m[1].trim(); out[key] = m[2] || ''; continue; }
    if (key) out[key] = oneLine(`${out[key]} ${l}`);
  }
  return {
    source: 'Website', name: out['Name'], phone: out['Phone'], email: out['Email'],
    from: out['Moving From'], to: out['Moving To'], date: out['Moving Date'],
    details: out['Message'] ? [['Message', out['Message']]] : [],
  };
}

const YELP_LABELS = [
  [/size of your move/i, 'Size'], [/floors at your current location/i, 'Stairs (pickup)'],
  [/floors at your destination/i, 'Stairs (drop-off)'], [/special items/i, 'Special items'],
  [/assistance preparing/i, 'Packing / prep help'], [/other details/i, 'Details'],
];
const shortLabel = q => (YELP_LABELS.find(([re]) => re.test(q)) || [null, q.replace(/\s*\(.*?\)\s*/g, ' ').trim()])[1];

// Yelp tags the first email of a quote request (utm_source=request_a_quote_first_message);
// later emails in the thread (team replies, customer follow-ups) are not new leads.
function parseYelp(subject, text, raw) {
  const first = /request_a_quote_first_message/.test(raw) || (!raw && /requested a quote/i.test(text));
  if (!first) return null;
  const L = lines(text);
  const name = oneLine((text.match(/^\s*(.+?) requested a quote/m) || [])[1] || (subject.match(/response to (.+)$/i) || [])[1]);
  const service = oneLine((text.match(/for an? (.+?)\. Reply/i) || [])[1] || (text.match(/new (.+?) request/i) || [])[1]);
  const qa = [];
  for (let i = 0; i < L.length - 1; i++) {
    if (L[i].endsWith('?') && !L[i + 1].endsWith('?')) { qa.push([L[i].replace(/\?$/, ''), L[i + 1]]); i++; }
  }
  const get = re => (qa.find(([q]) => re.test(q)) || [])[1];
  const skip = /where are you moving to|when do you require|in what location/i;
  return {
    source: 'Yelp', name, service,
    from: get(/in what location/i), to: get(/where are you moving to/i), date: get(/when do you require/i),
    details: qa.filter(([q]) => !skip.test(q)).map(([q, a]) => [shortLabel(q), a]),
    note: 'Reply in the Yelp for Business app',
  };
}

// Thumbtack: only "New direct lead" (a customer chose us). "Great fit" = open opportunity, ignored.
function parseThumbtack(subject, text) {
  if (!/new direct lead/i.test(subject)) return null;
  const name = oneLine(subject.replace(/^.*new direct lead!?\s*/i, ''));
  const L = lines(text).filter(l => !/^[A-Z]{1,3}$/.test(l));        // drop avatar initials ("JE")
  const i = L.findIndex(l => l === name);
  const body = (i >= 0 ? L.slice(i + 1) : L).filter(l => !/^direct lead$/i.test(l) && !/view direct lead|you can call this customer/i.test(l));
  const date = (body.find(l => /^date:/i.test(l)) || '').replace(/^date:\s*/i, '');
  const extra = body.filter(l => !/^date:/i.test(l));
  return {
    source: 'Thumbtack', name, service: extra[0], from: extra[1], date,
    details: extra.slice(2).filter(l => !/^(view|reply|unsubscribe|thumbtack)/i.test(l) && l.length < 200).map(l => ['Note', l]),
    note: 'Reply in the Thumbtack app, then you can call the customer',
  };
}

function parseLeadEmail(msg) {
  const from = ((msg.from && msg.from.value && msg.from.value[0] && msg.from.value[0].address) || '').toLowerCase();
  const subject = msg.subject || '';
  const text = cleanText(msg.text || '');
  let lead = null;
  if (from === 'mailer@shopify.com' && /^new customer message on/i.test(subject)) lead = parseWebsite(text);
  else if (from.endsWith('@messaging.yelp.com')) lead = parseYelp(subject, text, `${msg.html || ''}${msg.text || ''}`);
  else if (from.endsWith('thumbtack.com')) lead = parseThumbtack(subject, text);
  if (!lead) return null;
  lead.receivedAt = msg.date;
  lead.gmailId = msg.id;
  return lead;
}

const ICON = { Website: ':globe_with_meridians:', Yelp: ':red_circle:', Thumbtack: ':large_blue_circle:' };

function slackText(lead) {
  const when = lead.receivedAt ? new Date(lead.receivedAt).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  let t = `${ICON[lead.source] || ':bell:'} *New ${lead.source} lead* — ${lead.name || 'Unknown name'}${when ? `  _(${when})_` : ''}\n`;
  t += templateSlack(lead);                       // the team's lead template (lib/lead-template.js)
  if (lead.note) t += `\n_${lead.note}_`;
  return t.trim();
}

if (typeof module !== 'undefined') module.exports = { parseLeadEmail, slackText, cleanText };
