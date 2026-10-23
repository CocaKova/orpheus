# Third-party software

Orpheus itself is [MIT](LICENSE). The Windows desktop build (the installer and the portable
folder) bundles the components below, each under its own license. Running the desktop client
from source installs the Python packages from [`desktop/requirements.txt`](desktop/requirements.txt).

| Component | What it does in Orpheus | License |
|---|---|---|
| [Python](https://www.python.org) 3.12 | the runtime | PSF-2.0 |
| [Tcl/Tk](https://www.tcl-lang.org) 8.6, through tkinter | draws the Settings window | Tcl/Tk license (BSD-style) |
| [PyInstaller](https://pyinstaller.org) bootloader | starts the app | GPL-2.0-or-later with the bootloader exception |
| [requests](https://github.com/psf/requests), urllib3, idna, charset-normalizer, certifi | talks to the transcription server | Apache-2.0; MIT; BSD-3-Clause; MIT; MPL-2.0 |
| [numpy](https://numpy.org) | measures the take's level for the silence check | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice), cffi, pycparser, [PortAudio](https://www.portaudio.com) | records from the microphone | MIT; MIT-0; BSD-3-Clause; PortAudio license (MIT-style) |
| [pynput](https://github.com/moses-palmer/pynput), six | the global hotkey and the Ctrl+V paste | LGPL-3.0; MIT |
| [pystray](https://github.com/moses-palmer/pystray) | the tray icon and its notifications | LGPL-3.0 |
| [Pillow](https://python-pillow.org) | draws the Orpheus mark for the tray and windows | MIT-CMU |

The Settings window's look:

| Component | What it does in Orpheus | License |
|---|---|---|
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) 6.0.0 by Tom Schimansky, with its Roboto fonts | rounded cards and switches in the phone app's colours | MIT (its PyPI "License" field says CC0, but the LICENSE file it ships and the repository are MIT); Roboto: Apache-2.0 |
| [darkdetect](https://github.com/albertosottile/darkdetect) 0.8.0 by Alberto Sottile | a CustomTkinter dependency | BSD-3-Clause |
| [packaging](https://github.com/pypa/packaging) 26.3 | a CustomTkinter dependency | Apache-2.0 OR BSD-2-Clause |

The server downloads NVIDIA's Parakeet TDT model from Hugging Face on first start, under the
model's own license; it is not part of any Orpheus download.
