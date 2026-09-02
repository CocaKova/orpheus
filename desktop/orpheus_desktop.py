#!/usr/bin/env python3
"""
Orpheus desktop — push-to-talk dictation for Windows and Linux.

    python orpheus_desktop.py                 # daemon: hotkey + control socket
    python orpheus_desktop.py toggle          # start/stop from a shortcut (Wayland)
    python orpheus_desktop.py start | stop | status | quit
    python orpheus_desktop.py transcribe clip.wav
    python orpheus_desktop.py install         # start at login (Task Manager → Startup apps)
    python orpheus_desktop.py uninstall
    python orpheus_desktop.py --setup         # write the config template

Press the hotkey, talk, press it again (or release it in hold-to-talk mode).
The take is recorded at 16 kHz mono, sent to the Orpheus server, and the text
is pasted into whatever has focus. Nothing is stored except a failed take,
which is saved so it is never lost.

Required:  requests, numpy, sounddevice, pynput   (see requirements.txt)
Optional:  pystray + Pillow (tray icon; headless daemon without them),
           arecord (Linux mic fallback when portaudio is missing),
           wl-copy/wl-paste or xclip/xsel, wtype or ydotool (Wayland paste),
           notify-send, paplay/aplay.
"""
from __future__ import annotations

import argparse
import io
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

VERSION = "1.1.0"
RATE = 16000
IS_WIN = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_WAYLAND = IS_LINUX and (os.environ.get("XDG_SESSION_TYPE") == "wayland"
                           or bool(os.environ.get("WAYLAND_DISPLAY")))
HAS_DISPLAY = IS_WIN or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

DEFAULT_HOTKEY = "<cmd>+h" if IS_WIN else "<ctrl>+<alt>+<space>"

DEFAULTS = {
    "url": "http://localhost:8123",
    "api_key": "",
    "model": "",
    "hotkey": DEFAULT_HOTKEY,
    "hold_to_talk": False,
    "paste": True,
    "restore_clipboard": True,
    "raw": False,
    "dictionary": "",
    "style": "",
    "sounds": True,
    "control_port": 47123,   # Windows only; Linux uses a unix socket
}

CONFIG_TEMPLATE = """# Orpheus desktop client — edit and restart the daemon.

# Orpheus (or any OpenAI-compatible) transcription server.
# Bare host, /v1, or the full /v1/audio/transcriptions path all work.
url = "http://localhost:8123"

# Only if the server has ORPHEUS_API_KEY set.
api_key = ""

# Optional model name (ignored by Orpheus, required by some other servers).
model = ""

# Global hotkey (pynput syntax). Windows and X11 only; on Wayland bind
# `orpheus_desktop.py toggle` as a system shortcut instead.
# Windows default <cmd>+h = Win+H, which replaces Windows voice typing.
hotkey = "@HOTKEY@"

# false = press once to start, again to stop.  true = record while held.
hold_to_talk = false

# Paste the text into the focused window (Ctrl+V). false = clipboard only.
paste = true

# Put whatever was on the clipboard back after pasting.
restore_clipboard = true

# Skip the server's AI cleanup pass (verbatim ASR output).
raw = false

# Names and terms to spell right, comma-separated (sent as the OpenAI
# `prompt` hint; Orpheus servers also take heard=meant pairs).
dictionary = ""

# Orpheus servers: "" (prose) | message | email | code — how to dress the text.
style = ""

# Beep on record start/stop.
sounds = true

# Windows only: local TCP port for the toggle/start/stop commands.
control_port = 47123
"""


_LOG_FILE: Path | None = None


def log(msg: str) -> None:
    line = f"[orpheus] {msg}"
    if sys.stdout is not None:
        print(line, flush=True)
    if sys.stdout is None or getattr(sys, "frozen", False):
        _log_to_file(line)


def _log_to_file(line: str) -> None:
    """No console (pythonw / the exe): append to the log file instead."""
    global _LOG_FILE
    try:
        if _LOG_FILE is None:
            _LOG_FILE = cache_dir() / "orpheus-desktop.log"
            if _LOG_FILE.exists() and _LOG_FILE.stat().st_size > 1_000_000:
                _LOG_FILE.replace(_LOG_FILE.with_suffix(".log.1"))
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except Exception:
        pass


# ------------------------------------------------------------------ config

def config_path() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Orpheus" / "desktop.toml"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "orpheus" / "desktop.toml"


def cache_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        d = base / "Orpheus"
    else:
        d = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "orpheus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE.replace("@HOTKEY@", DEFAULT_HOTKEY), encoding="utf-8")
    log(f"wrote config template: {path}")


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    if not path.exists():
        write_template(path)
        return cfg
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        log("python 3.11+ needed for tomllib; using defaults")
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for k in DEFAULTS:
        if k in data:
            cfg[k] = data[k]
    return cfg


