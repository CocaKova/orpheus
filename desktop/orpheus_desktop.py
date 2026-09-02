#!/usr/bin/env python3
"""
Orpheus desktop — push-to-talk dictation for Windows and Linux.

    python orpheus_desktop.py                 # daemon: hotkey + control socket
    python orpheus_desktop.py toggle          # start/stop from a shortcut (Wayland)
    python orpheus_desktop.py start | stop | status | quit
    python orpheus_desktop.py transcribe clip.wav
    python orpheus_desktop.py --setup         # write the config template

Press the hotkey, talk, press it again (or release it in hold-to-talk mode).
The take is recorded at 16 kHz mono, sent to the Orpheus server, and the text
is pasted into whatever has focus. Nothing is stored except a failed take,
which is saved so it is never lost.

Required:  requests, numpy, sounddevice, pynput   (see requirements.txt)
Optional:  arecord (Linux mic fallback when portaudio is missing),
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

DEFAULTS = {
    "url": "http://localhost:8123",
    "api_key": "",
    "model": "",
    "hotkey": "<ctrl>+<alt>+space",
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
hotkey = "<ctrl>+<alt>+space"

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


def log(msg: str) -> None:
    print(f"[orpheus] {msg}", flush=True)


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
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
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
    d = cache_dir() / "failed"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"take-{datetime.now():%Y%m%d-%H%M%S}.wav"
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
            self.state = "recording"
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
                self.state = "idle"
                beep("error", self.cfg)
                notify("Orpheus", f"Mic failed: {e}")
                log(f"mic failed: {e}")
                return f"error: {e}"
            self.state = "busy"
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
                self.state = "idle"

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

    # ---- hotkey ----
    def hotkey_thread(self) -> threading.Thread | None:
        spec = self.cfg["hotkey"]
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
                if self.cfg["hold_to_talk"]:
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


# ------------------------------------------------------------------ cli

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Orpheus desktop push-to-talk dictation")
    ap.add_argument("command", nargs="?", default="daemon",
                    choices=["daemon", "toggle", "start", "stop", "status", "quit", "transcribe"])
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
    ap.add_argument("--version", action="version", version=f"orpheus desktop {VERSION}")
    a = ap.parse_args(argv)

    path = Path(a.config) if a.config else config_path()
    if a.setup:
        if path.exists():
            log(f"config already exists: {path}")
        else:
            write_template(path)
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

    Daemon(cfg, a.fake_audio).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
