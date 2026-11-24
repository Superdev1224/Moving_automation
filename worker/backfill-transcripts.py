#!/usr/bin/env python3
"""One-off: transcribe PAST recorded calls into n8n's call_transcripts table (2026-11-16).

The live worker (transcribe-calls.py) only stores calls from now on. The invoice review and the cash
audit also need the crew's calls from earlier this month, so this fills the table for a date range.
It ONLY stores - it never posts to the lead-scoring webhook, so no card is moved or scored twice.
Own lock and own state file, so the live worker keeps running every minute alongside it.

  nice -n 19 python3 worker/backfill-transcripts.py 2026-11-01 2026-11-16T17:05:00Z
"""
import fcntl, importlib.util, json, pathlib, sys, time, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('worker', ROOT / 'worker/transcribe-calls.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

STATE = ROOT / 'worker/backfill-state.json'
LOCK = ROOT / 'worker/.backfill.lock'


def main(date_from, date_to):
    lock = LOCK.open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    s = w.secrets()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    stored = set(state.get('stored', []))
    rc = {}
    auth = lambda: {'Authorization': f'Bearer {w.rc_token(s, rc)}'}
    calls, page = [], 1
    while True:
        url = ('https://platform.ringcentral.com/restapi/v1.0/account/~/call-log'
               f'?dateFrom={date_from}&dateTo={date_to}&withRecording=true&perPage=250&page={page}&view=Detailed')
        res = w.http(url, headers=auth())
        calls += res.get('records', [])
        if not (res.get('navigation') or {}).get('nextPage'):
            break
        page += 1
        time.sleep(2)
    todo = [c for c in calls if c.get('recording', {}).get('id') and c.get('duration', 0) >= w.MIN_SECONDS
            and c.get('id') not in stored]
    todo.sort(key=lambda c: c.get('startTime', ''))
    w.log(f'BACKFILL {date_from}..{date_to}: {len(calls)} recorded calls, {len(todo)} to store')
    for i, c in enumerate(todo):
        try:
            mp3 = w.http(f"https://platform.ringcentral.com/restapi/v1.0/account/~/recording/{c['recording']['id']}/content",
                         headers=auth(), binary=True)
            text = w.transcribe(mp3)
            payload = {'callId': c['id'], 'startTime': c.get('startTime'), 'duration': c.get('duration'),
                       'direction': c.get('direction'),
                       'fromNumber': (c.get('from') or {}).get('phoneNumber', ''),
                       'toNumber': (c.get('to') or {}).get('phoneNumber', ''),
                       'fromName': (c.get('from') or {}).get('name', ''), 'toName': (c.get('to') or {}).get('name', ''),
                       'extension': ((c.get('to') or {}).get('extensionId') or (c.get('from') or {}).get('extensionId')),
                       'transcript': text}
            if w.store(s, payload):
                stored.add(c['id'])
            state['fails'] = 0
        except urllib.error.HTTPError as e:
            w.log(f"BACKFILL failed {c.get('id')}: HTTP {e.code}")
            fails = state.setdefault('fails', 0) + 1
            state['fails'] = fails
            if e.code == 401:
                rc.clear()                              # token refused: log in again next time
            if fails >= 5:                              # don't hammer RingCentral: stop and let the live worker be
                w.log('BACKFILL stopping after 5 failures in a row')
                break
            time.sleep(60 if e.code in (401, 429) else 10)
            continue
        except Exception as e:
            w.log(f"BACKFILL failed {c.get('id')}: {type(e).__name__}: {e}")
        if i % 10 == 9:
            STATE.write_text(json.dumps({'stored': list(stored)}))
            w.log(f'BACKFILL {i + 1}/{len(todo)} done')
        time.sleep(8)                                   # RingCentral allows ~10 recording downloads a minute, shared with everything else
    STATE.write_text(json.dumps({'stored': list(stored)}))
    w.log(f'BACKFILL finished: {len(stored)} stored')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