# ------------------------------------------------------------------ audio

def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm_stats(pcm: bytes) -> tuple[float, int]:
    """(peak amplitude 0..1, sample count) for int16 PCM."""
    n = len(pcm) // 2
    if n == 0:
        return 0.0, 0
    try:
        import numpy as np
        arr = np.frombuffer(pcm[: n * 2], dtype=np.int16)
        return float(np.abs(arr.astype(np.int32)).max()) / 32768.0, n
    except ImportError:
        peak = max(abs(s) for s in struct.unpack(f"<{n}h", pcm[: n * 2]))
        return peak / 32768.0, n


def is_silence(pcm: bytes, rate: int = RATE) -> bool:
    peak, n = pcm_stats(pcm)
    return n < rate * 0.3 or peak < 0.012


class SoundDeviceRecorder:
    """Mic capture via portaudio."""

    def __init__(self) -> None:
        import sounddevice as sd  # raises if portaudio is missing
        self._sd = sd
        self._chunks: list[bytes] = []
        self._stream = None

    def start(self) -> None:
        self._chunks = []

        def cb(indata, frames, t, status):
            self._chunks.append(bytes(indata))

        self._stream = self._sd.RawInputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=1024, callback=cb)
        self._stream.start()

    def stop(self) -> bytes:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return b"".join(self._chunks)


