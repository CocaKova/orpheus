"""Run: python test_desktop.py  (no pytest needed; no display, mic or server needed)"""
import http.server
import json
import os
import shutil
import struct
import tempfile
import threading
import tomllib
import wave
from pathlib import Path

os.environ.pop("DISPLAY", None)          # keep the daemon headless in here
os.environ.pop("WAYLAND_DISPLAY", None)
cache = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = cache

import orpheus_desktop as od  # noqa: E402


def eq(got, want):
    assert got == want, f"\n got: {got!r}\nwant: {want!r}"


tmp = Path(tempfile.mkdtemp())

# --- config: render → parse round trip, comments kept
cfg = dict(od.DEFAULTS)
cfg.update(url="http://box:8123/v1", api_key='k"e\\y', hotkey="<ctrl>+<alt>+<space>",
           dictionary="Ünïcode, pie torch=PyTorch", style="message", keep_period=True,
           raw=True, sounds=False, control_port=47999)
p = tmp / "desktop.toml"
od.save_config(p, cfg)
text = p.read_text(encoding="utf-8")
assert "# Orpheus servers, message style" in text, "template comments survive a save"
assert "@" not in text.replace("<", "").split("dictionary =")[0], "every placeholder filled"
eq(tomllib.loads(text), cfg)
eq(od.load_config(p), cfg)
assert not (tmp / "desktop.toml.tmp").exists()

# control characters and DEL stay valid TOML
od.save_config(p, dict(od.DEFAULTS, dictionary="a\tb\x7fc\nd"))
eq(od.load_config(p)["dictionary"], "a\tb\x7fc\nd")

# --- config: a missing file is not created by loading it
missing = tmp / "nope" / "desktop.toml"
eq(od.load_config(missing), od.DEFAULTS)
assert not missing.exists()

# --- config: hand edits are coerced, a broken file falls back to defaults
p.write_text('paste = "false"\nkeep_period = "yes"\ncontrol_port = "47200"\nstyle = 3\n',
             encoding="utf-8")
c = od.load_config(p)
eq((c["paste"], c["keep_period"], c["control_port"], c["style"]), (False, True, 47200, "3"))
p.write_text("url = [broken", encoding="utf-8")
eq(od.load_config(p), od.DEFAULTS)
assert od.config_problem(p) and "Saving replaces it" in od.config_problem(p)
assert od.config_problem(missing) is None

# --- keep_period: default off, and the field is only sent when on
eq(od.DEFAULTS["keep_period"], False)
eq("trailing_period" in od.SttClient.from_config(od.DEFAULTS).form_fields(), False)
f = od.SttClient.from_config(dict(od.DEFAULTS, keep_period=True, style="message")).form_fields()
eq((f["trailing_period"], f["style"]), ("keep", "message"))
f = od.SttClient.from_config(dict(od.DEFAULTS, raw=True, dictionary="a, b")).form_fields()
eq((f["clean"], f["prompt"]), ("false", "a, b"))

# --- level math: peak and silence, exact values (this replaced numpy; same results)
def pcm(*samples):
    return struct.pack(f"<{len(samples)}h", *samples)


eq(od.pcm_stats(b""), (0.0, 0))
eq(od.pcm_stats(b"\x01"), (0.0, 0))                          # half a sample is no sample
eq(od.pcm_stats(pcm(0, 0, 0)), (0.0, 3))
eq(od.pcm_stats(pcm(100, -200, 50)), (200 / 32768, 3))
eq(od.pcm_stats(pcm(-32768)), (1.0, 1))                       # int16's minimum, no overflow
eq(od.pcm_stats(pcm(32767, -1)), (32767 / 32768, 2))
eq(od.pcm_stats(pcm(16) + b"\x7f"), (16 / 32768, 1))          # trailing odd byte ignored
eq(od.pcm_stats(b"\x00\x80\xff\x7f"), (1.0, 2))              # little-endian -32768, 32767
quiet, loud = 393, 394                                        # 0.012 * 32768 = 393.2
eq(od.is_silence(pcm(*[quiet] * 16000)), True)
eq(od.is_silence(pcm(*[loud] * 16000)), False)
eq(od.is_silence(pcm(*[loud] * 4799)), True)                  # under 0.3 s at 16 kHz
eq(od.is_silence(pcm(*[loud] * 4800)), False)
eq(od.is_silence(pcm(*[-loud] * 8000), rate=16000), False)    # the sign doesn't matter
try:                                                          # and the old numpy formula agrees
    import numpy as np
    import random
    rnd = random.Random(7)
    raw = pcm(*[rnd.randint(-32768, 32767) for _ in range(50000)], -32768)
    old = float(np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.int32)).max()) / 32768.0
    eq(od.pcm_stats(raw), (old, 50001))
