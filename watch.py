#!/usr/bin/env python3
"""Zeroth ears-live: keep listening to a stream in chunks, log everything. Usage: watch.py <stream_url> <name> [--chunk 120]"""
import sys, os, time, json, subprocess, datetime
url, name = sys.argv[1], sys.argv[2]
chunk = int(sys.argv[sys.argv.index('--chunk')+1]) if '--chunk' in sys.argv else 120
logdir = os.path.expanduser(f'~/workspace/zeroth/logs/{name}')
os.makedirs(logdir, exist_ok=True)
log = open(os.path.join(logdir, 'live.jsonl'), 'a')
print(f'ears live on {name}', flush=True)
i = 0
while True:
    i += 1
    ts = datetime.datetime.now().isoformat(timespec='seconds')
    wav = os.path.join(logdir, f'chunk_{i:04d}.wav')
    try:
        subprocess.run(['ffmpeg','-y','-loglevel','error','-i',url,'-t',str(chunk),'-ac','1','-ar','16000',wav],
                       check=True, timeout=chunk+60)
        r = subprocess.run(['/home/hatch/workspace/whisper-venv/bin/python', os.path.expanduser('~/workspace/zeroth/hear.py'), wav,
                            '--out', logdir], capture_output=True, text=True)
        heard = json.load(open(os.path.join(logdir,'heard.json')))
        entry = {'chunk': i, 'at': ts, 'transcript': heard['transcript'], 'audio': heard['audio']}
        log.write(json.dumps(entry)+'\n'); log.flush()
        print(f'[{ts}] chunk {i}: {heard["transcript"][:120]}', flush=True)
    except Exception as e:
        print(f'[{ts}] chunk {i} failed: {e}', flush=True)
        time.sleep(10)