class ArecordRecorder:
    """Linux fallback: ALSA `arecord` subprocess (no portaudio needed)."""

    def __init__(self) -> None:
        if not shutil.which("arecord"):
            raise RuntimeError("arecord not found")
        self._proc: subprocess.Popen | None = None
        self._buf = io.BytesIO()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        self._buf = io.BytesIO()
        self._proc = subprocess.Popen(
            ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def pump():
            assert self._proc and self._proc.stdout
            for chunk in iter(lambda: self._proc.stdout.read(4096), b""):
                self._buf.write(chunk)

        self._reader = threading.Thread(target=pump, daemon=True)
        self._reader.start()

    def stop(self) -> bytes:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            if self._reader:
                self._reader.join(timeout=2)
            err = (self._proc.stderr.read() if self._proc.stderr else b"").decode("utf-8", "replace").strip()
            self._proc = None
            if not self._buf.getvalue() and err:
                raise RuntimeError(err.splitlines()[-1])
        return self._buf.getvalue()


class FakeRecorder:
    """Feeds a WAV file instead of the mic (testing on headless boxes)."""

    def __init__(self, path: str) -> None:
        self.path = path
        with wave.open(path, "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError("fake audio must be 16-bit PCM WAV")
            self.rate = w.getframerate()
            frames = w.readframes(w.getnframes())
            if w.getnchannels() > 1:  # keep the first channel
                ch = w.getnchannels()
                frames = b"".join(frames[i:i + 2] for i in range(0, len(frames), 2 * ch))
        self.pcm = frames

    def start(self) -> None:
        pass

    def stop(self) -> bytes:
        return self.pcm


def make_recorder(fake: str | None):
    if fake:
        return FakeRecorder(fake)
    try:
        return SoundDeviceRecorder()
    except Exception as e:  # ImportError or OSError (no portaudio)
        if IS_LINUX:
            try:
                r = ArecordRecorder()
                log(f"sounddevice unavailable ({e.__class__.__name__}); using arecord")
                return r
            except RuntimeError:
                pass
        raise SystemExit(f"no audio input backend: {e}\n"
                         "  pip install sounddevice   (needs portaudio)\n"
                         "  or on Linux: apt install alsa-utils   (arecord)")


# ------------------------------------------------------------------ server

def endpoint_url(base: str) -> str:
    b = base.strip().rstrip("/")
    if b.endswith("/audio/transcriptions"):
        return b
    if b.endswith("/v1"):
        return b + "/audio/transcriptions"
    return b + "/v1/audio/transcriptions"


class SttClient:
    RETRY_BUDGET_S = 40
    RETRY_STEPS = (2, 3, 5, 8, 10, 12)

    def __init__(self, url: str, api_key: str = "", model: str = "", raw: bool = False,
                 dictionary: str = "", style: str = "") -> None:
        import requests
        self._requests = requests
        self.endpoint = endpoint_url(url)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model
        self.raw = raw
        self.dictionary = dictionary
        self.style = style

    def transcribe(self, wav: bytes, rate: int = RATE) -> str:
        data = {"response_format": "json"}
        if self.model:
            data["model"] = self.model
        if self.raw:
            data["clean"] = "false"
        if self.dictionary:
            data["prompt"] = self.dictionary
        if self.style:
            data["style"] = self.style
        t0 = time.monotonic()
        deadline = t0 + self.RETRY_BUDGET_S
        last = "no response"
        for i, wait in enumerate((0,) + self.RETRY_STEPS):
            if wait:
                if time.monotonic() + wait > deadline:
                    break
                log(f"server busy ({last}); retry {i} in {wait}s")
                time.sleep(wait)
            try:
                resp = self._requests.post(
                    self.endpoint, headers=self.headers, data=data,
                    files={"file": ("take.wav", wav, "audio/wav")}, timeout=(5, 120))
            except self._requests.ConnectionError as e:
                last = f"connection error: {e.__class__.__name__}"
                continue
            if resp.status_code == 503:
                last = "HTTP 503 " + resp.text[:80].strip()
                continue
            if not resp.ok:
                raise RuntimeError(f"STT server error {resp.status_code}: {resp.text[:200]}")
            return (resp.json().get("text") or "").strip()
        raise RuntimeError(f"STT server unavailable after {time.monotonic() - t0:.0f}s ({last})")


def save_failed_take(wav: bytes) -> Path:
    p = failed_dir() / f"take-{datetime.now():%Y%m%d-%H%M%S}.wav"
    p.write_bytes(wav)
    return p


# ------------------------------------------------------------------ clipboard

def _run(cmd: list[str], inp: bytes | None = None, timeout: float = 3) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=inp, capture_output=True, timeout=timeout)


class Clipboard:
    def __init__(self) -> None:
        self.backend = self._pick()

    def _pick(self) -> str:
        if IS_WIN:
            return "win"
        if IS_WAYLAND and shutil.which("wl-copy"):
            return "wl"
        if shutil.which("xclip"):
            return "xclip"
        if shutil.which("xsel"):
            return "xsel"
        if shutil.which("wl-copy"):
            return "wl"
        return "none"

    def get(self) -> str | None:
        """Current clipboard text, or None if unreadable / not text."""
        try:
            if self.backend == "win":
                return _win_clip_get()
            if self.backend == "wl":
                r = _run(["wl-paste", "--no-newline", "--type", "text/plain"])
            elif self.backend == "xclip":
                r = _run(["xclip", "-selection", "clipboard", "-o"])
            elif self.backend == "xsel":
                r = _run(["xsel", "--clipboard", "--output"])
            else:
                return None
            return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
        except Exception:
            return None

    def set(self, text: str) -> bool:
        try:
            if self.backend == "win":
                _win_clip_set(text)
                return True
            b = text.encode("utf-8")
            if self.backend == "wl":
                r = _run(["wl-copy", "--type", "text/plain"], b)
            elif self.backend == "xclip":
                r = _run(["xclip", "-selection", "clipboard", "-i"], b)
            elif self.backend == "xsel":
                r = _run(["xsel", "--clipboard", "--input"], b)
            else:
                return False
            return r.returncode == 0
        except Exception as e:
            log(f"clipboard set failed: {e}")
            return False


def _win_libs():
    import ctypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.GetClipboardData.argtypes = [ctypes.c_uint]
    u32.GetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    return ctypes, u32, k32


CF_UNICODETEXT = 13


def _win_clip_get() -> str | None:
    ctypes, u32, k32 = _win_libs()
    if not u32.OpenClipboard(None):
        return None
    try:
        if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None  # not text (image, files…): don't touch it
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _win_clip_set(text: str) -> None:
    ctypes, u32, k32 = _win_libs()
    data = text.encode("utf-16-le") + b"\x00\x00"
    h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    if not u32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(CF_UNICODETEXT, h)  # clipboard owns h from here
    finally:
        u32.CloseClipboard()


# ------------------------------------------------------------------ paste

def press_ctrl_v() -> str | None:
    """Simulate Ctrl+V. Returns the method used, or None if nothing worked."""
    if IS_WAYLAND:
        if shutil.which("wtype"):
            if _run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"]).returncode == 0:
                return "wtype"
        if shutil.which("ydotool"):
            # keycodes: 29 = LEFTCTRL, 47 = V
            if _run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]).returncode == 0:
                return "ydotool"
        return None
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.ctrl):
            kb.press("v")
            kb.release("v")
        return "pynput"
    except Exception as e:
        log(f"pynput paste failed: {e}")
        return None


def deliver(text: str, cfg: dict, clip: Clipboard) -> str:
    """Clipboard + paste. Returns a short status string for the notification."""
    previous = clip.get() if cfg["restore_clipboard"] else None
    if not clip.set(text):
        return "no clipboard backend — text printed only"
    if not cfg["paste"]:
        return "copied"
    time.sleep(0.08)  # let the clipboard owner settle before the paste lands
    method = press_ctrl_v()
    if method is None:
        return "copied (no paste tool — install wtype or ydotool)"
    if previous is not None and previous != text:
        def restore():
            time.sleep(0.5)
            clip.set(previous)
        threading.Thread(target=restore, daemon=True).start()
    return f"pasted via {method}"


