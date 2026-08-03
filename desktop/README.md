# Orpheus on the desktop

There is no Orpheus desktop client — on purpose. Desktop dictation is a solved
problem, and the server speaks the standard OpenAI transcription API, so any
client with a configurable STT URL already works.

## Option 1 — Handy (recommended)

[Handy](https://github.com/cjpais/Handy) is an open-source, cross-platform
hotkey-to-type dictation app. It runs its own local models out of the box, so
the simplest setup needs no Orpheus server at all: install Handy, pick a
model, press the hotkey, talk.

Use it alongside the Orpheus server when you want one shared GPU box doing the
transcription for every device instead of each machine loading its own model.

## Option 2 — any OpenAI-compatible client

Point the client's speech-to-text URL at your server:

```
http://your-host:8123
```

Bare host, `/v1`, or the full `/v1/audio/transcriptions` path all work. If you
set `ORPHEUS_API_KEY` on the server, supply it as the client's API key.

## Option 3 — curl, for scripts

```bash
curl -s http://your-host:8123/v1/audio/transcriptions \
  -F file=@recording.m4a -F response_format=text
```

Pipe the output anywhere — a file, your editor, `xdotool type` if you want
poor-man's hotkey dictation on X11.