except ImportError:
    pass

# --- dictionary: the window shows one per line and saves commas (every server version reads those)
eq(od.dictionary_entries("Kubernetes\npie torch=PyTorch;  gRPC ,,\n\n"),
   ["Kubernetes", "pie torch=PyTorch", "gRPC"])
eq(od.dictionary_entries(""), [])

# --- server_base / endpoint_url agree
for u in ("http://h:8123", "http://h:8123/", "http://h:8123/v1", "http://h:8123/v1/audio/transcriptions"):
    eq(od.server_base(u), "http://h:8123")
    eq(od.endpoint_url(u), "http://h:8123/v1/audio/transcriptions")
eq(od.server_base("https://api.example.com/openai/v1"), "https://api.example.com/openai")

# --- hotkey capture → spec, and the friendly name
eq(od.hotkey_from_keys({"ctrl", "alt"}, "space"), "<ctrl>+<alt>+<space>")
eq(od.hotkey_from_keys({"cmd"}, "h"), "<cmd>+h")
eq(od.hotkey_from_keys({"shift", "ctrl"}, "H"), "<ctrl>+<shift>+h")
eq(od.hotkey_from_keys(set(), "F9"), "<f9>")
eq(od.hotkey_from_keys(set(), "h"), None)               # a bare letter would fire while typing
eq(od.hotkey_from_keys({"ctrl"}, "Control_L"), None)    # only modifiers so far
eq(od.hotkey_from_keys({"ctrl"}, "exclam"), None)       # can't name it: type it instead
eq(od.friendly_hotkey("<ctrl>+<alt>+<space>"), "Ctrl + Alt + Space")
eq(od.friendly_hotkey("ctrl+alt+f9"), "Ctrl + Alt + F9")
eq(od.hotkey_problem(""), "Pick a hotkey.")
eq(od.hotkey_problem("<ctrl>+<alt>+<space>"), None)
try:
    from pynput import keyboard  # noqa: F401  (needs a display on Linux)
    have_pynput = True
except Exception:
    have_pynput = False
if have_pynput:
    assert od.hotkey_problem("<ctrl>+<nosuchkey>"), "pynput refuses an unknown key"
else:
    eq(od.hotkey_problem("<ctrl>+<nosuchkey>"), None)   # can't check here; the daemon logs it

# --- Test connection against a fake Orpheus
class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            body = {"status": "ok", "version": "9.9.9"}
        elif self.path == "/v1/models":
            if self.headers.get("Authorization") != "Bearer right":
                self.send_response(401)
                self.end_headers()
                return
            body = {"object": "list", "data": [{"id": "parakeet"}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
eq(od.check_server(base + "/v1", "right"), (True, "Connected to Orpheus 9.9.9. Key accepted."))
eq(od.check_server(base, ""), (False, "The server wants an API key."))
eq(od.check_server(base, "wrong"), (False, "The server refused this API key."))
eq(od.check_server("box:8123"), (False, "The URL should start with http:// or https://"))
ok, msg = od.check_server("http://127.0.0.1:9", timeout=1)
assert not ok and msg.startswith("Can't reach"), msg

# --- the daemon takes a reload without a restart; CLI overrides still win
wav = tmp / "take.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(struct.pack("<h", 0) * 1600)
od.HAS_DISPLAY = False                  # no real hotkey hook in here (Windows always has a display)
cfgp = tmp / "daemon.toml"
od.save_config(cfgp, dict(od.DEFAULTS, url="http://one:8123"))
d = od.Daemon(od.load_config(cfgp), str(wav), cfg_path=cfgp, overrides={"sounds": False})
od.save_config(cfgp, dict(od.DEFAULTS, url="http://two:8123", keep_period=True, sounds=True))
eq(d.handle("reload"), "reloaded")
eq(d.client.endpoint, "http://two:8123/v1/audio/transcriptions")
eq(d.client.keep_period, True)
eq(d.cfg["sounds"], False)
eq(d.handle("hotkey-on"), "hotkey unavailable")         # no display in here
eq(d.handle("hotkey-off"), "hotkey off")
eq(od.Daemon(dict(od.DEFAULTS), str(wav)).handle("reload"), "error: no config file")

# --- self_command runs this file again with the same python
cmd = od.self_command("settings")
eq(cmd[-1], "settings")
assert cmd[-2].endswith("orpheus_desktop.py")

srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(cache, ignore_errors=True)
print("ok")