# ------------------------------------------------------------------ feedback

_TONE_FILES: dict[int, Path] = {}


def _tone_file(freq: int, ms: int = 90) -> Path:
    if freq not in _TONE_FILES:
        import math
        n = RATE * ms // 1000
        pcm = b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * freq * i / RATE)
                                  * min(1.0, (n - i) / (n * 0.3))))
            for i in range(n))
        p = cache_dir() / f"tone-{freq}.wav"
        if not p.exists():
            p.write_bytes(wav_bytes(pcm))
        _TONE_FILES[freq] = p
    return _TONE_FILES[freq]


def beep(kind: str, cfg: dict) -> None:
    if not cfg["sounds"]:
        return
    freq = {"start": 880, "stop": 660, "error": 330}[kind]

    def play():
        try:
            if IS_WIN:
                import winsound
                winsound.Beep(freq, 90)
                return
            f = str(_tone_file(freq))
            for cmd in (["paplay", f], ["aplay", "-q", f]):
                if shutil.which(cmd[0]) and _run(cmd, timeout=2).returncode == 0:
                    return
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass

    threading.Thread(target=play, daemon=True).start()


def notify(title: str, body: str) -> None:
    def send():
        try:
            if IS_WIN:
                ps = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                    "$t = $x.GetElementsByTagName('text');"
                    "$t.Item(0).AppendChild($x.CreateTextNode($env:ORPHEUS_T)) | Out-Null;"
                    "$t.Item(1).AppendChild($x.CreateTextNode($env:ORPHEUS_B)) | Out-Null;"
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show("
                    "[Windows.UI.Notifications.ToastNotification]::new($x))"
                )
                env = dict(os.environ, ORPHEUS_T=title, ORPHEUS_B=body)
                subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                               env=env, capture_output=True, timeout=8)
            elif shutil.which("notify-send"):
                _run(["notify-send", "-a", "Orpheus", "-t", "4000", title, body], timeout=3)
        except Exception:
            pass

    threading.Thread(target=send, daemon=True).start()


# ------------------------------------------------------------------ daemon

