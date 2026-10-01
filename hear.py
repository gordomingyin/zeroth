#!/home/hatch/workspace/whisper-venv/bin/python
"""Zeroth ears: capture + transcribe + analyze any audio source. Usage: hear.py <file|url> [--secs N] [--out DIR]"""
import sys, os, json, subprocess, wave
import numpy as np

SNAP = '/home/hatch/.cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots/manual'
SR = 16000

def capture(url, secs, outpath):
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',url,'-t',str(secs),'-ac','1','-ar',str(SR),outpath], check=True)
    return outpath

def load_wav(path):
    w = wave.open(path); n = w.getnframes()
    d = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)/32768.0
    if w.getnchannels() > 1: d = d.reshape(-1, w.getnchannels()).mean(axis=1)
    if w.getframerate() != SR:
        # crude resample
        idx = (np.arange(int(len(d)*SR/w.getframerate()))*w.getframerate()/SR).astype(int)
        d = d[np.clip(idx,0,len(d)-1)]
    return d

def analyze(d):
    frame = int(SR*0.5); frames = [d[i:i+frame] for i in range(0, len(d)-frame, frame)]
    rms = np.array([np.sqrt(np.mean(f*f)+1e-12) for f in frames])
    speech = rms > 0.02
    # spectral centroid brightness on speech frames
    cents = []
    for f in frames[::4]:
        if np.sqrt(np.mean(f*f)) > 0.02:
            sp = np.abs(np.fft.rfft(f*np.hanning(len(f))))
            freqs = np.fft.rfftfreq(len(f), 1/SR)
            cents.append(float(np.sum(freqs*sp)/(np.sum(sp)+1e-12)))
    return {
        'mean_db': round(float(20*np.log10(np.sqrt(np.mean(d*d))+1e-12)),1),
        'peak_db': round(float(20*np.log10(np.max(np.abs(d))+1e-12)),1),
        'speech_ratio': round(float(speech.mean()),2),
        'brightness_hz': round(float(np.mean(cents)) if cents else 0,1),
    }

def main():
    src = sys.argv[1]
    secs = int(sys.argv[sys.argv.index('--secs')+1]) if '--secs' in sys.argv else 120
    outdir = sys.argv[sys.argv.index('--out')+1] if '--out' in sys.argv else '.'
    os.makedirs(outdir, exist_ok=True)
    if src.startswith('http'):
        wav = os.path.join(outdir, 'capture.wav'); capture(src, secs, wav)
    else:
        wav = src
    d = load_wav(wav)
    from faster_whisper import WhisperModel
    model = WhisperModel(SNAP, device='cpu', compute_type='int8')
    segments, _ = model.transcribe(d, language='en')
    segs = [{'start': round(s.start,1), 'end': round(s.end,1), 'text': s.text.strip()} for s in segments]
    result = {
        'source': src,
        'duration_s': round(len(d)/SR,1),
        'transcript': ' '.join(s['text'] for s in segs),
        'segments': segs,
        'audio': analyze(d),
    }
    base = os.path.join(outdir, 'heard')
    json.dump(result, open(base+'.json','w'), indent=1)
    open(base+'.txt','w').write(result['transcript'])
    print(json.dumps({'transcript': result['transcript'][:600], 'audio': result['audio']}, indent=1))

if __name__ == '__main__': main()
