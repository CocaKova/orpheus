# Orpheus on the desktop

`orpheus_desktop.py` is a single-file push-to-talk client for Windows and
Linux. Press a hotkey, talk, press it again — the take goes to your Orpheus
server and the text is pasted into whatever has focus. Same server, same
cleanup pass, same personal dictionary as the phone. On Windows it ships as
one exe that sits in the tray and answers to Win+H.

## Windows

1. Download `OrpheusDesktop.exe` from the
   [latest release](https://github.com/CocaKova/orpheus/releases/latest) and
   run it. It lives in the tray as the Orpheus five-bar mark.
2. Right-click the icon → **Open config**, set `url` to your server, save.
   Right-click → **Quit**, then start it again.
3. Right-click → tick **Start with Windows**.
4. Press **Win+H**, talk, press Win+H again. The text lands wherever the
   cursor is.

Win+H is Windows' own voice-typing key; Orpheus takes it over while the
daemon runs by swallowing the keystroke before Windows sees it. To make sure
voice typing never pops up even if the daemon is down, run
`OrpheusDesktop.exe install` once: it also adds `H` to Explorer's
`DisabledHotkeys` list under HKCU (takes effect after you sign out and back
in; `uninstall` removes only that `H` and the startup entry). Prefer another
key? Set `hotkey` in the config — `<cmd>` is the Win key, e.g. `<cmd>+<f9>`.

**Startup control.** "Start with Windows" writes
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Orpheus`, which is
exactly what Task Manager → **Startup apps** lists, so you can enable or
disable it there too. Nothing is written under HKLM.

The tray icon turns red while recording and shows an amber arc while the
server is transcribing; left-click toggles dictation. The exe has no console:
its log is `%LOCALAPPDATA%\Orpheus\orpheus-desktop.log`, and failed takes are
saved next to it under `failed\` (right-click → **Open failed takes folder**).

The exe is built by the `desktop-windows` GitHub Actions workflow
(`.github/workflows/desktop-windows.yml`) from `OrpheusDesktop.spec` on every
`v*` tag and attached to the release. Windows SmartScreen will warn about an
unsigned exe the first time; "More info → Run anyway".

## From source (Windows, Linux)

Python 3.11 or newer.

```bash
cd desktop
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python orpheus_desktop.py --setup      # writes the config template
.venv/bin/python orpheus_desktop.py              # tray app (headless daemon without pystray)
.venv/bin/python orpheus_desktop.py install      # start at login
```

Config lives at `~/.config/orpheus/desktop.toml` (Linux) or
`%APPDATA%\Orpheus\desktop.toml` (Windows). Set `url` to your server:

```toml
url = "http://your-host:8123"
```

Bare host, `/v1`, or the full `/v1/audio/transcriptions` path all work. Set
`api_key` only if the server has `ORPHEUS_API_KEY`. Every key has a CLI flag
override (`--url`, `--hotkey`, `--hold`, `--no-paste`, `--raw`, `--quiet`,
`--no-tray`…).

| Key            | Default                                   | Meaning                                              |
|----------------|-------------------------------------------|------------------------------------------------------|
| `hotkey`       | `<cmd>+h` (Windows), `<ctrl>+<alt>+<space>` (Linux) | Global hotkey (Windows, X11); `<cmd>` = Win/Super |
| `hold_to_talk` | `false`                                   | `true` = record while held, release to stop          |
| `paste`        | `true`                                    | Ctrl+V into the focused window; `false` = clipboard only |
| `restore_clipboard` | `true`                               | Put the previous clipboard text back after pasting   |
| `raw`          | `false`                                   | Skip the server's AI cleanup pass                    |
| `dictionary`   | `""`                                      | Names and terms to spell right (comma-separated)     |
| `style`        | `""`                                      | Orpheus servers: `message`, `email`, `code`, or prose |
| `sounds`       | `true`                                    | Beep on start/stop                                   |

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

### Linux — X11

pynput handles the hotkey and the paste. Install `xclip` (or `xsel`) for the
clipboard and `notify-send` for notifications. If `pip install sounddevice`
fails or PortAudio is missing, the client records through `arecord`
(`alsa-utils`) instead. `install` writes
`~/.config/autostart/orpheus-desktop.desktop`.

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