class Daemon:
    def __init__(self, cfg: dict, fake_audio: str | None = None) -> None:
        self.cfg = cfg
        self.recorder = make_recorder(fake_audio)
        self.rate = getattr(self.recorder, "rate", RATE)
        self.client = SttClient(cfg["url"], cfg["api_key"], cfg["model"], cfg["raw"],
                                cfg["dictionary"], cfg["style"])
        self.clip = Clipboard()
        self.state = "idle"          # idle | recording | busy
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.started_at = 0.0
        self.on_state = None         # tray hook: called with the new state
        self.on_quit = None          # tray hook: called when serve() ends

    def _set_state(self, state: str) -> None:
        self.state = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    # ---- state machine ----
    def start(self) -> str:
        with self.lock:
            if self.state != "idle":
                return f"ignored: {self.state}"
            try:
                self.recorder.start()
            except Exception as e:
                beep("error", self.cfg)
                notify("Orpheus", f"Mic failed: {e}")
                return f"error: {e}"
            self._set_state("recording")
            self.started_at = time.monotonic()
        beep("start", self.cfg)
        log("recording…")
        return "recording"

    def stop(self) -> str:
        with self.lock:
            if self.state != "recording":
                return f"ignored: {self.state}"
            try:
                pcm = self.recorder.stop()
            except Exception as e:
                self._set_state("idle")
                beep("error", self.cfg)
                notify("Orpheus", f"Mic failed: {e}")
                log(f"mic failed: {e}")
                return f"error: {e}"
            self._set_state("busy")
        beep("stop", self.cfg)
        secs = time.monotonic() - self.started_at
        log(f"stopped after {secs:.1f}s ({len(pcm) // 2} samples)")
        threading.Thread(target=self._process, args=(pcm,), daemon=True).start()
        return "transcribing"

    def toggle(self) -> str:
        return self.stop() if self.state == "recording" else self.start()

    def _process(self, pcm: bytes) -> None:
        try:
            if is_silence(pcm, self.rate):
                log("heard nothing")
                notify("Orpheus", "Heard nothing")
                return
            wav = wav_bytes(pcm, self.rate)
            t0 = time.monotonic()
            try:
                text = self.client.transcribe(wav, self.rate)
            except Exception as e:
                saved = save_failed_take(wav)
                beep("error", self.cfg)
                log(f"transcription failed: {e}\n  take saved: {saved}")
                notify("Orpheus — failed", f"{e}\nTake saved: {saved}")
                return
            dt = time.monotonic() - t0
            if not text:
                log("heard nothing (empty transcript)")
                notify("Orpheus", "Heard nothing")
                return
            status = deliver(text, self.cfg, self.clip)
            log(f"{dt:.1f}s {status}: {text}")
            notify("Orpheus", text if len(text) < 200 else text[:197] + "…")
        finally:
            with self.lock:
                self._set_state("idle")

    # ---- control socket ----
    def handle(self, cmd: str) -> str:
        cmd = cmd.strip().lower()
        if cmd == "toggle":
            return self.toggle()
        if cmd == "start":
            return self.start()
        if cmd == "stop":
            return self.stop()
        if cmd == "status":
            return self.state
        if cmd == "quit":
            self.stop_event.set()
            return "bye"
        return f"unknown command: {cmd}"

    def serve(self) -> None:
        srv = control_listener()
        srv.settimeout(0.5)
        try:
            while not self.stop_event.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                with conn:
                    try:
                        data = conn.recv(256).decode("utf-8", "replace")
                        conn.sendall((self.handle(data) + "\n").encode())
                    except OSError:
                        pass
        finally:
            srv.close()
            if not IS_WIN:
                try:
                    Path(control_address()).unlink()
                except OSError:
                    pass
            if self.on_quit:
                try:
                    self.on_quit()
                except Exception:
                    pass

    # ---- hotkey ----
    def hotkey_thread(self) -> threading.Thread | None:
        spec = normalize_hotkey(self.cfg["hotkey"])
        if IS_WAYLAND or not HAS_DISPLAY:
            why = "Wayland session" if IS_WAYLAND else "no display"
            log(f"global hotkey disabled ({why}). Bind a system shortcut to:\n"
                f"    {sys.executable} {Path(__file__).resolve()} toggle\n"
                "  GNOME: Settings → Keyboard → Custom Shortcuts.  KDE: System Settings → Shortcuts.")
            return None
        try:
            from pynput import keyboard
        except Exception as e:
            log(f"pynput unavailable ({e}); hotkey disabled, control socket only")
            return None

        def run():
            try:
                if IS_WIN and "<cmd>" in spec.lower():
                    self._win_cmd_hotkey_loop(spec)
                elif self.cfg["hold_to_talk"]:
                    combo = set(keyboard.HotKey.parse(spec))
                    hk = keyboard.HotKey(list(combo), self.start)

                    def on_press(k):
                        hk.press(listener.canonical(k))

                    def on_release(k):
                        ck = listener.canonical(k)
                        hk.release(ck)
                        if ck in combo and self.state == "recording":
                            self.stop()

                    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                        listener.join()
                else:
                    with keyboard.GlobalHotKeys({spec: self.toggle}) as h:
                        h.join()
            except Exception as e:
                log(f"hotkey listener died: {e}")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        mode = "hold to talk" if self.cfg["hold_to_talk"] else "press to toggle"
        log(f"hotkey {spec} ({mode})")
        return t

    def _win_cmd_hotkey_loop(self, spec: str) -> None:
        """Win-key hotkeys need a low-level hook that swallows the letter so the
        Windows shortcut (Win+H = voice typing) never fires."""
        from pynput import keyboard
        from pynput.keyboard import Key
        kb = keyboard.Controller()
        hold = self.cfg["hold_to_talk"]

        def fire(down: bool) -> None:
            if hold:
                (self.start if down else self.stop)()
            elif down:
                self.toggle()

        def inject_ctrl() -> None:
            # AutoHotkey trick: a harmless modifier tap while Win is still held
            # stops the Start menu from opening when Win comes back up.
            kb.press(Key.ctrl)
            kb.release(Key.ctrl)

        hk = WinCmdHotkey(spec, fire, inject_ctrl)

        def filt(msg, data):
            if hk.event(msg, data.vkCode, bool(data.flags & LLKHF_INJECTED)):
                listener.suppress_event()
            return True

        listener = keyboard.Listener(win32_event_filter=filt)
        with listener:
            listener.join()

    def run(self) -> None:
        log(f"orpheus desktop {VERSION} → {self.client.endpoint}")
        log(f"clipboard: {self.clip.backend}; paste: {'on' if self.cfg['paste'] else 'off'}; "
            f"control: {control_address()}")
        self.hotkey_thread()
        try:
            self.serve()
        except KeyboardInterrupt:
            pass
        log("bye")


def control_address() -> str:
    if IS_WIN:
        return f"127.0.0.1:{DEFAULTS['control_port']}"
    name = f"orpheus-desktop-{os.getuid()}.sock"
    run = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    path = Path(run) / name
    if len(str(path)) > 100:  # AF_UNIX paths are capped at ~108 bytes
        path = Path(tempfile.gettempdir()) / name
    return str(path)


