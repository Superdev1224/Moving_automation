#!/usr/bin/env python3
"""Transcribes new RingCentral call recordings and posts them to n8n (items #1 and #3).

Runs on this server from cron, every minute since 2026-10-05 (was every 5): the summary and the AI's
card move then land 1-2 minutes after a call instead of 3-6. A lock keeps a long transcription from
overlapping the next run, and the RingCentral login is reused for its hour instead of every minute. It only makes outbound requests: RingCentral -> local
whisper.cpp -> n8n webhook. Audio never leaves the machine and nothing new listens.

State (last call seen, ids already done) lives in worker/state.json.
"""
import fcntl, json, os, pathlib, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / 'worker/state.json'
LOCK = ROOT / 'worker/.transcribe.lock'
LOG = ROOT / 'worker/transcribe.log'
WHISPER = '/root/whisper.cpp/build/bin/whisper-cli'
MODEL = '/root/whisper.cpp/models/ggml-base.en.bin'

MIN_SECONDS = 60                                            # skip very short calls (voicemail drops, misdials)
LOOKBACK_MIN = int(os.environ.get('LOOKBACK_MIN', 90))      # how far back to look for finished calls
MAX_PER_RUN = int(os.environ.get('MAX_PER_RUN', 6))         # keep each run short; the next run picks up the rest


def secrets():
    out = {}
    for line in (ROOT / '.secrets').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    if LOG.exists() and LOG.stat().st_size > 5_000_000:      # keep the log small
        LOG.write_text(LOG.read_text()[-1_000_000:])
    with LOG.open('a') as f:
        f.write(line + '\n')


def http(url, data=None, headers=None, method=None, binary=False, timeout=180):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw if binary else json.loads(raw or b'{}')


TABLE = ROOT / 'workflows/call-transcripts.table'     # n8n data table "call_transcripts" (2026-11-16)


def store(s, payload):
    """Keep the transcript in n8n's call_transcripts table, so the invoice review and the cash audit can
    search the crew's calls later. Never fatal: lead scoring does not depend on it."""
    try:
        row = {k: ('' if payload.get(k) is None else str(payload.get(k))) for k in
               ('callId', 'direction', 'fromNumber', 'toNumber', 'fromName', 'toName', 'extension', 'transcript')}
        row.update(startTime=payload.get('startTime'), duration=payload.get('duration') or 0)
        http(f"https://n8n.expresshaulers.com/api/v1/data-tables/{TABLE.read_text().strip()}/rows",
             json.dumps({'data': [row], 'returnType': 'count'}).encode(),
             {'Content-Type': 'application/json', 'X-N8N-API-KEY': s['N8N_API_KEY']}, timeout=60)
        return True
    except Exception as e:
        log(f"could not store {payload.get('callId')}: {type(e).__name__}: {e}")
        return False


def rc_token(s, state):
    # reuse the token while it has 5+ minutes left (RingCentral limits logins to a few a minute)
    if state.get('rcToken') and state.get('rcTokenUntil', 0) > time.time() + 300:
        return state['rcToken']
    body = urllib.parse.urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': s['RC_JWT'],
    }).encode()
    import base64
    basic = base64.b64encode(f"{s['RC_CLIENT_ID']}:{s['RC_CLIENT_SECRET']}".encode()).decode()
    r = http('https://platform.ringcentral.com/restapi/oauth/token', body,
             {'Authorization': f'Basic {basic}', 'Content-Type': 'application/x-www-form-urlencoded'})
    state['rcToken'], state['rcTokenUntil'] = r['access_token'], time.time() + int(r.get('expires_in', 3600))
    return r['access_token']


def transcribe(mp3: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        src, wav, out = f'{d}/a.mp3', f'{d}/a.wav', f'{d}/a'
        pathlib.Path(src).write_bytes(mp3)
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', src, '-ar', '16000', '-ac', '1', wav],
                       check=True, timeout=600)
        subprocess.run([WHISPER, '-m', MODEL, '-f', wav, '-t', '4', '-np', '-nt', '-otxt', '-of', out],
                       check=True, timeout=3600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return pathlib.Path(out + '.txt').read_text().strip()


def main():
    lock = LOCK.open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:                                   # the previous run is still transcribing
        return
    s = secrets()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    done = set(state.get('done', []))
    webhook, token = s.get('TRANSCRIPT_WEBHOOK', ''), s.get('TRANSCRIPT_TOKEN', '')
    if not webhook:
        log('no TRANSCRIPT_WEBHOOK in .secrets, nothing to do')
        return

    since = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MIN)).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = ('https://platform.ringcentral.com/restapi/v1.0/account/~/call-log'
           f'?dateFrom={since}&withRecording=true&perPage=250&view=Detailed')
    # RingCentral said "too many requests" recently: stay quiet for a bit instead of retrying every minute
    # (2026-11-16: retrying each minute kept the account throttled for 30 minutes)
    if state.get('pauseUntil', 0) > time.time():
        return
    try:
        auth = {'Authorization': f'Bearer {rc_token(s, state)}'}
        try:
            calls = http(url, headers=auth).get('records', [])
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            state.pop('rcToken', None)                        # the saved login was refused: log in again
            auth = {'Authorization': f'Bearer {rc_token(s, state)}'}
            calls = http(url, headers=auth).get('records', [])
    except urllib.error.HTTPError as e:
        if e.code not in (401, 429):
            raise
        state['pauseUntil'] = time.time() + 120
        state.pop('rcToken', None)
        STATE.write_text(json.dumps({**state, 'done': list(done)}))
        log(f'RingCentral refused ({e.code}); pausing 2 minutes')
        return

    todo = [c for c in calls
            if c.get('recording', {}).get('id') and c.get('id') not in done and c.get('duration', 0) >= MIN_SECONDS]
    todo.sort(key=lambda c: c.get('startTime', ''))
    if todo or datetime.now().minute % 15 == 0:              # every minute would bury the log in "0 new"
        log(f'{len(calls)} recorded calls in window, {len(todo)} new')

    for c in todo[:MAX_PER_RUN]:
        rec_id = c['recording']['id']
        try:
            t0 = time.time()
            mp3 = http(f'https://platform.ringcentral.com/restapi/v1.0/account/~/recording/{rec_id}/content',
                       headers=auth, binary=True)
            text = transcribe(mp3)
            payload = {
                'callId': c['id'], 'recordingId': rec_id,
                'startTime': c.get('startTime'), 'duration': c.get('duration'),
                'direction': c.get('direction'),
                'fromNumber': (c.get('from') or {}).get('phoneNumber', ''),
                'toNumber': (c.get('to') or {}).get('phoneNumber', ''),
                # caller-ID names help match a caller to a card that does not have their number yet
                'fromName': (c.get('from') or {}).get('name', ''),
                'toName': (c.get('to') or {}).get('name', ''),
                'extension': ((c.get('to') or {}).get('extensionId') or (c.get('from') or {}).get('extensionId')),
                'transcript': text,
            }
            http(webhook, json.dumps(payload).encode(),
                 {'Content-Type': 'application/json', 'X-Worker-Token': token}, timeout=120)
            done.add(c['id'])
            store(s, payload)
            log(f"sent {c['id']} ({c.get('duration')}s audio, {len(text.split())} words, {time.time()-t0:.0f}s)")
        except Exception as e:                                    # keep going; retry next run
            log(f"FAILED {c.get('id')}: {type(e).__name__}: {e}")

    # forget ids older than the lookback window so the file stays small
    state['done'] = list(done)[-2000:]
    state['lastRun'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    STATE.write_text(json.dumps(state))


if __name__ == '__main__':
    main()
