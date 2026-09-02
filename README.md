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
| `desktop/` | new | single-file push-to-talk client for Windows and Linux: hotkey, record, paste into whatever has focus — see `desktop/README.md` |
| `android/` | stable | floating dictation orb + dashboard app — appears whenever a keyboard opens, inserts at the cursor in any app |

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

Spoken symbols are honoured: "open paren", "new line", "exclamation point",
"underscore", "slash", "dash dash", "quote … end quote", "smiley face" and the
rest of the usual dictation vocabulary become the symbols themselves in a
deterministic pre-pass (see `server/formatting.py`). The LLM pass then handles
the context-dependent ones ("jonny at example dot com" → `jonny@example.com`,
"hashtag local peer" → `#localpeer`), turns spoken code and prices into
`get_user(user_id)` and `$25.50`, strips fillers, applies self-corrections and
fixes casing. A content guard compares the model's answer with what was said
and falls back to the pre-passed text if the model dropped anything, so a
cleanup pass can never lose your words. Set `ORPHEUS_DICTIONARY="Jonny, Keryx,
DGX Spark"` to teach it the names it keeps mishearing.

Lists come out as lists. Name the things you need ("from the store I need
eggs, milk, bread and cheese"), count them off ("first… second…", "number
one…", "step one…") or say "bullet point" between items, and Orpheus writes
the lead-in on its own line with a colon and one item per line — bullets for
a plain set, `1.` `2.` when you counted. Two things joined by "and" stay a
sentence, and so does a list buried inside a longer one. Terminal-style
targets (`style=code`) never get list formatting.

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

- **The orb** — an accessibility service watches for the keyboard opening
  in *any* app and floats a draggable glass orb beside it. Tap to record (the
  glow follows your voice), tap again to stop — or press-and-hold to record
  only while held. The transcript lands at the cursor of the focused field,
  spaced to fit the words around it, with a green check and a tick of haptics
  when it does. It snaps to the screen edge after a drag and rests dim when
  idle.
- **Context-aware** — the words next to your cursor and the app you're in
  travel with the audio, so the server starts lowercase mid-sentence, skips
  the trailing period on a one-line chat message, and treats terminal input
  as literal commands. A personal dictionary in Settings rides along too.
- **Nothing is lost** — if the server is reloading or unreachable, the
  recording is kept: the orb turns amber, a tap retries, a hold records
  fresh, and the dashboard offers the same take with Retry / Discard. Server
  restarts (HTTP 503) are retried automatically for up to 40 s.
- **Dashboard** — a live preview of the orb, words dictated today / this
  week / all time with the time saved over typing, plus a local history of
  every transcript with the app it went into and what the recognizer heard
  before cleanup (tap to re-copy, long-press to delete, export via the share
  sheet). Transcripts age out after 30 days by default —
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

**Why an accessibility service?** It's how the orb knows a keyboard opened
and how the transcript lands in the focused text field of *other* apps — the
whole point of system-wide dictation. Orpheus reads only the text around your
cursor in the field you're dictating into (and only when "match the text
around the cursor" is on), sends it to your server with the audio, and stores
none of it;
transcripts and word counts live in a local file on the device, backup is
disabled so they never leave it, and the clipboard entry is flagged sensitive.

## License

MIT

Clients can also say where the text is going. `context_before` / `context_after` (the text around the cursor), `app` (the target package) and `style` (`message`, `email`, `code`, `prose`) let the server start lowercase mid-sentence, drop the trailing period on a one-line chat message, and treat terminal input as literal commands. `ORPHEUS_APP_STYLES` extends the built-in app → style map, and the OpenAI `prompt` field works as a per-request spelling hint.
