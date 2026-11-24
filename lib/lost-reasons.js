// Why leads are lost (Additional Integrations #7).
// When a card lands in "Moves Lost" the reason is almost never written down, but the team's own
// notes usually say it: "left vm and text", "went with someone cheaper", "date not available".
// Claude reads the card and its comments, picks one reason from a fixed list, and writes it back on
// the card so the monthly report can count them.

const REASONS = [
  'Price - too expensive',
  'Went with another company',
  'Date not available',
  'No response - never reached them',
  'Cancelled or postponed',
  'Moving themselves',
  'Outside our area or service',
  'Not a real lead',
  'Other',
];
const MARK = '📉 Lost reason:';          // also how we know a card has already been done

const lostReasonSystem = () => [
  'A New Jersey moving company marks a lead "Moves Lost" when it will not become a booking.',
  'You are given the card and the notes the team left on it. Say why the lead was lost.',
  'Choose exactly one reason from this list:', REASONS.join(' | '),
  'Guidance: repeated "left voicemail", "no answer", "left text" with no reply = "No response - never reached them".',
  '"Booked with", "went with", "found someone cheaper" = "Went with another company".',
  'Complaints about the quote, "too expensive", asking for a discount then silence = "Price - too expensive".',
  'We could not do their date, or we were fully booked = "Date not available".',
  'Renting a truck, friends helping, "we will do it ourselves" = "Moving themselves".',
  'Move on hold, lease fell through, closing delayed, "maybe later" = "Cancelled or postponed".',
  'Out of state, too small, a service we do not offer = "Outside our area or service".',
  'Spam, wrong number, a supplier or a job application = "Not a real lead".',
  'If the notes do not say, answer "Other" and keep the note short and honest.',
  'Answer with JSON only: {"reason":"<one from the list>","note":"<max 12 words, what actually happened>",',
  '"confident":true|false}',
].join(' ');

// Cards come in batches: one Claude call reads several at once, which keeps the backfill cheap.
function lostReasonBody(cards, model = 'claude-opus-5') {
  const blocks = cards.map(c => `id: ${c.id}\nname: ${c.name}\nnotes: ${c.notes}`).join('\n---\n');
  return { model, max_tokens: 4000, system: lostReasonSystem(),
    messages: [{ role: 'user', content: `Cards (${cards.length}). Answer with {"items":[{"id":...,"reason":...,"note":...,"confident":...}]}\n\n${blocks}` }] };
}

function parseLostReasons(res) {
  const text = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  const m = text.match(/\{[\s\S]*\}/);
  try {
    const items = m ? (JSON.parse(m[0]).items || []) : [];
    return items.filter(i => i && i.id).map(i => ({
      id: String(i.id),
      reason: REASONS.includes(i.reason) ? i.reason : 'Other',
      note: String(i.note || '').slice(0, 90),
      confident: i.confident !== false,
    }));
  } catch (e) { return []; }
}

// What the AI is given: the card plus the team's own notes, newest last.
function cardContext(task, comments) {
  const notes = (comments || [])
    .map(c => String(c.comment_text || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean).slice(-12).join(' // ');
  const desc = String(task.markdown_description || task.description || '')
    .replace(/\*\*/g, '').replace(/\s+/g, ' ').slice(0, 400);
  return { id: task.id, name: (task.name || '').slice(0, 80), url: task.url,
    notes: `${desc}${desc && notes ? ' // ' : ''}${notes}`.slice(0, 1400) || '(no notes on the card)',
    alreadyDone: (comments || []).some(c => String(c.comment_text || '').includes(MARK)),
    lostAt: +task.date_updated || 0 };
}

const lostComment = r => `${MARK} ${r.reason}${r.note ? ` - ${r.note}` : ''}`
  + (r.confident ? '' : ' (best guess from the notes)');

// One row per lost lead for the spreadsheet.
const sheetRow = (card, r) => ({
  'Lost on': new Date(card.lostAt).toLocaleDateString('en-US', { timeZone: 'America/New_York' }),
  Customer: (card.name || '').split('|')[0].trim(),
  Reason: r.reason,
  'What happened': r.note,
  Card: card.url,
  Certain: r.confident ? 'yes' : 'best guess',
});

function monthlyReport(rows, monthName) {
  const counts = {};
  for (const r of rows) counts[r.Reason] = (counts[r.Reason] || 0) + 1;
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const total = rows.length || 1;
  const lines = sorted.map(([reason, n]) =>
    `• *${reason}* — ${n} (${Math.round((n / total) * 100)}%)`);
  return [`:bar_chart: *Why we lost leads in ${monthName}* — ${rows.length} in total`, ...lines].join('\n');
}

if (typeof module !== 'undefined') module.exports = { REASONS, MARK, lostReasonBody, parseLostReasons, cardContext, lostComment, sheetRow, monthlyReport };