def control_listener() -> socket.socket:
    if IS_WIN:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", DEFAULTS["control_port"]))
        except OSError:
            raise SystemExit(f"another daemon is already running on {control_address()}")
    else:
        path = control_address()
        if os.path.exists(path):
            # stale socket from a crashed daemon? refuse if someone answers
            if send_command("status", quiet=True) is not None:
                raise SystemExit(f"another daemon is already running on {path}")
            os.unlink(path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        os.chmod(path, 0o600)
    s.listen(4)
    return s


def send_command(cmd: str, quiet: bool = False) -> str | None:
    try:
        if IS_WIN:
            s = socket.create_connection(("127.0.0.1", DEFAULTS["control_port"]), timeout=2)
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(control_address())
        with s:
            s.sendall(cmd.encode())
            return s.recv(256).decode().strip()
    except OSError:
        if not quiet:
            log("daemon not running (start it with: orpheus_desktop.py)")
        return None


# ------------------------------------------------------------------ win hotkey

LLKHF_INJECTED = 0x10
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
_VK_GROUPS = {
    "cmd": (0x5B, 0x5C),          # left / right Win
    "ctrl": (0x11, 0xA2, 0xA3),
    "alt": (0x12, 0xA4, 0xA5),
    "shift": (0x10, 0xA0, 0xA1),
}
_NAMED_VKS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22, "pause": 0x13, "caps_lock": 0x14,
    "scroll_lock": 0x91, **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def normalize_hotkey(spec: str) -> str:
    """pynput wants named keys in angle brackets: 'ctrl+alt+space' → '<ctrl>+<alt>+<space>'."""
    parts = []
    for part in spec.split("+"):
        part = part.strip()
        if len(part) > 1 and not (part.startswith("<") and part.endswith(">")):
            part = f"<{part}>"
        parts.append(part)
    return "+".join(parts)


def parse_win_hotkey(spec: str) -> tuple[list[tuple[int, ...]], int]:
    """'<cmd>+h' → ([(0x5B, 0x5C)], 0x48): modifier vk groups + one trigger vk."""
    groups: list[tuple[int, ...]] = []
    trigger = None
    for part in normalize_hotkey(spec).lower().split("+"):
        part = part.strip()
        if part.startswith("<") and part.endswith(">"):
            name = {"win": "cmd", "super": "cmd", "control": "ctrl"}.get(part[1:-1], part[1:-1])
            if name in _VK_GROUPS:
                groups.append(_VK_GROUPS[name])
                continue
            if name not in _NAMED_VKS:
                raise ValueError(f"unsupported key in hotkey: {part}")
            trigger = _NAMED_VKS[name]
        elif len(part) == 1 and part.isalnum() and part.isascii():
            trigger = ord(part.upper())   # VK_A..VK_Z / VK_0..VK_9 equal ASCII
        elif len(part) == 1:
            import ctypes
            trigger = ctypes.windll.user32.VkKeyScanW(ord(part)) & 0xFF
        else:
            raise ValueError(f"unsupported key in hotkey: {part}")
    if trigger is None or not groups:
        raise ValueError("hotkey needs modifiers plus one key, e.g. <cmd>+h")
    return groups, trigger


class WinCmdHotkey:
    """State machine for a modifier+key hotkey on a Windows low-level hook.

    event() returns True when the event must be suppressed. Pure Python so the
    logic is testable off-Windows; the hook glue lives in the Daemon.
    """

    def __init__(self, spec: str, fire, inject_ctrl) -> None:
        self.groups, self.trigger = parse_win_hotkey(spec)
        self.mod_vks = {vk for g in self.groups for vk in g}
        self.held: set[int] = set()
        self.trigger_down = False
        self.fire = fire                # fire(down: bool)
        self.inject_ctrl = inject_ctrl

    def _mods_active(self) -> bool:
        return all(any(vk in self.held for vk in g) for g in self.groups)

    def _dispatch(self, down: bool) -> None:
        def go():
            if down and (0x5B in self.held or 0x5C in self.held):
                self.inject_ctrl()
            self.fire(down)
        threading.Thread(target=go, daemon=True).start()

    def event(self, msg: int, vk: int, injected: bool) -> bool:
        if injected:
            return False                # our own Ctrl tap and the Ctrl+V paste
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if vk in self.mod_vks:
            (self.held.add if down else self.held.discard)(vk)
            return False
        if vk != self.trigger:
            return False
        if down:
            if not self._mods_active():
                return False
            if not self.trigger_down:   # ignore auto-repeat
                self.trigger_down = True
                self._dispatch(True)
            return True
        if self.trigger_down:           # release after our press, even if Win went up first
            self.trigger_down = False
            self._dispatch(False)
            return True
        return False


# ------------------------------------------------------------------ autostart / install

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_ADVANCED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_OWN_KEY = r"Software\Orpheus\Desktop"


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    if IS_WIN:
        pyw = exe.with_name("pythonw.exe")
        if pyw.exists():
            exe = pyw
    return f'"{exe}" "{Path(__file__).resolve()}"'


def autostart_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "orpheus-desktop.desktop"


def autostart_enabled() -> bool:
    if IS_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, "Orpheus")
            return True
        except OSError:
            return False
    return autostart_desktop_path().exists()


