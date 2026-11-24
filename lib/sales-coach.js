// Sales Call Coaching (#31, 2026-11-16) — client PDF "AI Automations", item 5.
// Every recorded sales call is already transcribed and scored hot/warm/cold in #lead-scores (#1/#3).
// This adds coaching against the company's own "Sales Script (Assume The Sale)" and "Sales Objection
// Playbook" (both in contact@'s Google Drive): what worked, four 1-5 ratings, 1-3 improvements with the
// line to say instead, and the best next move. Posted in the thread of that call's #lead-scores post.

const COACH_MIN_SECONDS = 120;          // shorter calls are callbacks, voicemails or "can I call you later"

const COACH_RULES = `
EXPRESS HAULERS SALES SCRIPT ("Assume The Sale"):
1. Intro: "Hi, this is <name> from Express Haulers Moving. Thank you for reaching out! Who am I speaking with today?" Use their name.
2. Control/transition: "I will walk you through everything. I just need a few quick details so I can give you an accurate quote." Stay in control; if they jump to price early, answer briefly and redirect ("Absolutely, I'll go over pricing with you. I just want to get a few quick details first...").
3. Availability micro-commitment: "What date are you looking to move?" ... "Let me check availability." ... "We do have that date available as of right now."
4. Discovery: gather all move details (addresses, floors/stairs/elevator, inventory, packing, special items) and ask about their CONCERN: "Is there anything you're concerned about or want us to be extra careful with? Any glass, ceramic or delicate items?" Then address that concern FIRST.
5. Build value: experienced regular movers (5+ years average), wrap furniture, protect floors and walls, disassembly/reassembly, truck/equipment/blankets/wrapping included, placed in the right rooms, fully licensed and insured. Pause and let it land.
6. Assumptive close transition: "Based on what you described, we handle moves like this all the time. Would you prefer a morning or afternoon arrival window?"
7. Send the estimate WHILE ON THE CALL and walk them through it ("Did you receive the email? Subject: Express Haulers Moving Agreement... go ahead and open it"). Price reveal: "__ movers at $__ per hour, includes truck, equipment, blankets and travel time; billed on actual working time. How does that sound so far?"
8. Deposit + signature: "the small $50 reservation deposit reserves your date. Once you sign it and place the deposit your move date is fully reserved." Then stay quiet and let them complete it.
9. Discount = LAST RESORT only (strong lead, likely to lose the move, value already established). Monthly payments may be offered (never say "financing", "loan" or "debt").
10. After booking, upsell packing (fragile/kitchen packing or full packing) and junk removal.

OBJECTION PLAYBOOK ("Assume the customer is ready to move forward"):
Method: Listen (let them finish) -> Acknowledge ("Absolutely, I understand") -> Clarify (ONE question to find the real concern) -> Educate/Reframe (answer the actual issue) -> Confirm ("Does that address your concern?") -> Assume forward movement ("Perfect, let's get the date secured").
Golden rules: never rebut before understanding; one good question beats five rebuttals; an objection is not a rejection; stay calm and confident.
- "Another company is cheaper": ask how much and whether it's the same move; compare crew, hours, protection, travel, materials, coverage.
- "Comparing prices": compare value and total cost, not the number.
- "Flat-rate quote": ask what's included; flat rates change when the scope changes.
- "Hourly rate too high": ask compared to what; experienced crews are faster and careful.
- "Can you give a discount?": ask "Is price the only thing keeping you from moving forward?" Solve the real concern first; discount only to close a qualified booking.
- "Need to talk to spouse": ask what they'll want to know; offer to send the estimate; offer a tentative hold.
- "Need time / I'll think about it": ask what part they're thinking over; don't accept a soft no; if they truly need time, set a SPECIFIC follow-up date AND time.
- "Waiting for another quote / just getting estimates": side with them, teach what to compare, stay in control, set a specific follow-up.
- "Bad experience before": ask what happened; show how we prevent it.
- "Not ready for a deposit": deposit secures crew and date; date can change with 4 days' notice; dates fill fast (weekends, month-end).
- "Don't know exact date": take the general timeframe; date can change with 4 days' notice; tentative hold or follow-up.
- "Hidden fees": walk through what's included and what could change the price.
- "Building/landlord": we provide a COI and work with the building; follow up after they confirm.
- Multiple stops/long distance/stairs: gather details so the estimate is accurate.
NEVER SAY: "they're probably a bad company", "you don't need to ask your spouse", "just call us back if you decide", "that's just our price", "we can discount it right now".
DO NOT: interrupt, get defensive, argue, sound desperate or pushy, rush, ignore objections, discount immediately, keep rebutting after they clearly need time.
Power questions: "What part are you most concerned about?", "If we clear that up, are you comfortable moving forward?", "Would later today or tomorrow be better for a follow-up?"
`;

