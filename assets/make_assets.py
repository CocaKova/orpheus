#!/usr/bin/env python3
"""Regenerate the README header art (hero-dark.svg, hero-light.svg). Stdlib only."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# GitHub-like neutrals; the accent is the app's own violet (BubbleView COLOR_IDLE #b39dff),
# deepened on light so it keeps contrast on white.
THEMES = {
    "dark": dict(bg="#0d1117", panel="#161b22", line="#30363d", text="#e6edf3", dim="#8b949e",
                 accent="#b39dff", accent2="#d6c9ff"),
    "light": dict(bg="#ffffff", panel="#f6f8fa", line="#d0d7de", text="#1f2328", dim="#656d76",
                  accent="#6e56cf", accent2="#8b75e0"),
}
FONT = "ui-sans-serif, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"


def box(x, y, w, h, label, sub, c, stroke, dashed=False, size=16):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{c["panel"]}" stroke="{stroke}" stroke-width="1.5"{dash}/>'
            f'<text x="{x + w / 2}" y="{y + 29}" text-anchor="middle" font-family="{FONT}" font-size="{size}" font-weight="600" fill="{c["text"]}">{label}</text>'
            f'<text x="{x + w / 2}" y="{y + 50}" text-anchor="middle" font-family="{FONT}" font-size="12.5" fill="{c["dim"]}">{sub}</text>')


def chip(x, y, w, h, label, c):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{c["panel"]}" stroke="{c["line"]}" stroke-width="1.5"/>'
            f'<text x="{x + 14}" y="{y + h / 2 + 5}" font-family="{FONT}" font-size="14" font-weight="600" fill="{c["text"]}">{label}</text>')


def arrow(x1, y1, x2, y2, color, dashed=False):
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    # unit vector so the head points along the line, whatever its angle
    dx, dy = x2 - x1, y2 - y1
    n = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / n, dy / n
    bx, by = x2 - 8 * ux, y2 - 8 * uy
    px, py = -uy * 5, ux * 5
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2 - 7 * ux:.1f}" y2="{y2 - 7 * uy:.1f}" stroke="{color}" stroke-width="1.8"{dash}/>'
            f'<path d="M{bx + px:.1f},{by + py:.1f} L{x2},{y2} L{bx - px:.1f},{by - py:.1f} Z" fill="{color}"/>')


def mark(x, y, c):
    """The app icon's five waveform bars (ic_orpheus_foreground.xml), scaled down."""
    bars = [(0, 12, "accent"), (8, 24, "accent"), (16, 40, "accent2"), (24, 24, "accent"), (32, 12, "accent")]
    mid = y
    return "".join(f'<rect x="{x + bx}" y="{mid - h / 2}" width="6" height="{h}" rx="3" fill="{c[k]}"/>'
                   for bx, h, k in bars)


def hero(c):
    W, H = 1200, 340
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
         'aria-label="Orpheus: the Android orb, the desktop hotkey client or any OpenAI-compatible client sends audio to '
         'your own server, which decodes it, transcribes it with Parakeet TDT, applies a spoken-symbol pre-pass and an '
         'optional LLM cleanup, and returns the text">',
         f'<rect width="{W}" height="{H}" rx="16" fill="{c["bg"]}" stroke="{c["line"]}"/>',
         mark(60, 66, c),
         f'<text x="112" y="78" font-family="{MONO}" font-size="38" font-weight="700" fill="{c["text"]}">orpheus</text>',
         f'<text x="60" y="114" font-family="{FONT}" font-size="18" fill="{c["dim"]}">'
         'Speak on your phone or desktop. Your own server transcribes and tidies it. The text lands at the cursor.</text>']

    # clients
    s.append(f'<text x="60" y="166" font-family="{FONT}" font-size="12.5" fill="{c["dim"]}">clients</text>')
    clients = [("Android orb", 178), ("desktop hotkey", 226), ("any OpenAI STT client", 274)]
    for label, y in clients:
        s.append(chip(60, y, 210, 38, label, c))

    # server group
    gx, gy, gw, gh = 330, 150, 810, 168
    s.append(f'<rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="12" fill="none" stroke="{c["line"]}" stroke-width="1.5" stroke-dasharray="2 4"/>')
    s.append(f'<text x="{gx + 20}" y="{gy + 26}" font-family="{FONT}" font-size="12.5" fill="{c["dim"]}">your server · '
             f'<tspan font-family="{MONO}" fill="{c["text"]}">POST /v1/audio/transcriptions</tspan></text>')
    by, bw, bh, gap = gy + 44, 177, 68, 20
    x0 = gx + (gw - (4 * bw + 3 * gap)) / 2
    nodes = [("decode", "ffmpeg → 16 kHz mono", c["line"], False),
             ("Parakeet TDT", "NeMo speech-to-text", c["accent"], False),
             ("pre-pass", "spoken symbols, lists", c["line"], False),
             ("LLM cleanup", "optional, any chat API", c["line"], True)]
    for i, (label, sub, stroke, dashed) in enumerate(nodes):
        x = x0 + i * (bw + gap)
        s.append(box(x, by, bw, bh, label, sub, c, stroke, dashed))
        if i:
            s.append(arrow(x - gap, by + bh / 2, x, by + bh / 2, c["dim"]))
    s.append(f'<text x="{gx + 20}" y="{gy + gh - 18}" font-family="{FONT}" font-size="12.5" fill="{c["dim"]}">'
             'If the cleanup drops words or the endpoint is down, the pre-pass text comes back instead.</text>')

    # clients -> server
    for _, y in clients:
        s.append(arrow(270, y + 19, x0 - 2, by + bh / 2, c["dim"]))
    s.append("</svg>")
    return "".join(s)


for theme, colors in THEMES.items():
    with open(os.path.join(HERE, f"hero-{theme}.svg"), "w", encoding="utf-8") as f:
        f.write(hero(colors))
print("ok")