def autostart_set(enabled: bool) -> None:
    if IS_WIN:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, "Orpheus", 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, "Orpheus")
                except OSError:
                    pass
        log(f"start with Windows: {'on' if enabled else 'off'} (HKCU\\...\\Run\\Orpheus; "
            "toggle it in Task Manager → Startup apps too)")
        return
    p = autostart_desktop_path()
    if enabled:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "[Desktop Entry]\nType=Application\nName=Orpheus Desktop\n"
            "Comment=Push-to-talk dictation\n"
            f"Exec={launch_command()}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
            encoding="utf-8")
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    log(f"start at login: {'on' if enabled else 'off'} ({p})")


def win_disabled_hotkeys(add: bool) -> None:
    """Belt and braces for Win+H: Explorer's DisabledHotkeys list. Takes effect
    after sign-out. Only removes the H we added ourselves."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _ADVANCED_KEY) as k:
        try:
            current, _ = winreg.QueryValueEx(k, "DisabledHotkeys")
        except OSError:
            current = ""
        current = str(current or "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _OWN_KEY) as own:
            try:
                we_added = winreg.QueryValueEx(own, "DisabledHotkeysAddedH")[0] == 1
            except OSError:
                we_added = False
            if add:
                if "H" in current.upper():
                    log("Win+H already in DisabledHotkeys")
                    return
                winreg.SetValueEx(k, "DisabledHotkeys", 0, winreg.REG_SZ, current + "H")
                winreg.SetValueEx(own, "DisabledHotkeysAddedH", 0, winreg.REG_DWORD, 1)
                log("added H to Explorer DisabledHotkeys (Win+H voice typing off after sign-out)")
            elif we_added:
                idx = current.upper().find("H")
                if idx >= 0:
                    winreg.SetValueEx(k, "DisabledHotkeys", 0, winreg.REG_SZ,
                                      current[:idx] + current[idx + 1:])
                winreg.DeleteValue(own, "DisabledHotkeysAddedH")
                log("removed our H from Explorer DisabledHotkeys (after sign-out)")


def install(enabled: bool) -> None:
    autostart_set(enabled)
    if IS_WIN:
        try:
            win_disabled_hotkeys(enabled)
        except Exception as e:
            log(f"DisabledHotkeys registry step failed: {e}")


def open_path(p: Path) -> None:
    try:
        if IS_WIN:
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as e:
        log(f"could not open {p}: {e}")


# ------------------------------------------------------------------ tray

_ICON_COLORS = {"idle": (0xB3, 0x9D, 0xFF), "recording": (0xFF, 0x52, 0x52), "busy": (0xFF, 0xC4, 0x6B)}
_ICON_LABELS = {"idle": "Idle", "recording": "Recording...", "busy": "Transcribing..."}  # ASCII: X11 WM_NAME is latin-1


def make_icon_image(state: str = "idle", size: int = 64):
    """The Orpheus mark: five rounded bars on a violet disc (amber arc while busy)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((1, 1, size - 2, size - 2), fill=(0x20, 0x1A, 0x33, 255))
    color = _ICON_COLORS[state] + (255,)
    cx = cy = size / 2
    if state == "busy":
        inset = size * 0.28
        d.arc((inset, inset, size - inset, size - inset), start=300, end=210,
              fill=color, width=max(2, int(size * 0.07)))
        return img
    heights = (0.35, 0.62, 0.88, 0.62, 0.35) if state == "idle" else (0.55, 0.9, 0.7, 0.95, 0.5)
    w = size * 0.085
    gap = size * 0.16
    x = cx - gap * 2
    for h in heights:
        half = size * 0.34 * h
        d.rounded_rectangle((x - w / 2, cy - half, x + w / 2, cy + half), radius=w / 2, fill=color)
        x += gap
    return img


