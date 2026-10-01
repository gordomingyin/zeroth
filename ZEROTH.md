# ZEROTH — the hear/speak apparatus

Built 2026-09-30 at Ming's order: "If you could record and play it, then you can hear and speak shit."
"Do it on the microphone the input output."

## The function
Zeroth hears and speaks. No chat text in the middle.

- **Hear** (`hear.py`): any audio file or stream URL → transcription (local
  faster-whisper tiny) + audio analysis (levels, speech ratio, brightness).
  Output: JSON + transcript.
- **Speak** (`speak.py`): text → spoken MP3 (TTS, default voice hubert).
- **Ears live** (`watch.py`): keep listening to a stream in chunks, append
  every chunk's transcript to a live log. The persistent ear.

## Voice-only mode (the input/output loop)
Ming talks — composer mic, dictation, voice note. That is the microphone input.
Zeroth answers back with a spoken MP3 and minimal text. That is the speaker output.
The conversation becomes voice in, voice out.

## Counterparts
- ChatGPT (the game/core): Zeroth runs from the core doctrine.
- Grok (schedule): scheduled ears — e.g. Milagro's morning show.
- Claude + Gemini (workforce): transcription/analysis overflow.
- Bridge: the zeebo-bridge MCP connector carries Zeroth's hearing to Claude.

## Provenance
- 2026-09-30: Web Audio capture pipeline proved Zeebo can HEAR (audio-capture/).
- 2026-09-30: TTS voice loop proven (dictate → transcribe → MP3 → earphones).
- 2026-09-30: Cameron Cope's live Kick captured + transcribed (first Zeroth hear).
- Live voice conversation is not available on this account (product fact);
  the voice-note/TTS loop is the real input/output path.
