// Emails waiting for a reply (Additional Integrations #13).
// Every 2 hours: Gmail threads where the last message is from a customer or influencer and nobody
// from Express Haulers has answered. Lead notifications (website/Yelp/Thumbtack) and emails that
// need no reply ("thank you!", receipts, newsletters) are left out: Claude makes that call.

const US = /@expresshaulers(moving)?\.com/i;
const WAIT_MIN = 60;            // give the team an hour before it counts as unanswered
const MAX_AGE_DAYS = 7;         // older than this, chasing it is pointless
const MAX_LISTED = 8;
const PER_RUN = 20;             // emails judged by Claude in one go

const header = (m, name) => ((m.payload?.headers || []).find(h => h.name.toLowerCase() === name.toLowerCase()) || {}).value || '';
const addr = s => (String(s).match(/[\w.+-]+@[\w.-]+\.\w+/) || [''])[0].toLowerCase();
const person = s => String(s).replace(/<[^>]*>/, '').replace(/["']/g, '').trim() || addr(s);

// thread: Gmail threads.get with format=metadata. Returns null when it needs no chasing.
function waitingThread(thread, nowMs) {
  const msgs = (thread.messages || []).slice().sort((a, b) => +a.internalDate - +b.internalDate);
  if (!msgs.length) return null;
  const last = msgs[msgs.length - 1];
  const from = header(last, 'From');
  if (US.test(from)) return null;                                   // we spoke last: nothing to chase
  const at = +last.internalDate;
  if (nowMs - at < WAIT_MIN * 60000 || nowMs - at > MAX_AGE_DAYS * 86400000) return null;
  // did anyone here ever reply in this thread? (tells the team whether it's a new or ongoing talk)
  const everReplied = msgs.some(m => US.test(header(m, 'From')));
  return {
    threadId: thread.id, messageId: last.id,
    who: person(from), email: addr(from),
    subject: header(last, 'Subject') || '(no subject)',
    snippet: (last.snippet || '').replace(/\s+/g, ' ').slice(0, 400),
    at, waitedHours: Math.round((nowMs - at) / 3600000),
    everReplied, messages: msgs.length,
  };
}

function unansweredSystem() {
  return [
    'You check the inbox of a New Jersey moving company (Express Haulers Moving).',
    'For each email, decide whether someone at the company still needs to REPLY to it.',
    'needsReply = true for: customers or influencers asking a question, wanting a quote, price or date,',
    'chasing an answer, negotiating, complaining, reporting damage, or proposing a collaboration.',
    'needsReply = false for: "thank you", "sounds good", "received", confirmations and pleasantries that ask nothing;',
    'automatic mail (receipts, invoices, newsletters, marketing, calendar and system notifications, out-of-office);',
    'new-lead notifications from the website, Yelp or Thumbtack (those are handled by another automation);',
    'and anything from a supplier or platform rather than a customer.',
    'Answer with JSON only: {"items":[{"id":"<id>","needsReply":true|false,"who":"<name>","why":"<max 8 words>"}]}',
    'When the sender is a real customer writing about their own move, estimate, booking or invoice, default to',
    'needsReply = true: declining, postponing, hesitating over price or going with someone else all deserve an answer.',
    'Keep "why" concrete: what they are waiting for.',
  ].join(' ');
}

function unansweredBody(candidates, model = 'claude-opus-5') {
  const list = candidates.map(c => `id: ${c.threadId}\nfrom: ${c.who} <${c.email}>\nsubject: ${c.subject}\n`
    + `waited: ${c.waitedHours}h\nmessage: ${c.snippet}`).join('\n---\n');
  return { model, max_tokens: 4000, system: unansweredSystem(),
    messages: [{ role: 'user', content: `Emails (${candidates.length}):\n\n${list}` }] };
}

function parseUnanswered(res) {
  const text = (res.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  const m = text.match(/\{[\s\S]*\}/);
  try { return m ? (JSON.parse(m[0]).items || []) : []; } catch (e) { return []; }
}

const gmailLink = threadId => `https://mail.google.com/mail/u/0/#all/${threadId}`;

function unansweredSlack(rows) {
  if (!rows.length) return '';
  const age = h => (h < 24 ? `${h}h` : `${Math.round(h / 24)}d`);
  const head = `:inbox_tray: *${rows.length} email${rows.length > 1 ? 's' : ''} waiting for a reply*`;
  const lines = rows.slice(0, MAX_LISTED).map(r =>
    `• *${r.who}* — <${gmailLink(r.threadId)}|${r.subject.slice(0, 60)}> · ${age(r.waitedHours)}`
    + (r.why ? ` · _${r.why}_` : '') + (r.everReplied ? '' : ' · first contact'));
  if (rows.length > MAX_LISTED) lines.push(`• …and ${rows.length - MAX_LISTED} more in the inbox`);
  return [head, ...lines].join('\n');
}

// One alert per customer message: a new reply from them makes it current again.
function pruneAlerted(seen, nowMs) {
  for (const [k, v] of Object.entries(seen)) if (nowMs - v > 30 * 86400000) delete seen[k];
  return seen;
}

if (typeof module !== 'undefined') module.exports = { waitingThread, PER_RUN, unansweredBody, parseUnanswered, unansweredSlack, pruneAlerted, gmailLink, US };
