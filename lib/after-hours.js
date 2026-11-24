// After-hours reply to new leads (Additional Integrations #9).
// Wording approved by the client on 2026-09-24 — do not reword without asking them.
// Only brand-new leads that arrived between 8 PM and 8 AM, only once, and never when someone on the
// team has already answered them. Customers we are already talking to get nothing.

const CLOSED_FROM = 20, CLOSED_TO = 8;      // 8 PM until 8 AM, New York time (client, 2026-10-08; was 9 PM)
const LEAD_CARD = /\|\s*(website|yelp|thumbtack)\s+lead/i;   // cards our lead automation created

const nyHourOf = ms => +new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hourCycle: 'h23' }).format(new Date(ms));
const isClosed = ms => { const h = nyHourOf(ms); return h >= CLOSED_FROM || h < CLOSED_TO; };
const firstName = full => String(full || '').trim().split(/\s+/)[0] || 'there';
const ten = s => String(s || '').replace(/\D/g, '').slice(-10);

// ClickUp cards written by the lead automations carry the customer's email and phone.
// Yelp leads are answered at any hour (client, 2026-10-10: "yelp reply automatically 24/7, just Yelp");
// website and Thumbtack only when they arrive while we are closed.
const ALWAYS_REPLY = ['yelp'];
// Now that it runs every 10 minutes around the clock, only a card under an hour old gets a reply: an
// older one was either answered already (maybe inside Yelp, which we cannot see) or missed by an outage.
const FRESH_MIN = 60;
const sourceOf = t => ((t.name || '').match(LEAD_CARD) || [])[1]?.toLowerCase() || 'website';

function afterHoursLeads(tasks, nowMs) {
  return (tasks || [])
    .filter(t => LEAD_CARD.test(t.name || ''))
    .filter(t => isClosed(+t.date_created) || ALWAYS_REPLY.includes(sourceOf(t)))
    .filter(t => (nowMs || Date.now()) - +t.date_created < FRESH_MIN * 60000)
    .map(t => {
      const text = `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '');
      // a Yelp card no longer carries Yelp's relay address, only its chat id: our first reply goes to it
      const yelpChat = (text.match(/Yelp chat id:\s*([0-9a-f]{8,})/i) || [])[1];
      const email = (text.match(/[\w.+-]+@[\w.-]+\.\w{2,}/) || [''])[0].toLowerCase()
        || (yelpChat ? `leadsapi+${yelpChat.toLowerCase()}@messaging.yelp.com` : '');
      const phone = (text.match(/(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)/) || []).slice(1).join('');
      const source = sourceOf(t);
      return { taskId: t.id, url: t.url, name: (t.name || '').split('|')[0].trim(), source,
               first: firstName((t.name || '').split('|')[0]), email, phone, createdAt: +t.date_created };
    })
    .filter(l => l.email || l.phone);
}

const EMAIL_SUBJECT = 'Thanks for reaching out to Express Haulers Moving';

// Yelp and Thumbtack leads get Diego's own message instead (client, 2026-10-06, "only for yelp and
// thumbtack"). Yelp's is emailed to its relay address and shows up in the Yelp conversation;
// Thumbtack sends no email, so theirs goes out as a text.
const MARKETPLACES = ['yelp', 'thumbtack'];
const isMarketplace = lead => MARKETPLACES.includes(String(lead.source || '').toLowerCase());
function marketplaceReply(lead) {
  return `Hey ${lead.first},

Thank you for reaching out. It's Diego, the owner of Express Haulers Moving.
We're offering 10% off to customers that book this week.

To get an instant, upfront quote with a discount applied, visit our website here:
https://www.expresshaulersmoving.com

Or call/text 862-379-9113 if you'd rather speak to me or my team directly.`;
}

function afterHoursEmail(lead) {
  if (isMarketplace(lead)) return marketplaceReply(lead);
  return `Hi ${lead.first},

Thanks for reaching out to Express Haulers Moving! We received your request, and someone from our team will give you a call first thing in the morning, usually Between 8:00AM - 10:00 AM.

If you haven't already, feel free to reply with the following information so we can start preparing your quote before we call:

• Move date
• Moving from and to including apartment/floor and stairs or elevator access
• What needs to be moved a rough inventory is perfectly fine
• Additional services packing, unpacking, junk removal, or storage

If you've already provided this information, there's no need to send it again. Just be on the lookout for our call. We usually call from (862) 379-9113.

Talk soon,

Express Haulers Moving
(862) 379-9113
contact@expresshaulers.com`;
}

function afterHoursText(lead) {
  // Diego's message exactly as written - the client asked for no opt-out line (2026-10-06)
  if (isMarketplace(lead)) return marketplaceReply(lead);
  return `Hi ${lead.first}, Express Haulers Moving here! Thanks for reaching out. We're currently closed, but we'll call you first thing in the morning.

If you haven't already, feel free to reply with your move date and addresses so we can start preparing your quote.

Reply STOP to opt out.`;
}

// Has anyone here already answered this lead? Then the automation stays out of the way.
function alreadyAnswered(lead, calls, texts, sentCount) {
  if (sentCount > 0) return true;
  const after = ms => ms > lead.createdAt;
  const theirs = n => lead.phone && ten(n) === lead.phone;
  return (calls || []).some(c => after(Date.parse(c.startTime))
      && ((c.direction === 'Outbound' && theirs(c.to?.phoneNumber))
        || (c.direction === 'Inbound' && c.result === 'Accepted' && theirs(c.from?.phoneNumber))))
    || (texts || []).some(m => m.direction === 'Outbound' && after(Date.parse(m.creationTime))
        && (m.to || []).some(t => theirs(t.phoneNumber)));
}

function afterHoursPreview(lead, sendEmail, sendText) {
  const bits = [sendEmail ? `email to ${lead.email}` : '', sendText ? `text to ${lead.phone}` : ''].filter(Boolean);
  return `:crescent_moon: *After-hours reply* to <${lead.url}|${lead.name}> — ${bits.join(' and ') || 'nothing to send'}`;
}

if (typeof module !== 'undefined') module.exports = { afterHoursLeads, afterHoursEmail, afterHoursText, alreadyAnswered, afterHoursPreview, isClosed, EMAIL_SUBJECT, firstName };
