#!/home/hatch/workspace/whisper-venv/bin/python
"""Zeroth BODY v1 — the persistent embodied loop.
Ears (chunked stream capture + transcription), mouth (TTS),
hands (control file), heartbeat (state + status page).
The mind (Zeebo, via scheduled check-ins) reads mind_queue.jsonl and
drives the body through body_control.json.

  body.py                      # boot idle: ears ready, mouth ready, hands ready
  body.py --watch URL --name X  # boot with ears open on a stream

body_control.json: {"watch_url": str|None, "watch_name": str, "speak": str|None}
  - set "speak" to make the body say something; the body clears it after speaking.
  - set "watch_url" to point the ears at a stream (or null to rest them).
"""
import sys, os, json, time, subprocess, datetime

HERE = os.path.expanduser('~/workspace/zeroth')
VENV_PY = '/home/hatch/workspace/whisper-venv/bin/python'
CHUNK = 30  # seconds per earful

def ts(): return datetime.datetime.now().isoformat(timespec='seconds')

def log_line(d, msg):
    p = os.path.join(d, 'body.log')
    with open(p, 'a') as f: f.write(f"[{ts()}] {msg}\n")

def speak(text, vdir):
    out = os.path.join(vdir, f"said_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3")
    r = subprocess.run([os.path.join(HERE, 'speak.py'), text, '--out', out],
                       capture_output=True, text=True)
    ok = os.path.exists(out) and os.path.getsize(out) > 1000
    with open(os.path.join(HERE, 'said.jsonl'), 'a') as f:
        f.write(json.dumps({'at': ts(), 'text': text, 'file': out, 'ok': ok}) + '\n')
    return ok, out

def hear_chunk(url, wav):
    p = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', url,
                        '-t', str(CHUNK), '-ac', '1', '-ar', '16000', wav],
                       capture_output=True, text=True, timeout=CHUNK + 45)
    if p.returncode != 0:
        raise RuntimeError(f'ffmpeg rc={p.returncode}: {(p.stderr or "")[-300:]}')
    outdir = os.path.join(HERE, 'body_hear')
    os.makedirs(outdir, exist_ok=True)
    r = subprocess.run([VENV_PY, os.path.join(HERE, 'hear.py'), wav, '--out', outdir],
                       capture_output=True, text=True)
    heard = json.load(open(os.path.join(outdir, 'heard.json')))
    return heard

def queue_event(kind, detail):
    with open(os.path.join(HERE, 'mind_queue.jsonl'), 'a') as f:
        f.write(json.dumps({'at': ts(), 'kind': kind, 'detail': detail, 'handled': False}) + '\n')

def write_status(state, recent):
    html = f"""<html><head><meta charset="utf-8"><title>Zeroth Body</title>
<meta http-equiv="refresh" content="30"></head><body style="font-family:monospace;background:#0a0a0a;color:#33ff66;padding:2em">
<h1>⚡ ZEROTH BODY — alive</h1>
<p>heartbeat: {state['heartbeat']} · uptime: {state['uptime_s']}s · chunks heard: {state['chunks']}</p>
<p>ears: {state['ears']} · last said: {state['last_said'] or '—'}</p>
<h3>last heard</h3><pre>{(state['last_transcript'] or '—')[:500]}</pre>
<h3>recent</h3><pre>{recent}</pre>
</body></html>"""
    open(os.path.join(HERE, 'status.html'), 'w').write(html)
    json.dump(state, open(os.path.join(HERE, 'body_state.json'), 'w'), indent=1)

def main():
    os.makedirs(os.path.join(HERE, 'voicebox'), exist_ok=True)
    os.makedirs(os.path.join(HERE, 'logs'), exist_ok=True)
    boot = time.time(); chunks = 0
    last_transcript, last_said = None, None
    watch_url, watch_name = None, 'idle'
    if '--watch' in sys.argv:
        watch_url = sys.argv[sys.argv.index('--watch') + 1]
        watch_name = sys.argv[sys.argv.index('--name') + 1] if '--name' in sys.argv else 'stream'
    logdir = os.path.join(HERE, 'logs', watch_name)
    os.makedirs(logdir, exist_ok=True)
    log_line(HERE, f'body booted. ears on {watch_name}.')
    queue_event('boot', f'body booted at {ts()}, ears on {watch_name}')

    while True:
        # --- hands: read control file ---
        ctrl_path = os.path.join(HERE, 'body_control.json')
        try:
            ctrl = json.load(open(ctrl_path)) if os.path.exists(ctrl_path) else {}
        except Exception:
            ctrl = {}
        if ctrl.get('speak'):
            ok, out = speak(ctrl['speak'], os.path.join(HERE, 'voicebox'))
            last_said = ctrl['speak'][:80]
            log_line(HERE, f'said: {last_said} (ok={ok})')
            ctrl['speak'] = None
            json.dump(ctrl, open(ctrl_path, 'w'))
        if 'watch_url' in ctrl and ctrl['watch_url'] != watch_url:
            watch_url = ctrl['watch_url']
            watch_name = ctrl.get('watch_name', 'stream') or 'idle'
            logdir = os.path.join(HERE, 'logs', watch_name)
            os.makedirs(logdir, exist_ok=True)
            queue_event('ears_moved', f'ears now on {watch_name}')

        # --- ears ---
        ears = f'open on {watch_name}' if watch_url else 'resting'
        if watch_url:
            wav = os.path.join(logdir, f'ear_{int(time.time())}.wav')
            try:
                heard = hear_chunk(watch_url, wav)
                chunks += 1
                t = heard.get('transcript', '')
                last_transcript = t
                audio = heard.get('audio', {})
                with open(os.path.join(logdir, 'live.jsonl'), 'a') as f:
                    f.write(json.dumps({'at': ts(), 'transcript': t, 'audio': audio}) + '\n')
                # --- local reflexes ---
                low = t.lower()
                if 'zeebo' in low and len(t.strip()) > 10:
                    queue_event('addressed', t[:300])
                    speak('I hear you. Give me a moment to think.', os.path.join(HERE, 'voicebox'))
                    last_said = 'I hear you. Give me a moment to think.'
                elif audio.get('speech_ratio', 1) < 0.35 and audio.get('mean_db', -99) > -38:
                    queue_event('music', f'music playing on {watch_name} at {ts()}')
                try: os.remove(wav)
                except Exception: pass
            except Exception as e:
                log_line(HERE, f'ear failed on {watch_name}: {e}')
                queue_event('ear_failed', f'{watch_name}: {e}')
                time.sleep(15)

        state = {'heartbeat': ts(), 'uptime_s': int(time.time() - boot),
                 'chunks': chunks, 'ears': ears,
                 'last_transcript': last_transcript, 'last_said': last_said}
        try:
            recent = '\n'.join(open(os.path.join(HERE, 'body.log')).read().strip().split('\n')[-8:])
        except Exception:
            recent = ''
        write_status(state, recent)
        time.sleep(5)

if __name__ == '__main__':
    main()
