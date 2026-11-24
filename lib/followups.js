// Follow-up reminders for ClickUp "SCHEDULED FOLLOW UPS" cards (item #2).
// Needs parse-card.js loaded first (findDate, findTime, tzOffsetMin, toEpoch, nyToday).
// When: ClickUp due date if set, else the date/time written in the card name
// ("Callback 9/19 @10AM", "Call 9/18 @4:30pm", "follow up 10/10").

const REMIND_BEFORE_MIN = 10;

function followUpWhen(task, nowMs) {
  let date = null, time = null;
  if (task.due_date) {
    const due = +task.due_date;
    const local = new Date(due + tzOffsetMin(due) * 60000);
    date = { y: local.getUTCFullYear(), m: local.getUTCMonth() + 1, d: local.getUTCDate() };
    const hh = local.getUTCHours(), mm = local.getUTCMinutes();
    if (task.due_date_time !== false && !(mm === 0 && (hh === 0 || hh === 4))) time = { hh, mm };
  } else {
    const name = task.name || '';
    date = findDate(name, nowMs);
    if (date) time = findTime(name.slice(name.lastIndexOf(date.raw) + date.raw.length)) || findTime(name);
  }
  if (!date) return null;
  return {
    date, time,
    at: time ? toEpoch(date.y, date.m, date.d, time.hh, time.mm) * 1000 : null,
    dayKey: date.y * 10000 + date.m * 100 + date.d,
  };
}

function fmtTime(ms) {
  return new Date(ms).toLocaleString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });
}
const who = t => (t.assignees || []).map(a => a.username).filter(Boolean).join(', ');
const link = t => `<${t.url || 'https://app.clickup.com/t/' + t.id}|Open in ClickUp>`;
const clean = s => (s || '').replace(/\s+/g, ' ').trim();

// Called every few minutes. `sent` is persistent storage so each follow-up is reminded once.
function dueReminders(tasks, nowMs, sent) {
  const out = [];
  for (const t of tasks) {
    const w = followUpWhen(t, nowMs);
    if (!w || !w.at) continue;
    const key = `${t.id}@${w.at}`;
    if (sent[key]) continue;
    // window: from 10 min before until 5 min after (covers a missed run)
    if (nowMs >= w.at - REMIND_BEFORE_MIN * 60000 && nowMs < w.at + 5 * 60000) {
      sent[key] = nowMs;
      const mins = Math.max(0, Math.round((w.at - nowMs) / 60000));
      const a = who(t);
      out.push(`:alarm_clock: *Follow-up ${mins ? `in ${mins} min` : 'now'}* (${fmtTime(w.at)}): *${clean(t.name)}*` +
        (a ? `\nAssigned: ${a}` : '') + `\n${link(t)}`);
    }
  }
  for (const k of Object.keys(sent)) if (nowMs - sent[k] > 3 * 86400000) delete sent[k];   // tidy
  return out;
}

// 8 AM list: today's follow-ups (by time), overdue ones, and cards without a readable date.
function morningList(tasks, nowMs) {
  const t0 = nyToday(nowMs), today = t0.y * 10000 + t0.m * 100 + t0.d;
  const todays = [], overdue = [], noDate = [];
  for (const t of tasks) {
    const w = followUpWhen(t, nowMs);
    if (!w) noDate.push(t);
    else if (w.dayKey === today) todays.push([w, t]);
    else if (w.dayKey < today) overdue.push([w, t]);
  }
  todays.sort((a, b) => (a[0].at ?? Infinity) - (b[0].at ?? Infinity));
  overdue.sort((a, b) => b[0].dayKey - a[0].dayKey);
  const day = new Date(nowMs).toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric' });
  const line = (w, t) => `• ${w.at ? fmtTime(w.at) : '_no time_'}: ${clean(t.name)}${who(t) ? ` (${who(t)})` : ''}  ${link(t)}`;
  let s = `:sunrise: *Today's follow-ups: ${day}* (${todays.length})\n`;
  s += todays.length ? todays.map(([w, t]) => line(w, t)).join('\n') : '_None scheduled for today._';
  if (overdue.length) {
    s += `\n\n:warning: *Overdue, still in Scheduled Follow Ups* (${overdue.length})\n`;
    s += overdue.slice(0, 25).map(([w, t]) => `• ${w.date.m}/${w.date.d}: ${clean(t.name)}  ${link(t)}`).join('\n');
    if (overdue.length > 25) s += `\n…and ${overdue.length - 25} more`;
  }
  if (noDate.length) s += `\n\n_${noDate.length} follow-up card(s) have no date in the name or due date, so they can't be reminded._`;
  return s;
}

if (typeof module !== 'undefined') module.exports = { followUpWhen, dueReminders, morningList };
