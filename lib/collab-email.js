// The collaboration email for influencers (client's wording, updated 2026-10-26 - do not reword
// without asking). It goes out by itself as soon as an influencer gives us their email, because the team
// kept forgetting to send it by hand.

const COLLAB_SUBJECT = 'Express Haulers Moving – Collaboration Details';

// The client's new wording (2026-10-26) replaces the first one. It has no "circle back" line, so the
// move timeframe no longer changes the email; moveKnown is kept so callers do not change.
function collabEmail(moveKnown = false) {
  return `Hi there!

Thank you for your interest in collaborating with us! We’re excited about the opportunity to work together and build a partnership that benefits both of us.

About Us
Express Haulers Moving is a professional moving company based in Wayne, New Jersey, serving New Jersey, New York, and Pennsylvania. We specialize in residential and long-distance relocations, providing experienced crews, full-service moving support, and reliable, customer-focused service.

Collaboration Overview
We offer a significant discount on your moving services in exchange for authentic content showcasing your experience with our team on moving day.
The collaboration typically includes:

• An Instagram Reel and TikTok video featuring your moving experience
• Tagging @ExpressHaulers on TikTok and send collab invites to @Expresshaulers & @Expresshaulersmovers on Instagram
• Uploading the collaboration video to Instagram and TikTok within seven (7) days after your move is required as part of the partnership 

Our goal is to share a genuine, behind-the-scenes look at your moving experience and the service our team provides.

Earn 5% Commission on Every Successful Referral!
There’s more! Once you book your move with us, you choose your own unique REFERRAL CODE to include in your TikTok & Instagram video captions and stories! You can refer friends, family, or followers who need moving services and earn 5% commission for every successful book, while your referral gets 10% discount on their move!
You can reshare your video to your page and stories as many times as you like! A great opportunity to turn your collaboration into an ongoing partnership while helping others enjoy a smoother, stress-free moving experience.
We’d love to work with you! Let us know if you’re interested, and we can discuss the next steps.

Best regards,
Express Haulers Moving Team`;
}

const EMAIL_IN = /[\w.+-]+@[\w-]+(\.[\w-]+)*\.[a-z]{2,}/i;

// Is this an influencer giving us their email? Either the message itself was read as an influencer's,
// or it is a reply in a chat where we asked for an email "to send our collaboration details to".
function collabRecipient(j, conversation) {
  const email = ((String(j.message || '').match(EMAIL_IN) || [])[0] || (j.pushedLead || {}).email || '').toLowerCase();
  if (!email) return null;
  const msgs = ((conversation?.data || [])[0]?.messages?.data) || [];
  const weAsked = msgs.some(m => String(m.from?.id || '') !== String(j.senderId || '')
    && /collaboration details/i.test(m.message || ''));
  return j.kind === 'influencer' || weAsked ? email : null;
}

// Did the creator say WHEN they move, in this message or earlier in the chat? A month ("in May", not
// "I may"), "next year", a season, "end of the year" or a date like 11/20.
const WHEN = /\b(january|february|march|april|june|july|august|september|october|november|december|(jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\.?\s*\d{1,2}\b|(in|early|mid|late|end of) may\b|may \d{1,2}\b|next (year|month)|(this|next|in the|by) (spring|summer|fall|autumn|winter)|end of (the )?(year|month)|\d{1,2}\/\d{1,2})/i;
function moveTimeKnown(j, conversation) {
  if (String((j.pushedLead || {}).date || '').trim()) return true;
  const msgs = ((conversation?.data || [])[0]?.messages?.data) || [];
  const theirs = msgs.filter(m => String(m.from?.id || '') === String(j.senderId || '')).map(m => m.message || '');
  return [j.message, ...theirs].some(t => WHEN.test(String(t || '')));
}

if (typeof module !== 'undefined') module.exports = { COLLAB_SUBJECT, collabEmail, collabRecipient, moveTimeKnown };
