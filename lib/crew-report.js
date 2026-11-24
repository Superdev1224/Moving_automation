// Crew job reports (client, 2026-10-02): the crew lead texts the office from each job - start time,
// materials used, breaks, extra crew, finish time - and the invoice audit checks the invoice against
// it. The texts are free-form ("For Simon move / We started at 8:10am / Material used are 30 rolls of
// tape / 1 and half shrink wrap"), so Claude reads a day's texts and returns one report per job.

const MATERIALS = ['Tape', 'Shrink Wrap', 'Small Box', 'Medium Box', 'Large Box', 'Extra Large Box', 'Wardrobe Box',
  'Dish Pack', 'Packing Paper', 'Bubble Wrap', 'Mattress Bag', 'TV Box', 'Blanket'];

const crewSystem = () => [
  'You read text messages a moving crew lead sent the office on one work day. The crew usually does',
  'several jobs a day. Find the report for each job: when they arrived or started, when they finished,',
  'breaks, extra crew members who joined, and packing materials used.',
  'Assign each report to one of the jobs listed (they are in time order). Use a customer name when the',
  'text gives one ("For Simon move"); otherwise use the time of day and the order of the jobs.',
  'Times: 24-hour "HH:MM", New York time. If a finish time is only estimated ("we should be done by 6",',
  '"within his 2 hours"), give it but set endExact false. "We finished" sent at a time = that time, exact.',
  'Breaks in minutes, summed. Materials: use ONLY these names: ' + MATERIALS.join(', ') + '.',
  '"half of shrink wrap" = 0.5, "1 and half" = 1.5. A later "more material used" adds to the earlier list.',
  'Some lines are the office relaying a crew report in Slack (from "Slack: <name>"), e.g. "can you bill',
  'edin\'s cx, a call came in / 9:30 AM to 4:30 PM / 12 tapes / 1 large box": treat them as reports from the crew.',
  'A report that names a customer belongs ONLY to the job with that customer\'s name. If it names a customer who is',
  'not in the job list (e.g. "Jennifer Digirolamo / 7:45 - 11:45 AM" when Jennifer is not listed), it is ANOTHER crew\'s',
  'job: ignore it completely - never give its times or materials to a different customer. A job with no report of its',
  'own is simply left out of the answer.',
  'Ignore texts that are not job reports (payroll hours, chat, questions, directions).',
  'Answer with JSON only: {"reports":[{"job":"<job title exactly as listed>","start":"HH:MM"|null,',
  '"end":"HH:MM"|null,"endExact":true|false,"breakMinutes":0,"extraCrew":"<e.g. 4th person from 12:00>"|null,',
  '"materials":[{"item":"<name>","qty":<number>}],"noMaterials":true|false,"quotes":["<the key lines, short>"]}]}',
].join(' ');

function crewBody(day, jobs, texts, model = 'claude-opus-5') {
  const j = jobs.map(x => `- ${x.title} (scheduled ${x.time})`).join('\n');
  const t = texts.map(x => `[${x.time}] ${x.from}: ${x.text}`).join('\n');
  return { model, max_tokens: 4000, system: crewSystem(),
    messages: [{ role: 'user', content: `Day: ${day}\n\nJobs that day:\n${j}\n\nTexts from the crew:\n${t}` }] };
}

function parseCrew(res) {
  const txt = (res?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
  const m = txt.match(/\{[\s\S]*\}/);
  try { return (JSON.parse(m ? m[0] : '{}').reports || []).filter(r => r && r.job); } catch { return []; }
}

if (typeof module !== 'undefined') module.exports = { MATERIALS, crewSystem, crewBody, parseCrew };
