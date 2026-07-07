# Steno

Your voice, your machine, no word limits.

Steno is a self-hosted dictation stack — an open alternative to cloud
dictation apps like Wispr Flow. Speak on your phone or desktop; a GPU box on
your own network transcribes it (NVIDIA Parakeet TDT), polishes it with a
local LLM (filler removal, punctuation, self-correction handling), and types
it back. Nothing leaves your network.

## Pieces

| Piece | Status | What it is |
|---|---|---|
| `server/` | working | OpenAI-compatible `/v1/audio/transcriptions` endpoint: Parakeet TDT ASR + optional LLM cleanup pass |
| `desktop/` | notes | use [Handy](https://github.com/cjpais/Handy) (open-source, hotkey-to-type) locally, or point any OpenAI-compatible dictation client at the server |
| `android/` | planned | thin voice-keyboard IME that records, POSTs to the server (e.g. over Tailscale), and commits cleaned text into any app |

## Server quickstart

```bash
cd server
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/cu130 \
  --extra-index-url https://pypi.org/simple "torch==2.11.*"
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python steno_server.py
```

First start downloads the Parakeet model (~2.4 GB). Then:

```bash
curl -s http://localhost:8123/v1/audio/transcriptions \
  -F file=@recording.m4a -F response_format=verbose_json
```

Any audio format ffmpeg can read is accepted. `clean=false` skips the LLM
polish and returns raw ASR output.

The cleanup pass talks to any OpenAI-compatible chat endpoint
(`STENO_CLEAN_URL`, default `http://127.0.0.1:8000/v1`) and auto-discovers
the loaded model. If the endpoint is down, Steno degrades gracefully to raw
transcription — dictation never blocks on the polish.

A sample systemd user unit is in `server/steno.service`.

## License

MIT
