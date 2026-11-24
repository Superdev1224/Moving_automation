// Stale lead alert (Additional Integrations #5).
// A new lead that nobody has called, texted or emailed within STALE_MIN minutes gets one alert in
// #sales during business hours. Leads that arrive after hours are alerted when the day starts.
// "Contacted" = an outbound RingCentral call or text to their number, or an email sent to them.

const STALE_MIN = 10;               // the client asked for 5; 10 avoids firing mid-call with someone else
const LEAD_LOOKBACK_H = 6;          // how far back new cards are read
// The team often talks to the customer first and makes the card during or after the call (Peter
// Iannuzzi, David Jeong, Martha Leibla on 2026-09-28/29 all called in, then got a "nobody has
// called them" alert). Any contact in the hours before the card counts too.
const CONTACT_BEFORE_H = 6;
// Only a card nobody has worked yet counts. "Scheduled follow ups", "onsite estimate" and the
// booked/complete statuses all mean someone has already spoken to them.
const IS_A_NEW_LEAD = ['new leads', 'opportunities'];

const tenDigits = s => String(s || '').replace(/\D/g, '').slice(-10);

// The lead's contact details as the team's card template writes them.
function leadContacts(task) {
  const text = `${task.name || ''}\n${task.markdown_description || task.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '');
  const email = (text.match(/[\w.+-]+@[\w.-]+\.\w{2,}/) || [''])[0].toLowerCase();
  const phones = [...text.matchAll(/(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)/g)]
    .map(m => m[1] + m[2] + m[3]);
  return { email, phones: [...new Set(phones)] };
}

function newLeads(tasks, nowMs) {
  return (tasks || [])
    .filter(t => IS_A_NEW_LEAD.includes(String(t.status?.status || '').toLowerCase()))
    .filter(t => nowMs - +t.date_created < LEAD_LOOKBACK_H * 3600000)
    .map(t => ({
      taskId: t.id, name: (t.name || '').split('|')[0].trim(), url: t.url,
      status: t.status?.status || '', createdAt: +t.date_created, ...leadContacts(t), notOurs: notJudgeable(t),
    }))
    .map(l => ({ ...l, mailQuery: sentMailQuery(l) }))
    .filter(l => l.email || l.phones.length)
    .filter(l => !l.notOurs);
}

// Cards we cannot judge from calls and emails: Instagram, Yelp and Thumbtack leads are answered inside
// those apps, which we cannot see (Yelp/Thumbtack added 2026-10-11: "we message them in Yelp" - often
// before we have their phone or email), and a card the Movegistics step made is for a customer the
// team had already entered themselves.
const notJudgeable = t => /\| (Instagram|Movegistics|Yelp|Thumbtack) Lead\b/i.test(t.name || '')
  || /Added automatically from (Instagram|Movegistics|Yelp|Thumbtack)/i.test(t.markdown_description || t.description || '');

// The sent-mail search for a lead: the email on the card OR their full name. The team often makes the
// card by hand from an email thread and leaves the Email line empty - Sophia Fabian (2026-10-05) was
// answered from contact@ at 4:51 PM and still alerted at 4:55, because the card had no email to look
// for. Gmail matches to:"First Last" against the recipient's name; first names alone are too loose.
function sentMailQuery(lead) {
  const name = String(lead.name || '').replace(/["{}()]/g, ' ').replace(/\s+/g, ' ').trim();
  const terms = [lead.email && `to:${lead.email}`, name.split(' ').length >= 2 && `to:"${name}"`].filter(Boolean);
  return terms.length ? `in:sent newer_than:1d {${terms.join(' ')}} ${NOT_AUTO_EMAIL}` : 'in:sent to:nobody@invalid.invalid';
}

// Our own automatic reply (#23) is not someone on the team getting in touch. Since Yelp leads are
// answered automatically day and night (2026-10-10), counting it would hide every Yelp lead from this
// alert. Gmail: leave out the automatic email itself, but keep a person's "Re:" in the same thread
// (checked on real mail: auto only -> 0, auto + a person's reply -> 1).
const NOT_AUTO_EMAIL = '{-subject:"Thanks for reaching out to Express Haulers Moving" '
  + 'subject:"Re: Thanks for reaching out to Express Haulers Moving"}';
const AUTO_TEXT = /It's Diego, the owner of Express Haulers Moving|We're currently closed, but we'll call you first thing/i;

// calls/texts: RingCentral records; sentCount: how many emails we sent to this lead since it arrived.
function reachedOut(lead, calls, texts, sentCount, activeNumbers = []) {
  if (sentCount > 0) return 'email';
  const mine = n => lead.phones.includes(tenDigits(n));
  if ((activeNumbers || []).some(mine)) return 'on a call now';          // not in the call log until it ends
  const after = ms => ms > lead.createdAt - CONTACT_BEFORE_H * 3600000;
  if ((calls || []).some(c => c.direction === 'Outbound' && after(Date.parse(c.startTime)) && mine(c.to?.phoneNumber))) return 'call';
  if ((calls || []).some(c => c.direction === 'Inbound' && c.result === 'Accepted' && after(Date.parse(c.startTime)) && mine(c.from?.phoneNumber))) return 'call';
  if ((texts || []).some(m => m.direction === 'Outbound' && after(Date.parse(m.creationTime))
      && !AUTO_TEXT.test(m.subject || '') && (m.to || []).some(t => mine(t.phoneNumber)))) return 'text';
  return '';
}

function staleLeadSlack(rows, nowMs) {
  const mins = l => Math.round((nowMs - l.createdAt) / 60000);
  const age = l => (mins(l) < 90 ? `${mins(l)} min` : `${Math.round(mins(l) / 60)} h`);
  const head = rows.length === 1
    ? ':hourglass_flowing_sand: *New lead with no contact yet*'
    : `:hourglass_flowing_sand: *${rows.length} new leads with no contact yet*`;
  const lines = rows.map(l => `• <${l.url}|${l.name || 'Unnamed lead'}>`
    + (l.phones[0] ? ` · ${l.phones[0].slice(0, 3)}-${l.phones[0].slice(3, 6)}-${l.phones[0].slice(6)}` : '')
    + ` · waiting ${age(l)}${l.overnight ? ' (came in after hours)' : ''}`);
  return [head, ...lines, '_Nobody has called, texted or emailed them yet._'].join('\n');
}

if (typeof module !== 'undefined') module.exports = { newLeads, reachedOut, staleLeadSlack, leadContacts, sentMailQuery, STALE_MIN, LEAD_LOOKBACK_H, CONTACT_BEFORE_H };