function coachSystem() {
  return [
    'You coach phone sales reps at Express Haulers Moving (New Jersey) using the company\'s own script and objection',
    'playbook below. You get one recorded sales call (machine transcript: read past small errors; speaker turns may',
    'be unlabeled, work out who is the rep). Be specific to THIS call: quote or paraphrase what was actually said.',
    'Be fair and encouraging but honest. Reps read this on a phone: plain simple words, SHORT - every field has a word limit, respect it.',
    'Ratings 1-5 (5 = textbook): control (intro, used their name, stayed in control, availability check, discovery',
    'incl. their concern), value (explained what is included and how we protect their things, tied to their concern),',
    'objections (listen-acknowledge-clarify-educate-confirm; no early discount; null if no objection came up), close',
    '(assumed the sale: arrival window, sent/walked through the estimate on the call, asked for the $50 deposit; or',
    'set a SPECIFIC follow-up date and time).',
    'If the call was not a real sales conversation (wrong number, existing customer logistics, voicemail, vendor),',
    'answer {"coachable":false}.',
    'Answer with JSON only: {"coachable":true,"rep":"<first name if said, else null>","worked":"<what they did well, max 20 words>",',
    '"ratings":{"control":1-5,"value":1-5,"objections":1-5|null,"close":1-5},',
    '"improve":[{"tip":"<the fix as a short headline, max 8 words, e.g. Ask what worries them>","moment":"<what happened on this call, max 20 words>","say":"<the line from the script/playbook to say instead, max 30 words>"}] (1-3 items, most important first),',
    '"nextMove":"<the single best next step for this lead, with a day/time if one was discussed, max 25 words>"}',
    '', 'COMPANY PLAYBOOK:', COACH_RULES,
  ].join(' ');
}

function coachBody(call, model = 'claude-opus-5') {
  const head = `${call.direction} call, ${Math.round((call.duration || 0) / 60)} min, ${call.when}. Company side: ${call.rep || 'unknown'}. Customer: ${call.customer || 'unknown'}.`;
  return { model, max_tokens: 1500, system: coachSystem(),
    messages: [{ role: 'user', content: `${head}\n\nTranscript:\n${String(call.transcript || '').slice(0, 30000)}` }] };
}

function parseCoach(res) {
  const txt = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  const m = txt.match(/\{[\s\S]*\}/);
  try { return JSON.parse(m ? m[0] : 'null'); } catch { return null; }
}

// 2026-11-18 client: the first layout was hard to read (one dense block). Now: blank lines between parts,
// a coloured score per skill, each tip numbered with a bold headline and the line to say on its own line.
const score = n => (n == null ? ':white_circle: –' : `${n >= 4 ? ':large_green_circle:' : n >= 3 ? ':large_yellow_circle:' : ':red_circle:'} ${Math.round(n)}/5`);
const NUM = [':one:', ':two:', ':three:'];

function coachSlack(c) {
  if (!c || !c.coachable) return '';
  const r = c.ratings || {};
  // the salesperson's name is on the lead score above (client 2026-11-20), not repeated here
  const lines = [':mortar_board: *Call coaching*', '',
    `:white_check_mark: *Did well:* ${c.worked || ''}`, '',
    `*Control* ${score(r.control)}   *Value* ${score(r.value)}   *Objections* ${score(r.objections)}   *Close* ${score(r.close)}`];
  const tips = (c.improve || []).slice(0, 3);
  if (tips.length) lines.push('', '*Work on:*');
  tips.forEach((i, k) => {
    if (k) lines.push('');
    lines.push(`${NUM[k]} *${i.tip || 'Next time'}*${i.moment ? ` - ${i.moment}` : ''}`);
    if (i.say) lines.push(`>:speech_balloon: _"${String(i.say).replace(/^["“]|["”]$/g, '')}"_`);
  });
  if (c.nextMove) lines.push('', `:dart: *Next step:* ${c.nextMove}`);
  return lines.join('\n');
}

// The #lead-scores post for this call: same customer number (last 10 digits), posted within 30 minutes after the call.
function leadPostFor(call, posts) {
  const ten = String(call.customerNumber || '').replace(/\D/g, '').slice(-10);
  if (ten.length !== 10) return null;
  const start = Date.parse(call.startTime) / 1000;
  return (posts || []).filter(p => String(p.text || '').replace(/\D/g, '').includes(ten)
      && +p.ts >= start - 300 && +p.ts <= start + (call.duration || 0) + 1800)
    .sort((a, b) => +a.ts - +b.ts)[0] || null;
}

if (typeof module !== 'undefined') module.exports = { coachBody, parseCoach, coachSlack, leadPostFor, COACH_MIN_SECONDS, coachSystem };
