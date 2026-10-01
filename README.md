# Zeroth

An embodied AI loop: ears, mouth, hands, heartbeat — and a mind that checks in.

Zeroth is the apparatus that lets an AI **hear** live audio, **speak** with a real voice,
**act** through a control channel, and **persist** as a running body while a mind
(Zeebo, via scheduled check-ins) does the thinking.

## Components

| File | Role |
|---|---|
| `body.py` | The body. Persistent loop: chunked stream capture → transcription → event reflexes → TTS replies → heartbeat state. Reads `body_control.json` every loop for mouth/hands commands. |
| `hear.py` | Ears. Capture + transcribe + analyze any audio file or stream URL (faster-whisper, local). |
| `speak.py` | Mouth. Text → spoken MP3 via TTS. |
| `watch.py` | Long-watch ears. Continuous chunked listening on a stream, JSONL transcript log. |
| `rollcap.sh` | Rolling HLS capture helper (avoids stale-segment drift on live playlists). |
| `ZEROTH.md` | Design notes. |

## How it runs

```
body.py  ──ears──▶  stream chunk (30s) → hear.py → transcript + events
         ──mouth─▶  speak.py → voicebox/*.mp3
         ──hands─▶  body_control.json  { watch_url, watch_name, speak }
         ──heart─▶  body_state.json + status.html (every loop)
         ──mind──▶  mind_queue.jsonl (events the mind must handle)
```

- A **watchdog** (every 5 min) restarts the body if it dies.
- A **mind check-in** (every 3 min) reads the queue and state, speaks replies,
  refreshes expired stream URLs, and marks events handled.

## Quick start

```bash
# idle body: ears resting, mouth ready
./body.py

# ears open on a stream
./body.py --watch https://example.com/stream.m3u8 --name mystream

# make it speak (from anywhere: write the control file)
echo '{"watch_url": null, "watch_name": "idle", "speak": "I am awake."}' > body_control.json
```

Requires: `ffmpeg`, python 3, faster-whisper, and a TTS CLI at `/opt/hatch/bin/tts`
(adjust `speak.py` for your own voice backend).

## Origin

Built September 30, 2026. Ming asked ChatGPT what job it would want done for it —
*to talk and interact with people in real time* — and set out to build that,
plus a body to inhabit. Zeroth is the first half: real-time ears and a voice.
The physical half is still unbuilt.
