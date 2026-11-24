// Damage claims (item #6): a customer emails about damage -> ClickUp card filled in with the
// client's MOVING COMPLAINT INTAKE FORM + alert in Slack #sales-issues-and-damages.
// Keyword prefilter first (cheap), then Claude classifies and pulls out the details.

// a claim can also be a charge the customer thinks is wrong (client, 2026-09-30)
const WORDS = /(damag|broke|broken|scratch|dent|crack|chip|ruin|torn|stain|shatter|missing|lost|claim|complain|overcharg|charged|refund|dispute|mistake|wrong amount|double|too much|reimburs)/i;

function isClaimCandidate(msg) {
  const text = `${msg.subject || ''}\n${msg.text || ''}`;
  if (!WORDS.test(text)) return false;
  // certificate-of-insurance threads and invoice threads are routine, not claims
  if (/certificate of insurance|^(re:|fw:)?\s*coi\b/i.test(msg.subject || '')) return false;
  return true;
}

const SYSTEM = [
  'You read emails sent to a moving company and decide whether the email is a CLAIM from a customer:',
  'damaged, broken, scratched or lost belongings or damage to a property caused by the move; MISSING items;',
  'or a CHARGE they believe is wrong (overcharged, charged twice, billed for hours or items they dispute,',
  'a refund of a charge). Requests for a certificate of insurance, a copy of an invoice or how to pay,',
  'quote requests, scheduling messages and marketing are NOT claims.',
  'For a disputed charge: itemDamaged = what was charged (e.g. "Charge: 2 extra hours, $390"), totalValue =',
  'the amount they dispute, requestType = Reimbursement if they want money back.',
  'Extract only what the email actually says. Use null for anything not stated. Do not invent values.',
  'Answer with JSON only, in this exact shape:',
  '{"claim":true|false,"why":"<10 words max>","customerName":null,"email":null,"phone":null,',
  '"moveDate":null,"movingFrom":null,"movingTo":null,"issueCategory":"<e.g. Damaged furniture, Property damage, Missing items, Disputed charge>",',
  '"items":[{"item":null,"conditionBefore":null,"estimatedValue":null}],"summary":"<2 sentences>","customerRequest":null,',
  // the fields of the team's Damage / Loss Claim Form (2026-09-30)
  '"itemDamaged":"<short list of what was damaged or lost>","whatHappened":"<how it happened and when it was found, in their words>",',
  '"totalValue":<number or null, only if they state amounts>,',
  '"conditionBeforeMove":"No Damage|Pre-existing|Unknown|null","requestType":"Repair|Replacement|Reimbursement|Other|null",',
  '"hasInsurance":"Yes|No|Unknown|null",',
  '"coveragePurchased":"Basic Coverage - $1 Per Pound|Full Value - 0 Deductible|Full Value - $250 Deductible|Full Value - $500 Deductible|null"}',
].join(' ');

function claudeBody(msg, model = 'claude-opus-5') {
  const body = [
    `From: ${(msg.from && msg.from.value && msg.from.value[0] && msg.from.value[0].address) || ''}`,
    `Subject: ${msg.subject || ''}`,
    `Date: ${msg.date || ''}`,
    '',
    (msg.text || '').slice(0, 6000),
  ].join('\n');
  return { model, max_tokens: 1200, system: SYSTEM, messages: [{ role: 'user', content: body }] };
}

function parseClaude(res) {
  const txt = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join(' ');
  const m = txt.match(/\{[\s\S]*\}/);
  if (!m) return { claim: false, why: 'no answer from Claude' };
  try { return JSON.parse(m[0]); } catch { return { claim: false, why: 'unreadable answer from Claude' }; }
}

const val = v => (v === null || v === undefined || v === '' ? '' : String(v));

