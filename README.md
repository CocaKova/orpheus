# Orpheus

Your voice, your machine, no word limits.

Orpheus is a self-hosted dictation stack — an open alternative to cloud
dictation apps like Wispr Flow. Speak on your phone or desktop; a GPU box on
your own network transcribes it (NVIDIA Parakeet TDT), polishes it with a
local LLM (filler removal, punctuation, self-correction handling), and types
it back. Nothing leaves your network.

## Pieces

| Piece | Status | What it is |
|---|---|---|
| `server/` | stable | OpenAI-compatible `/v1/audio/transcriptions` endpoint: Parakeet TDT ASR + optional LLM cleanup pass |
| `desktop/` | docs | use [Handy](https://github.com/cjpais/Handy) (open-source, hotkey-to-type) locally, or point any OpenAI-compatible dictation client at the server — see `desktop/README.md` |
| `android/` | stable | floating dictation bubble + dashboard app — appears whenever a keyboard opens, pastes into any app |

Because the endpoint speaks the OpenAI transcription API, any client with a
configurable STT URL works out of the box — point it at
`http://your-host:8123` and dictate.

## Server quickstart

```bash
cd server
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/cu130 \
  --extra-index-url https://pypi.org/simple "torch==2.11.*"
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python orpheus_server.py
```

First start downloads the Parakeet model (~2.4 GB). Then:

```bash
curl -s http://localhost:8123/v1/audio/transcriptions \
  -F file=@recording.m4a -F response_format=verbose_json
```

Any audio format ffmpeg can read is accepted. `clean=false` skips the LLM
polish and returns raw ASR output.

The cleanup pass talks to any OpenAI-compatible chat endpoint
(`ORPHEUS_CLEAN_URL`, default `http://127.0.0.1:8000/v1`) and auto-discovers
the loaded model. If the endpoint is down, Orpheus degrades gracefully to raw
transcription — dictation never blocks on the polish.

A sample systemd user unit is in `server/orpheus.service`.

Optional hardening (all off by default, see the docstring in
`orpheus_server.py` for the full list): set `ORPHEUS_API_KEY` to require a
bearer token on `/v1/*`. Transcript text stays out of the server logs unless
you set `ORPHEUS_LOG_TEXT=true`.

## Android app

A standalone dictation app (`android/`) in the spirit of Wispr Flow:

- **Floating bubble** — an accessibility service watches for the keyboard
  opening in *any* app and floats a draggable Orpheus bubble beside it. Tap to
  record (live waveform), tap again to stop — or press-and-hold to record only
  while held. The transcript is copied to the clipboard and pasted straight
  into the focused text field.
- **Dashboard** — words dictated today / this week / all time, plus a local
  history of every transcript (tap to re-copy, long-press to delete, export
  via the share sheet). Transcripts age out after 30 days by default —
  configurable from 7 days to forever, or off entirely — while the word-count
  stats are kept for good. The log never leaves the device.
- **Universal STT** — same OpenAI-compatible client as everything else here:
  point it at your Orpheus server, or OpenAI, or Groq, with optional API key
  and model fields. A test-connection button tells you the setup works before
  you ever dictate, and a "skip AI cleanup" switch pastes the raw
  transcription instead of the polished one.

Install the APK from the [latest release](https://github.com/CocaKova/orpheus/releases),
or build it yourself: `cd android && ./gradlew assembleRelease` (APK lands in
`app/build/outputs/apk/release/`). No Google services, no analytics, no
permissions beyond mic + the accessibility service you explicitly enable.

**Why an accessibility service?** It's how the bubble knows a keyboard opened
and how the transcript lands in the focused text field of *other* apps — the
whole point of system-wide dictation. Orpheus reads nothing from your screen;
transcripts and word counts live in a local file on the device, backup is
disabled so they never leave it, and the clipboard entry is flagged sensitive.

## License

MIT
