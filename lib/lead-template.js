// The client's lead template (Additional Integrations #1), filled from whatever the lead source
// gives us. Used for the #inbound-leads post and the ClickUp card, so both look the same.

const TEMPLATE = [
  ['Name', l => l.name],
  ['Notes', l => l.notes],
  null,
  ['Date of Move', l => l.dateText],
  ['Starting Time', l => l.time],
  null,
  ['Moving From', l => l.from],
  ['Residence Type', l => l.fromType],
  ['Apt Number', l => l.fromApt],
  ['Flights of Stairs (Are there any turns?)', l => l.fromStairs],
  ['Loading Distance To Truck', l => l.fromDistance],
  ['Access & Obstacles', l => l.fromAccess],
  null,
  ['Moving To', l => l.to],
  ['Residence Type', l => l.toType],
  ['Apt Number', l => l.toApt],
  ['Flights of Stairs - (Are there any turns?)', l => l.toStairs],
  ['Unloading Distance To Truck', l => l.toDistance],
  ['Access & Obstacles', l => l.toAccess],
  null,
  ['Inventory List', l => l.inventory],
  ['Packing or Unpacking', l => l.packing],
  ['Moving Supplies', l => l.supplies],
  ['Supplies Rental', l => l.rental],
  ['Junk Removal', l => l.junk],
  ['Storage', l => l.storage],
  ['How Did You Hear About Us', l => l.source],
  ['Record Footage?', l => l.footage],
  null,
  ['Estimated Time', () => ''],
  ['Estimated Cost', () => ''],
  null,
  ['Insurance Paid', () => ''],
  ['Deposit Paid', () => ''],
  null,
  ['Email', l => l.email],
  ['Phone Number', l => l.phone],
  ['Contact Name', l => l.name],
  ['Payment Phone Number', () => ''],
];

const tidy = s => String(s || '').replace(/\s+/g, ' ').trim();

// Spread a lead (from parse-lead.js or a webhook) over the template's fields.
let templateValues = function (lead) {
  const get = re => (lead.details || []).filter(([k]) => re.test(k)).map(([, v]) => tidy(v)).join('; ');
  const iso = String(lead.date || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  const text = [get(/message|details|note/i), get(/^size/i) && `Size: ${get(/^size/i)}`, get(/service/i) || lead.service]
    .filter(Boolean).join(' · ');
  const any = re => re.test(`${text} ${get(/special|details|message/i)}`);
  return {
    name: tidy(lead.name), email: tidy(lead.email), phone: tidy(lead.phone), source: lead.source,
    notes: text,
    dateText: iso ? `${+iso[2]}/${+iso[3]}` : tidy(lead.date),
    time: tidy(lead.time),
    from: tidy(lead.from || lead.location), to: tidy(lead.to),
    fromType: /house/i.test(text) ? 'House' : /apartment|apt\b/i.test(text) ? 'Apartment' : /storage/i.test(tidy(lead.from)) ? 'Storage' : '',
    toType: '',
    fromStairs: get(/stairs \(pickup\)/i), toStairs: get(/stairs \(drop-off\)/i),
    inventory: get(/special items/i),
    packing: get(/packing|prep/i),
    junk: any(/junk/i) ? 'Yes (mentioned)' : '',
    storage: any(/storage/i) ? 'Yes (mentioned)' : '',
  };
};

// Lines a form fills in itself (the website's Helium forms through Zapier, 2026-10-01) win over the
// guesses above. Inventory keeps its line breaks; everything else is one line.
const TEMPLATE_KEYS = ['fromType', 'fromApt', 'fromStairs', 'fromDistance', 'fromAccess', 'toType', 'toApt', 'toStairs',
  'toDistance', 'toAccess', 'inventory', 'packing', 'supplies', 'rental', 'junk', 'storage', 'footage', 'source', 'time'];
const baseValues = templateValues;
templateValues = function (lead) {
  const v = baseValues(lead), o = (lead && lead.template) || {};
  for (const k of TEMPLATE_KEYS) {
    const x = String(o[k] ?? '').trim();
    if (x) v[k] = k === 'inventory' ? x.replace(/\n{3,}/g, '\n\n') : tidy(x);
  }
  return v;
};

// a list (an inventory) starts on the line under its label
const multi = x => (String(x || '').includes('\n') ? '\n' + x : (x || ''));

// Slack: bold labels, blanks kept so the team can copy it straight into ClickUp / Movegistics.
function templateSlack(lead) {
  const v = templateValues(lead);
  return TEMPLATE.map(row => (row ? `*${row[0]} -* ${multi(row[1](v))}`.trimEnd() : '')).join('\n');
}

// ClickUp card description: the same template in the team's own markdown style.
function templateMarkdown(lead, footer) {
  const v = templateValues(lead);
  const lines = TEMPLATE.map(row => (row ? `**${row[0]} -** ${multi(row[1](v))}`.trimEnd() : ''));
  if (footer) lines.push('', `_${footer}_`);
  return lines.join('\n');
}

if (typeof module !== 'undefined') module.exports = { templateValues, templateSlack, templateMarkdown, TEMPLATE };
