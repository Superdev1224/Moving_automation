// What the ClickUp board's statuses mean. Shared by everything that has to answer "is this job
// actually booked?" - the team drives cards through several statuses after a booking, so the
// answer is "moves booked or one of the statuses after it", not "equals 'moves booked'".
//
// By NAME only. Until 2026-10-26 a status at or past the board's position 9 also counted: the team
// then added "quote sent" and "negotiation review", which pushed "date on hold" and "onsite estimate"
// to positions 9 and 10, and an onsite-estimate customer (Joseph Oluwole) was emailed "You're booked".
// A status the team adds later is never booked until it is named here.

const BOOKED_ONWARD = ['moves booked', 'upsell in progess', 'interested upselling', 'upsold customers',
  'moves complete', 'moves complete not paid', 'moves complete no review', 'moves complete + paid', 'affrim offered',
  // a booked influencer lives on the "All Influencer Leads" board: content wait = booked, content done = moved
  'influencers content wait', 'influencers content done'];

function isBooked(card) {
  const st = (card && card.status) || {};
  const name = String(st.status || '').toLowerCase().trim();
  if (String(st.type || '') === 'closed') return false;        // moves lost
  return BOOKED_ONWARD.includes(name);
}

if (typeof module !== 'undefined') module.exports = { isBooked, BOOKED_ONWARD };