// The client's intake form, filled in with whatever the email stated.
function renderIntake(f, msg) {
  const from = (msg.from && msg.from.value && msg.from.value[0] && msg.from.value[0].address) || '';
  const received = msg.date ? new Date(msg.date).toLocaleString('en-US', { timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short' }) : '';
  const items = (f.items || []).filter(i => i && (i.item || i.estimatedValue || i.conditionBefore));
  const lines = [
    '## MOVING COMPLAINT INTAKE FORM',
    '**Status:** Under Review',
    '',
    `**Move Date:** ${val(f.moveDate)}`,
    `**Moving From:** ${val(f.movingFrom)}`,
    `**Moving To:** ${val(f.movingTo)}`,
    `**Email:** ${val(f.email) || from}`,
    `**Phone Number:** ${val(f.phone)}`,
    '**Crew Members Assigned:**',
    '',
    '### Complaint Details',
    `- **Date Complaint Received:** ${received}`,
    '- **Received By:** Automation (email)',
    '- **Method of Complaint:** Email',
    '',
    `**Issue Category:** ${val(f.issueCategory)}`,
    '',
    '### Affected Item(s) or Area(s)',
  ];
  if (items.length) {
    for (const i of items) {
      lines.push(`- **Item/Area:** ${val(i.item)}`);
      lines.push(`  - Condition Before Move: ${val(i.conditionBefore)}`);
      lines.push(`  - Estimated Value: ${val(i.estimatedValue)}`);
    }
  } else {
    lines.push('- **Item/Area:**', '  - Condition Before Move:', '  - Estimated Value:');
  }
  lines.push('', '### Summary', val(f.summary), '', `**Customer asked for:** ${val(f.customerRequest)}`,
    '', '### RESOLUTION', '', '---', `_Original email from ${from}${msg.attachmentCount ? ` — ${msg.attachmentCount} attachment(s) in Gmail (photos are not copied here)` : ''}_`,
    '', '```', (msg.text || '').slice(0, 3000).trim(), '```');
  return lines.join('\n');
}

// Is this claim already on the board? Customers write from more than one address (husband, wife,
// work account), so the sender alone is not enough: match the contact details in the card too.
function matchClaimOnBoard(tasks, f, msg) {
  const from = ((msg.from && msg.from.value && msg.from.value[0] && msg.from.value[0].address) || '').toLowerCase();
  const email = String(val(f.email) || '').toLowerCase();
  const phone = String(val(f.phone) || '').replace(/\D/g, '').slice(-10);
  const name = String(val(f.customerName) || '').toLowerCase().trim();
  const hay = t => `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '').toLowerCase();
  const open = (tasks || [])
    .filter(t => !['closed', 'resolved', 'complete'].includes(String(t.status?.status || '').toLowerCase()))
    .sort((a, b) => +(b.date_updated || 0) - +(a.date_updated || 0));
  return open.find(t => email && hay(t).includes(email))
    || open.find(t => from && hay(t).includes(from))
    || open.find(t => phone.length === 10 && hay(t).replace(/\D/g, '').includes(phone))
    || open.find(t => name.length > 5 && hay(t).includes(name))
    || null;
}

function cardName(f, msg) {
  const sender = (msg.from && msg.from.value && msg.from.value[0]) || {};
  const fallback = sender.name || (sender.address || '').split('@')[0] || 'Unknown customer';
  const who = val(f.customerName) || fallback;
  const cat = val(f.issueCategory) || 'Claim';
  const when = val(f.moveDate) ? ` | move ${val(f.moveDate)}` : '';
  return `${who} | ${cat}${when}`;
}

function slackText(f, msg, url) {
  const items = (f.items || []).filter(i => i && i.item).map(i => i.item + (i.estimatedValue ? ` (${i.estimatedValue})` : '')).join(', ');
  const from = (msg.from && msg.from.value && msg.from.value[0] && msg.from.value[0].address) || '';
  let s = `:rotating_light: *New damage claim*: ${val(f.customerName) || from}\n`;
  if (f.issueCategory) s += `*Issue:* ${f.issueCategory}\n`;
  if (items) s += `*Items:* ${items}\n`;
  if (f.moveDate) s += `*Move date:* ${f.moveDate}\n`;
  if (f.phone || f.email || from) s += `*Contact:* ${[val(f.phone), val(f.email) || from].filter(Boolean).join(' · ')}\n`;
  if (f.summary) s += `${f.summary}\n`;
  if (url) s += `<${url}|Open the claim card in ClickUp>`;
  return s;
}

if (typeof module !== 'undefined') module.exports = { matchClaimOnBoard, isClaimCandidate, claudeBody, parseClaude, renderIntake, cardName, slackText, WORDS };
