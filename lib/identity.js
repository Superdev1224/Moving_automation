// Who is this caller? (client, 2026-10-03: "customer identity should be resolved before scoring")
// 1. their number on a card - All Leads, Influencers and Realtors, lost and completed cards included
// 2. their name (caller ID or what they said) against card names, spelling-tolerant: the team types
//    names as they hear them (Sophia Hrynowicz -> "Sofia Ohrynowicz", Angela Chanco -> "Angela",
//    Lauren Halford -> "Lauren Hufford"), and many cards get the phone number added only later.
// Results: matched (phone / name), possible (only a first name agrees), none, or unknown (ClickUp
// could not be read) - "no card" and "could not tell" are different answers.

const tenDigits = s => String(s || '').replace(/\D/g, '').slice(-10);
const GENERIC_CALLER = /wireless caller|unknown|anonymous|unavailable|private|toll free|^\s*[a-z .]+\s+[a-z]{2}\s*$/i;   // "BROOKLYN NY"
const NOT_NAME = /\b(influencer|realtor|referred|referral|lead|yelp|thumbtack|website|instagram|tiktok|movegistics|son'?s|daughter'?s|mom'?s|dad'?s|move|moving|storage|junk|piano|pending|callback|client|by|men|man|truck|deposit|db|dp|the|and|aka|i)\b/gi;

function lev(a, b) {
  if (a === b) return 0;
  const m = a.length, n = b.length;
  if (Math.abs(m - n) > 2) return 3;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    prev = cur;
  }
  return prev[n];
}

// "Lauren Hufford Son's Move | 3 Men | DB10/4" -> ["lauren", "hufford"]
function nameTokens(s) {
  const head = String(s || '').split('|')[0].replace(/\(.*?\)/g, ' ').replace(/[\/&,]/g, ' ');
  return head.toLowerCase().replace(NOT_NAME, ' ').replace(/[^a-z' -]/g, ' ').replace(/'/g, '')
    .split(/[\s-]+/).filter(w => w.length >= 2);
}
const similar = (x, y) => x === y || (x.length >= 4 && y.length >= 4 && lev(x, y) <= 2)
  || (x.length >= 5 && y.length >= 5 && (x.includes(y) || y.includes(x)));

// 'strong' = first and last name agree, 'first' = only a first name to compare and it agrees
function nameMatch(person, cardName) {
  const p = nameTokens(person), c = nameTokens(cardName);
  if (!p.length || !c.length || !similar(p[0], c[0])) return null;
  if (p.length >= 2 && c.length >= 2) return similar(p[p.length - 1], c[c.length - 1]) ? 'strong' : null;
  return 'first';
}

const cardText = t => `${t.name || ''}\n${t.markdown_description || t.description || ''}`.replace(/\\(?=[^A-Za-z0-9\s])/g, '');
// the team's own card wins over one an automation made, then the most recently worked one
const auto = t => /\|\s*Movegistics Lead/i.test(t.name || '') ? 1 : 0;
const newest = list => [...list].sort((a, b) => auto(a) - auto(b) || +(b.date_updated || 0) - +(a.date_updated || 0))[0];
const asCard = (t, how, extra = {}) => ({ id: t.id, name: t.name, url: t.url,
  status: String((t.status && t.status.status) || t.status || ''), how, ...extra });

function resolveIdentity({ number, names = [], cards, boardOk = true, nowMs = Date.now() }) {
  if (!boardOk) return { state: 'unknown' };
  const n = tenDigits(number);
  if (n.length === 10) {
    const byPhone = (cards || []).filter(t => cardText(t).replace(/\D/g, '').includes(n));
    if (byPhone.length) return { state: 'matched', card: asCard(newest(byPhone), 'phone') };
  }
  const people = [...new Set(names.filter(x => x && !GENERIC_CALLER.test(x)).map(x => String(x).trim()))];
  for (const person of people) {
    const strong = (cards || []).filter(t => nameMatch(person, t.name) === 'strong');
    if (strong.length) return { state: 'matched', card: asCard(newest(strong), 'name', { nameUsed: person }) };
  }
  for (const person of people) {
    // a first name alone only counts on a card someone worked in the last 2 weeks, and only if unique
    const recent = (cards || []).filter(t => nowMs - +(t.date_updated || t.date_created || 0) < 14 * 86400000
      && nameMatch(person, t.name) === 'first');
    if (recent.length === 1) return { state: 'possible', card: asCard(recent[0], 'first name', { nameUsed: person }) };
  }
  return { state: 'none' };
}

// the Slack line about the card
function identityLine(id, number) {
  const c = id.card;
  if (id.state === 'matched' && c.how === 'phone') return `<${c.url}|Open card> (${c.status})`;
  if (id.state === 'matched') return `<${c.url}|Open card> (${c.status}) · _found by name ("${c.nameUsed}"), this number was not on the card${id.learned ? ' - it is now' : ''}_`;
  if (id.state === 'possible') return `_Possible card: <${c.url}|${String(c.name).split('|')[0].trim()}> (${c.status}) - same first name, not confirmed. If it is them, add ${number} to the card._`;
  if (id.state === 'unknown') return "_Couldn't check ClickUp just now._";
  return '_No ClickUp card found (checked this number on every board, and the name)._';
}

if (typeof module !== 'undefined') module.exports = { resolveIdentity, identityLine, nameMatch, nameTokens, lev };
