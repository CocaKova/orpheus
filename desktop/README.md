# Orpheus on the desktop

`orpheus_desktop.py` is a single-file push-to-talk client for Windows and
Linux. Press a hotkey, talk, press it again — the take goes to your Orpheus
server and the text is pasted into whatever has focus. Same server, same
cleanup pass, same personal dictionary as the phone.

## Install

Python 3.11 or newer.

```bash
cd desktop
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python orpheus_desktop.py --setup      # writes the config template
```

Config lives at `~/.config/orpheus/desktop.toml` (Linux) or
`%APPDATA%\Orpheus\desktop.toml` (Windows). Set `url` to your server:

```toml
url = "http://your-host:8123"
```

Bare host, `/v1`, or the full `/v1/audio/transcriptions` path all work. Set
`api_key` only if the server has `ORPHEUS_API_KEY`. Every key has a CLI flag
override (`--url`, `--hotkey`, `--hold`, `--no-paste`, `--raw`, `--quiet`…).

## Run

```bash
.venv/bin/python orpheus_desktop.py              # daemon
```

| Key            | Default              | Meaning                                              |
|----------------|----------------------|------------------------------------------------------|
| `hotkey`       | `<ctrl>+<alt>+space` | Global hotkey (Windows, X11)                         |
| `hold_to_talk` | `false`              | `true` = record while held, release to stop          |
| `paste`        | `true`               | Ctrl+V into the focused window; `false` = clipboard only |
| `restore_clipboard` | `true`          | Put the previous clipboard text back after pasting   |
| `raw`          | `false`              | Skip the server's AI cleanup pass                    |
| `dictionary`   | `""`                 | Names and terms to spell right, comma-separated      |
| `style`        | `""`                 | Orpheus servers: `message`, `email`, `code`, or prose |
| `sounds`       | `true`               | Beep on start/stop                                   |

The daemon also listens on a local control socket, so any shortcut tool can
drive it:

```bash
python orpheus_desktop.py toggle     # start or stop
python orpheus_desktop.py start
python orpheus_desktop.py stop
python orpheus_desktop.py status
python orpheus_desktop.py quit
```

A take that sounds like silence is dropped without a round trip. If the
server is reloading (HTTP 503) the client retries for up to 40 s. If it still
fails, the WAV is saved under `~/.cache/orpheus/failed/` (Windows:
`%LOCALAPPDATA%\Orpheus\failed\`) and the path is shown, so a long dictation
is never lost — send it later with `python orpheus_desktop.py transcribe take.wav`.

### Windows

Everything works out of the box: pynput handles the hotkey and the paste,
the clipboard goes through the Win32 API, beeps via `winsound`, results show
as toast notifications. Run it with `pythonw` to hide the console once you
trust it. Start it with Windows by dropping a shortcut into
`shell:startup`.

### Linux — X11

Same as Windows for the hotkey and paste. Install `xclip` (or `xsel`) for the
clipboard and `notify-send` for notifications. If `pip install sounddevice`
fails or PortAudio is missing, the client records through `arecord`
(`alsa-utils`) instead.

### Linux — Wayland

Compositors don't hand out global hotkeys, so the daemon runs without one
and you bind a system shortcut to the toggle command:

- GNOME: Settings → Keyboard → Custom Shortcuts → add
  `/path/to/.venv/bin/python /path/to/orpheus_desktop.py toggle`
- KDE: System Settings → Shortcuts → Custom Shortcuts, same command.

Pasting needs `wtype` or `ydotool` (the latter needs its daemon and the
`input` group). Clipboard uses `wl-copy` / `wl-paste`. Without a paste tool the
text is still put on the clipboard and the notification says so.

## Testing without a microphone

```bash
python orpheus_desktop.py transcribe clip.wav                 # one file, prints text
python orpheus_desktop.py daemon --fake-audio clip.wav        # daemon that "records" the file
python orpheus_desktop.py toggle; python orpheus_desktop.py toggle
```

## Alternatives

### Handy

[Handy](https://github.com/cjpais/Handy) is an open-source, cross-platform
hotkey-to-type dictation app that ships its own local models. Point it at
the Orpheus server when you want one shared GPU box transcribing for every
machine instead of each one loading its own model.

### Any OpenAI-compatible client

Point the client's speech-to-text URL at `http://your-host:8123`. Bare host,
`/v1`, or the full path all work; supply `ORPHEUS_API_KEY` as the API key if
the server has one set.

### curl, for scripts

```bash
curl -s http://your-host:8123/v1/audio/transcriptions \
  -F file=@recording.m4a -F response_format=text
```
