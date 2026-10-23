# Orpheus on the desktop

`orpheus_desktop.py` is a single-file push-to-talk client for Windows and
Linux. Press a hotkey, talk, press it again — the take goes to your Orpheus
server and the text is pasted into whatever has focus. Same server, same
cleanup pass, same personal dictionary as the phone. On Windows it installs
like any other app, sits in the tray and answers to Win+H.

## Windows

1. Download `OrpheusSetup-<version>.exe` from the
   [latest release](https://github.com/CocaKova/orpheus/releases/latest) and
   run it. It installs for your user only (no administrator prompt) into
   `%LOCALAPPDATA%\Programs\Orpheus`, adds **Orpheus** and **Orpheus settings**
   to the Start menu, and asks whether to start with Windows. The page before
   that lists everything it changes.
2. On first start the **Settings** window opens. Enter your server URL (and
   the API key if the server has one), press **Test connection**, then
   **Save**. It takes effect at once, no restart.
3. Press **Win+H**, talk, press Win+H again. The text lands wherever the
   cursor is.

Orpheus lives in the tray as the five-bar mark. Right-click → **Settings...**
changes anything later: server, hotkey (press **Record** and then the keys),
hold-to-talk, cleanup, style, keeping the period in chat messages, the
personal dictionary (one entry per line), beeps and starting with Windows.

Prefer no installer? The release also has
`OrpheusDesktop-<version>-portable.zip`: unzip it anywhere and run
`OrpheusDesktop.exe` from the folder. Uninstall the installed one from
Settings → Apps; that removes the program and its startup entry and leaves
your settings and saved takes alone.

Win+H is Windows' own voice-typing key; Orpheus takes it over while the
daemon runs by swallowing the keystroke before Windows sees it. To make sure
voice typing never pops up even if the daemon is down, run
`OrpheusDesktop.exe install` once: it also adds `H` to Explorer's
`DisabledHotkeys` list under HKCU (takes effect after you sign out and back
in; `uninstall` removes only that `H` and the startup entry). Prefer another
key? Record one in Settings, or type it there: `<cmd>` is the Win key, e.g. `<cmd>+<f9>`.

**Startup control.** "Start with Windows" writes
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Orpheus`, which is
exactly what Task Manager → **Startup apps** lists, so you can enable or
disable it there too. Nothing is written under HKLM.

The tray icon turns red while recording and shows an amber arc while the
server is transcribing; left-click toggles dictation. The exe has no console:
its log is `%LOCALAPPDATA%\Orpheus\orpheus-desktop.log`, and failed takes are
saved next to it under `failed\` (right-click → **Open failed takes folder**).

The installer is built by the `desktop-windows` GitHub Actions workflow
(`.github/workflows/desktop-windows.yml`) on every `v*` tag: PyInstaller
folder from `OrpheusDesktop.spec` (bootloader compiled from source in CI),
Inno Setup from `installer/orpheus.iss`, both signed through SignPath once
the project is approved (see the
[code signing policy](../README.md#code-signing-policy)). Until a release is
signed, SmartScreen warns the first time; "More info → Run anyway".

## From source (Windows, Linux)

Python 3.11 or newer.

```bash
cd desktop
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python orpheus_desktop.py              # tray app (headless daemon without pystray)
.venv/bin/python orpheus_desktop.py settings     # the Settings window
.venv/bin/python orpheus_desktop.py install      # start at login
```

The first start with no config opens the Settings window (it needs tkinter:
on Debian/Ubuntu `sudo apt install python3-tk`; the Windows build has it
built in). Without a display or tkinter, the first start writes a commented
config template instead, and `--setup` writes one on demand.

Config lives at `~/.config/orpheus/desktop.toml` (Linux) or
`%APPDATA%\Orpheus\desktop.toml` (Windows). The Settings window writes it;
it is plain TOML if you'd rather edit it, then run
`orpheus_desktop.py reload` (or restart) to apply. Set `url` to your server:

```toml
url = "http://your-host:8123"
```

Bare host, `/v1`, or the full `/v1/audio/transcriptions` path all work. Set
`api_key` only if the server has `ORPHEUS_API_KEY`. Most keys have a CLI flag
override (`--url`, `--hotkey`, `--hold`, `--no-paste`, `--raw`,
`--keep-period`, `--quiet`, `--no-tray`…), which also wins over a reload.

| Key            | Default                                   | Meaning                                              |
|----------------|-------------------------------------------|------------------------------------------------------|
| `hotkey`       | `<cmd>+h` (Windows), `<ctrl>+<alt>+<space>` (Linux) | Global hotkey (Windows, X11); `<cmd>` = Win/Super |
| `hold_to_talk` | `false`                                   | `true` = record while held, release to stop          |
| `paste`        | `true`                                    | Ctrl+V into the focused window; `false` = clipboard only |
| `restore_clipboard` | `true`                               | Put the previous clipboard text back after pasting   |
| `raw`          | `false`                                   | Skip the server's AI cleanup pass                    |
| `dictionary`   | `""`                                      | Names and terms to spell right (comma-separated)     |
| `style`        | `""`                                      | Orpheus servers: `message`, `email`, `code`, or prose |
| `keep_period`  | `false`                                   | Orpheus servers, `message` style: keep the period on a one-sentence message |
| `sounds`       | `true`                                    | Beep on start/stop                                   |

The daemon also listens on a local control socket, so any shortcut tool can
drive it:

```bash
python orpheus_desktop.py toggle     # start or stop
python orpheus_desktop.py start
python orpheus_desktop.py stop
python orpheus_desktop.py status
python orpheus_desktop.py reload     # re-read the config file
python orpheus_desktop.py quit       # returns once the daemon has stopped
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