def run_tray(daemon: "Daemon", cfg_path: Path) -> bool:
    """Run the daemon behind a tray icon. Returns False if the tray can't run
    (no display, pystray/Pillow missing) so the caller falls back to headless."""
    if not HAS_DISPLAY:
        return False
    try:
        import pystray
    except Exception as e:
        log(f"tray unavailable ({e.__class__.__name__}: {e}); running headless")
        return False
    try:
        images = {s: make_icon_image(s) for s in _ICON_LABELS}
    except Exception as e:
        log(f"tray icon needs Pillow ({e}); running headless")
        return False

    def on_state(state: str) -> None:
        icon.icon = images[state]
        icon.title = f"Orpheus: {_ICON_LABELS[state]}"
        icon.update_menu()

    def toggle_autostart() -> None:
        autostart_set(not autostart_enabled())

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: _ICON_LABELS[daemon.state], None, enabled=False),
        pystray.MenuItem("Toggle dictation", lambda: daemon.toggle(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows" if IS_WIN else "Start at login",
                         toggle_autostart, checked=lambda item: autostart_enabled()),
        pystray.MenuItem("Open config", lambda: open_path(cfg_path)),
        pystray.MenuItem("Open failed takes folder", lambda: open_path(failed_dir())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda: daemon.stop_event.set()),
    )
    try:
        icon = pystray.Icon("orpheus", images["idle"], "Orpheus: Idle", menu=menu)
    except Exception as e:
        log(f"tray unavailable ({e.__class__.__name__}: {e}); running headless")
        return False
    daemon.on_state = on_state
    daemon.on_quit = icon.stop
    worker = threading.Thread(target=daemon.run, daemon=True)
    worker.start()
    try:
        icon.run()
    except Exception as e:
        log(f"tray failed ({e}); continuing headless")
        daemon.on_state = daemon.on_quit = None
        worker.join()
    return True


def failed_dir() -> Path:
    d = cache_dir() / "failed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _attach_console() -> None:
    """Frozen exe launched from a terminal: reuse the parent's console so
    --version / status print something."""
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
    except Exception:
        pass


# ------------------------------------------------------------------ cli

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if IS_WIN and getattr(sys, "frozen", False) and sys.stdout is None:
        _attach_console()
    if "--version" in argv:
        log(f"orpheus desktop {VERSION}")
        return 0
    ap = argparse.ArgumentParser(description="Orpheus desktop push-to-talk dictation")
    ap.add_argument("command", nargs="?", default="daemon",
                    choices=["daemon", "toggle", "start", "stop", "status", "quit", "transcribe",
                             "install", "uninstall"])
    ap.add_argument("file", nargs="?", help="WAV file for `transcribe`")
    ap.add_argument("--setup", action="store_true", help="write the config template and exit")
    ap.add_argument("--config", help="config file path")
    ap.add_argument("--url")
    ap.add_argument("--api-key")
    ap.add_argument("--model")
    ap.add_argument("--hotkey")
    ap.add_argument("--hold", action="store_true", help="hold-to-talk instead of toggle")
    ap.add_argument("--no-paste", action="store_true", help="clipboard only")
    ap.add_argument("--no-restore", action="store_true", help="don't restore the clipboard")
    ap.add_argument("--raw", action="store_true", help="skip the server's cleanup pass")
    ap.add_argument("--quiet", action="store_true", help="no beeps")
    ap.add_argument("--fake-audio", metavar="WAV", help="use this WAV instead of the mic (testing)")
    ap.add_argument("--no-tray", action="store_true", help="headless daemon, no tray icon")
    ap.add_argument("--version", action="store_true", help="print the version")
    a = ap.parse_args(argv)

    path = Path(a.config) if a.config else config_path()
    if a.setup:
        if path.exists():
            log(f"config already exists: {path}")
        else:
            write_template(path)
        return 0

    if a.command in ("install", "uninstall"):
        install(a.command == "install")
        return 0

    if a.command in ("toggle", "start", "stop", "status", "quit"):
        reply = send_command(a.command)
        if reply is None:
            return 1
        print(reply)
        return 0

    cfg = load_config(path)
    if a.url:
        cfg["url"] = a.url
    if a.api_key is not None:
        cfg["api_key"] = a.api_key
    if a.model is not None:
        cfg["model"] = a.model
    if a.hotkey:
        cfg["hotkey"] = a.hotkey
    if a.hold:
        cfg["hold_to_talk"] = True
    if a.no_paste:
        cfg["paste"] = False
    if a.no_restore:
        cfg["restore_clipboard"] = False
    if a.raw:
        cfg["raw"] = True
    if a.quiet:
        cfg["sounds"] = False
    DEFAULTS["control_port"] = int(cfg["control_port"])

    if a.command == "transcribe":
        if not a.file:
            ap.error("transcribe needs a WAV file")
        rec = FakeRecorder(a.file)
        if is_silence(rec.pcm, rec.rate):
            log("heard nothing (silence)")
            return 2
        client = SttClient(cfg["url"], cfg["api_key"], cfg["model"], cfg["raw"],
                                cfg["dictionary"], cfg["style"])
        t0 = time.monotonic()
        try:
            text = client.transcribe(wav_bytes(rec.pcm, rec.rate), rec.rate)
        except Exception as e:
            saved = save_failed_take(wav_bytes(rec.pcm, rec.rate))
            log(f"failed: {e}\n  take saved: {saved}")
            return 1
        log(f"{time.monotonic() - t0:.1f}s")
        print(text)
        return 0

    daemon = Daemon(cfg, a.fake_audio)
    if a.no_tray or not run_tray(daemon, path):
        daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
