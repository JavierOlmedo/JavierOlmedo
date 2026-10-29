#!/usr/bin/env python3
"""Build the Hackpuntes Dark profile assets.

Reads assets/profile.toml (and assets/posts.json) and writes:
  * assets/dist/*.svg  - header, cards, section titles, nav pills, posts, footer
  * README.md          - the NAV, CERTS, PROJECTS, CVE-CARD, CVE-TABLE, POSTS and
                         FOOTER blocks; the README must always end with FOOTER

Every SVG embeds a JetBrains Mono subset with only the glyphs it uses, so
GitHub renders it with no external requests.

Usage: python assets/build.py [--check | --fetch-posts]
  --check        exit 1 if anything is out of date (used by CI)
  --fetch-posts  refresh assets/posts.json from the blog feed, then build
Needs: fonttools, brotli  (pip install -r assets/requirements.txt)
"""
import base64
import email.utils
import io
import json
import re
import sys
import textwrap
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DIST = ROOT / "dist"
ICONS = ROOT / "icons"
POSTS = ROOT / "posts.json"
README = REPO / "README.md"
FONTS = {
    400: ROOT / "fonts" / "JetBrainsMono-Regular.subset.woff2",
    700: ROOT / "fonts" / "JetBrainsMono-Bold.subset.woff2",
}

# Hackpuntes palette, taken from hackpuntes.com (dark theme, plus the
# light theme for the transparent pieces shown on GitHub's light mode).
DARK = {
    "bg": "#0d1117", "panel": "#161b22", "border": "#21262d", "text": "#adbac7",
    "head": "#e6edf3", "muted": "#768390", "accent": "#00e5be", "accent2": "#00b894",
}
LIGHT = {
    "bg": "#f0f6fc", "panel": "#ffffff", "border": "#d0d7de", "text": "#2d3640",
    "head": "#0d1117", "muted": "#57606a", "accent": "#00866f", "accent2": "#006b58",
}
SEVERITY = [  # (label, min score, colour, emoji)
    ("Critical", 9.0, "#fb4934", "🟥"),
    ("High", 7.0, "#fe8019", "🟧"),
    ("Medium", 4.0, "#fabd2f", "🟨"),
    ("Low", 0.1, "#00e5be", "🟩"),
]
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
W = 880
CARD_W = 430
# The profile always ends with this line (enforced by main()).
FOOTER = ("Made with ", "❤️", " in Spain")
ADVANCE = 0.6  # JetBrains Mono advance width, in em
# Header typing rhythm, in seconds.
TYPE_STEP, ERASE_STEP, HOLD, GAP = 0.075, 0.03, 5.0, 0.6


def cw(size):
    """Width of one character at the given font size."""
    return size * ADVANCE


def attr(text):
    return escape(text, {'"': "&quot;"})


def tw(text, size):
    return len(text) * cw(size)


def severity(score):
    return next(s for s in SEVERITY if score >= s[1])


def is_emoji(ch):
    o = ord(ch)
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o in (0xFE0F, 0x200D)


def runs_of(text):
    """Split text into (chunk, is_emoji, cells); an emoji takes two cells."""
    out = []
    for ch in text:
        e = is_emoji(ch)
        if out and out[-1][1] == e:
            out[-1][0] += ch
        else:
            out.append([ch, e])
    result = []
    for chunk, e in out:
        if e:
            bases = sum(1 for i, ch in enumerate(chunk)
                        if ch not in "️‍" and (i == 0 or chunk[i - 1] != "‍"))
            result.append((chunk, True, 2 * bases))
        else:
            result.append((chunk, False, len(chunk)))
    return result


def cells(text):
    return sum(n for _, _, n in runs_of(text))


def nice_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def font_faces(glyphs):
    """@font-face rules with a per-SVG subset of each weight in use."""
    rules = []
    for weight, chars in sorted(glyphs.items()):
        if not chars:
            continue
        font = TTFont(FONTS[weight], recalcTimestamp=False)
        options = subset.Options()
        options.flavor = "woff2"
        options.layout_features = ["kern"]
        subsetter = subset.Subsetter(options)
        subsetter.populate(text="".join(sorted(chars | {" "})))
        subsetter.subset(font)
        buf = io.BytesIO()
        font.flavor = "woff2"
        font.save(buf)
        data = base64.b64encode(buf.getvalue()).decode()
        rules.append(
            f"@font-face{{font-family:'JBM';font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}"
        )
    return "".join(rules)


class Svg:
    def __init__(self, width, height, label):
        self.w, self.h, self.label = width, height, label
        self.glyphs = {400: set(), 700: set()}
        self.defs, self.css, self.body = [], [], []

    def text(self, x, y, s, size=14, fill=DARK["text"], weight=400, anchor="start", attrs=""):
        self.glyphs[weight].update(s)
        if weight != 400:
            attrs = f' font-weight="{weight}"' + attrs
        if anchor != "start":
            attrs = f' text-anchor="{anchor}"' + attrs
        self.body.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"{attrs}>{escape(s)}</text>'
        )

    def runs(self, x, y, parts, size=14):
        """Draw consecutive (text, fill, weight) runs on one line."""
        for s, fill, weight in parts:
            self.text(x, y, s, size, fill, weight)
            x += tw(s, size)
        return x

    def mixed(self, x, y, s, size, fill, weight=400):
        """Draw text that may contain emoji, keeping the monospace grid."""
        for chunk, emoji, n in runs_of(s):
            if emoji:
                self.emoji(x, y, chunk, size)
            else:
                self.text(x, y, chunk, size, fill, weight)
            x += n * cw(size)
        return x

    def emoji(self, x, y, s, size):
        """Emoji use the viewer's colour emoji font, not the embedded subset."""
        self.body.append(f'<text class="e" x="{x:.1f}" y="{y:.1f}" font-size="{size}">{escape(s)}</text>')

    def add(self, markup):
        self.body.append(markup)

    def render(self):
        style = (
            font_faces(self.glyphs)
            + "text{font-family:'JBM','JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;"
            "font-kerning:normal;white-space:pre}"
            ".e{font-family:'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif}"
            + "".join(self.css)
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{attr(self.label)}">'
            f"<title>{escape(self.label)}</title>"
            f"<style>{style}</style>"
            f"<defs>{''.join(self.defs)}</defs>"
            f"{''.join(self.body)}</svg>\n"
        )


GLOW = (
    '<filter id="glow" x="-20%" y="-60%" width="140%" height="220%">'
    '<feGaussianBlur in="SourceGraphic" stdDeviation="{r}" result="b"/>'
    '<feComponentTransfer in="b" result="g"><feFuncA type="linear" slope="{a}"/></feComponentTransfer>'
    '<feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
)
REDUCED_MOTION = "@media (prefers-reduced-motion:reduce){.anim{animation:none!important}}"


def panel(svg, c=DARK, rx=12):
    svg.add(
        f'<rect x=".5" y=".5" width="{svg.w - 1}" height="{svg.h - 1}" rx="{rx}" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>'
    )


def logo_markup(x, y, height, fill):
    src = (ROOT / "brand" / "hackpuntes-logo.svg").read_text(encoding="utf-8")
    inner = re.search(r"<svg[^>]*>(.*)</svg>", src, re.S).group(1).replace("currentColor", fill)
    width = height * 2042 / 1550
    return (
        f'<svg x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height}" '
        f'viewBox="0 0 2042 1550">{inner}</svg>'
    ), width


def icon_markup(name, x, y, size, fill):
    """An icon from assets/icons/, recoloured to `fill`."""
    src = (ICONS / f"{name}.svg").read_text(encoding="utf-8")
    view_box = re.search(r'viewBox="([^"]+)"', src).group(1)
    inner = re.search(r"<svg[^>]*>(.*)</svg>", src, re.S).group(1)
    inner = re.sub(r"<title>.*?</title>", "", inner, flags=re.S).replace("currentColor", fill)
    return (
        f'<svg x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" viewBox="{view_box}" '
        f'fill="{fill}">{inner}</svg>'
    )


def fill_placeholders(text, data):
    return text.replace("{cves}", str(len(data["cves"])))


def keyframes(name, frames):
    """frames: [(seconds, css)] over a cycle; later entries win on ties."""
    total = frames[-1][0]
    merged = {}
    for t, css in frames:
        merged[f"{100 * t / total:.3f}".rstrip("0").rstrip(".") + "%"] = css
    return f"@keyframes {name}{{" + "".join(f"{k}{{{v}}}" for k, v in merged.items()) + "}"


# --- components ------------------------------------------------------------

def header(data):
    p = data["profile"]
    svg = Svg(W, 320, f'{p["name"]} · {p["tagline"]}')
    c = DARK
    svg.defs.append(GLOW.format(r=4, a=0.55))
    svg.defs.append(
        '<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">'
        f'<circle cx="2" cy="2" r="1" fill="{c["border"]}"/></pattern>'
        '<radialGradient id="halo"><stop offset="0" stop-color="#00e5be" stop-opacity=".16"/>'
        '<stop offset="1" stop-color="#00e5be" stop-opacity="0"/></radialGradient>'
        '<radialGradient id="fade" cx=".5" cy=".42" r=".75"><stop offset=".25" stop-color="#fff"/>'
        '<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
        f'<mask id="m"><rect width="{W}" height="320" fill="url(#fade)"/></mask>'
        f'<clipPath id="clip"><rect x="1" y="1" width="{W - 2}" height="318" rx="11"/></clipPath>'
    )
    panel(svg)
    svg.add(f'<g clip-path="url(#clip)"><rect width="{W}" height="320" fill="url(#dots)" mask="url(#m)" opacity=".9"/>')
    svg.add(f'<ellipse cx="{W / 2}" cy="92" rx="190" ry="110" fill="url(#halo)"/></g>')

    logo, _ = logo_markup((W - 152 * 2042 / 1550) / 2, 22, 152, c["accent"])
    svg.add(f'<g filter="url(#glow)" class="anim pulse">{logo}</g>')
    svg.text(W / 2, 214, p["name"], 34, c["accent"], 700, "middle", ' filter="url(#glow)"')
    svg.text(W / 2, 246, p["tagline"], 15, c["text"], 400, "middle")

    # Terminal line: each phrase is typed, held, erased, then the next one.
    size, y = 15, 290
    prompt = p["prompt"] + " "
    phrases = [fill_placeholders(t, data) for t in p["typed"]]
    widest = max(cells(t) for t in phrases)
    x0 = (W - tw(prompt, size) - (widest + 1) * cw(size)) / 2
    start = svg.runs(x0, y, [(prompt, c["accent"], 700)], size)

    timeline, t = [], 0.0
    for text in phrases:
        n = cells(text)
        typed, erased = t + n * TYPE_STEP, t + n * TYPE_STEP + HOLD + n * ERASE_STEP
        timeline.append((text, n, t, typed, typed + HOLD, erased))
        t = erased + GAP
    cycle = t

    cursor_frames = [(0, "transform:translateX(0)")]
    for i, (text, n, a, b, hold_end, d) in enumerate(timeline):
        reveal = n * cw(size)
        shown, hidden = f"transform:translateX({reveal:.1f}px)", "transform:translateX(0)"
        tick = f"animation-timing-function:steps({n},end)"
        frames = [(0, hidden), (a, f"{hidden};{tick}"), (b, shown), (hold_end, f"{shown};{tick}"),
                  (d, hidden), (cycle, hidden)]
        cursor_frames += frames[1:-1]
        visible = [(0, "opacity:0"), (a, "opacity:1"), (d, "opacity:0"), (cycle, "opacity:0")]
        svg.css.append(
            f".p{i}{{opacity:{1 if i == 0 else 0};animation:v{i} {cycle:.2f}s step-end infinite both}}"
            f".c{i}{{{shown};animation:t{i} {cycle:.2f}s infinite both}}"
            + keyframes(f"v{i}", visible) + keyframes(f"t{i}", frames)
        )
        svg.defs.append(
            f'<clipPath id="l{i}"><rect x="{start:.1f}" y="{y - 18}" width="{reveal + 2:.1f}" height="25"/></clipPath>'
        )
        svg.add(f'<g class="anim p{i}">')
        svg.mixed(start, y, text, size, c["head"])
        svg.add(
            f'<g clip-path="url(#l{i})"><rect class="anim c{i}" x="{start:.1f}" y="{y - 18}" '
            f'width="{reveal + 2:.1f}" height="25" fill="{c["bg"]}"/></g></g>'
        )
    cursor_frames.append((cycle, "transform:translateX(0)"))
    first_reveal = timeline[0][1] * cw(size)
    svg.css.append(
        f".cursor{{transform:translateX({first_reveal:.1f}px);"
        f"animation:cur {cycle:.2f}s infinite both,blink 1.1s step-end infinite}}"
        + keyframes("cur", cursor_frames)
        + "@keyframes blink{50%{opacity:0}}"
        ".pulse{animation:pulse 4s ease-in-out infinite}"
        "@keyframes pulse{50%{opacity:.82}}"
        + REDUCED_MOTION
    )
    svg.add(
        f'<rect class="anim cursor" x="{start + 1:.1f}" y="{y - 14}" width="{cw(size) - 1:.1f}" height="18" '
        f'fill="{c["accent"]}"/>'
    )
    return svg


def pill(label, icon):
    size, pad, isz, gap = 13, 12, 16, 8
    svg = Svg(round(pad + isz + gap + tw(label, size) + pad + 2), 34, label)
    svg.add(
        f'<rect x=".5" y=".5" width="{svg.w - 1}" height="33" rx="7" '
        f'fill="{DARK["bg"]}" stroke="{DARK["border"]}"/>'
    )
    svg.add(icon_markup(icon, pad, 9, isz, DARK["accent"]))
    svg.text(pad + isz + gap, 21.5, label, size, DARK["text"])
    return svg


def title(icon, text, note, c):
    svg = Svg(W, 50, text)
    dark = c is DARK
    if dark:
        svg.defs.append(GLOW.format(r=3, a=0.45))
    glow = ' filter="url(#glow)"' if dark else ""
    svg.emoji(2, 30, icon, 20)
    svg.text(40, 30, text, 20, c["accent"] if dark else c["head"], 700, attrs=glow)
    if note:
        svg.text(W - 4, 30, note, 13, c["muted"], 400, "end")
    svg.add(f'<line x1="0" y1="46.5" x2="{W}" y2="46.5" stroke="{c["border"]}"/>')
    return svg


def whoami(data):
    p, c = data["profile"], DARK
    rows = [(icon, k, fill_placeholders(v, data)) for icon, k, v in p["whoami"]]
    size, lh, top = 14, 28, 36
    h = top + 30 + lh * len(rows) + 22
    svg = Svg(W, h, "whoami: " + "; ".join(f"{k}: {v}" for _, k, v in rows))
    svg.defs.append(f'<clipPath id="clip"><rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="11"/></clipPath>')
    panel(svg)
    svg.add(f'<g clip-path="url(#clip)"><rect width="{W}" height="{top}" fill="{c["panel"]}"/></g>')
    svg.add(f'<line x1="1" y1="{top + .5}" x2="{W - 1}" y2="{top + .5}" stroke="{c["border"]}"/>')
    for i, dot in enumerate(("#fb4934", "#fabd2f", c["accent"])):
        svg.add(f'<circle cx="{22 + i * 18}" cy="18" r="5.5" fill="{dot}"/>')
    svg.text(W / 2, 22.5, p["prompt"].rstrip("$ "), 12, c["muted"], 400, "middle")

    x, y = 26, top + 34
    svg.runs(x, y, [(p["prompt"] + " ", c["accent"], 700), ("whoami --verbose", c["head"], 400)], size)
    keyw = max(len(k) for _, k, _ in rows) + 3
    for icon, k, v in rows:
        y += lh
        svg.emoji(x, y, icon, size)
        svg.text(x + 26, y, k, size, c["accent2"], 700)
        vx = x + 26 + tw(" " * keyw, size)
        for j, part in enumerate(v.split(" · ")):
            if j:
                vx = svg.runs(vx, y, [(" · ", c["muted"], 400)], size)
            vx = svg.runs(vx, y, [(part, c["text"], 400)], size)
    return svg


def cve_stats(data):
    cves = data["cves"]
    sev = {s[0]: 0 for s in SEVERITY}
    classes = {}
    for v in cves:
        sev[severity(v["cvss"])[0]] += 1
        classes[v["class"]] = classes.get(v["class"], 0) + 1
    years = sorted({int(v["id"][4:8]) for v in cves})
    return {
        "total": len(cves),
        "years": (years[0], years[-1]),
        "on_edb": sum(1 for v in cves if v["edb"]),
        "top": max(v["cvss"] for v in cves),
        "severity": sev,
        "classes": sorted(classes.items(), key=lambda kv: (-kv[1], kv[0])),
    }


def cve_label(data):
    s = cve_stats(data)
    return (
        f'{s["total"]} CVEs (CVE-{s["years"][0]} to CVE-{s["years"][1]}), {s["on_edb"]} with exploits on '
        f'Exploit-DB, max CVSS {s["top"]}. Severity: '
        + ", ".join(f"{n} {k}" for k, n in s["severity"].items() if n)
        + ". By class: " + ", ".join(f"{n} {k}" for k, n in s["classes"]) + "."
    )


def cve_card(data):
    c, s = DARK, cve_stats(data)
    total, classes = s["total"], s["classes"]
    h = max(232, 74 + 28 * len(classes))
    svg = Svg(W, h, cve_label(data))
    svg.defs.append(GLOW.format(r=5, a=0.5))
    panel(svg)

    # Left: the headline number.
    svg.text(32, 108, str(total), 76, c["accent"], 700, attrs=' filter="url(#glow)"')
    svg.text(32 + tw(str(total), 76) + 12, 82, "CVEs", 22, c["head"], 700)
    svg.text(32 + tw(str(total), 76) + 12, 106, "published", 14, c["muted"])
    svg.text(32, 148, f'CVE-{s["years"][0]} — {s["years"][1]}', 13, c["text"])
    svg.runs(32, 174, [(str(s["on_edb"]), c["accent"], 700), (" exploits on Exploit-DB", c["text"], 400)], 13)
    svg.runs(32, 200, [("max CVSS ", c["text"], 400), (f'{s["top"]}', severity(s["top"])[2], 700)], 13)

    def bars(x, heading, items, colour_of, label_w, bar_w):
        svg.text(x, 44, heading, 12, c["muted"])
        peak = max(n for _, n in items) or 1
        for i, (name, n) in enumerate(items):
            y = 74 + i * 28
            svg.text(x, y, name, 13, c["text"])
            bx = x + label_w
            svg.add(f'<rect x="{bx}" y="{y - 9}" width="{bar_w}" height="8" rx="4" fill="{c["panel"]}"/>')
            if n:
                svg.add(
                    f'<rect x="{bx}" y="{y - 9}" width="{max(8, bar_w * n / peak):.1f}" height="8" rx="4" '
                    f'fill="{colour_of(name)}"/>'
                )
            svg.text(bx + bar_w + 30, y, str(n), 13, c["head"] if n else c["muted"], 700, "end")

    colours = {sv[0]: sv[2] for sv in SEVERITY}
    bars(300, "severity · CVSS 3.x", list(s["severity"].items()), colours.get, 84, 150)
    bars(598, "by class", classes, lambda _: c["accent"], 128, 110)
    svg.add(f'<line x1="276.5" y1="28" x2="276.5" y2="{h - 28}" stroke="{c["border"]}"/>')
    svg.add(f'<line x1="574.5" y1="28" x2="574.5" y2="{h - 28}" stroke="{c["border"]}"/>')
    return svg


def project_card(item):
    c, size, pad = DARK, 13, 22
    width = int((CARD_W - 2 * pad) / cw(size))
    lines = textwrap.wrap(item["desc"], width)
    if len(lines) > 3:
        lines = lines[:2] + [textwrap.shorten(" ".join(lines[2:]), width, placeholder="…")]
    svg = Svg(CARD_W, 150, f'{item["name"]} ({item["tags"]}): {item["desc"]}')
    panel(svg)
    svg.text(pad, 38, item["name"], 16, c["accent"], 700)
    svg.text(CARD_W - pad, 38, "→", 16, c["muted"], 700, "end")
    x = pad
    for j, part in enumerate(item["tags"].split(" · ")):
        if j:
            x = svg.runs(x, 62, [(" · ", c["muted"], 400)], 12)
        x = svg.runs(x, 62, [(part, c["accent2"] if j == 0 else c["muted"], 400)], 12)
    for i, line in enumerate(lines):
        svg.text(pad, 94 + i * 20, line, size, c["text"])
    return svg


def studying_badge(item):
    """Hexagon badge, the size of the earned ones, for a certification in progress."""
    c = DARK
    svg = Svg(112, 112, f'{item["name"]} · {item["title"]} ({item["issuer"]}) · in progress')
    hexagon = "56,3 102,29.5 102,82.5 56,109 10,82.5 10,29.5"
    svg.add(
        f'<polygon points="{hexagon}" fill="{c["bg"]}" stroke="{c["accent"]}" stroke-width="2.5" '
        'stroke-dasharray="7 4" stroke-linejoin="round"/>'
    )
    svg.text(56, 50, item["name"], 24, c["accent"], 700, "middle")
    issuer = item["issuer"].upper()
    svg.text(56, 66, issuer, min(7.5, 76 / len(issuer) / ADVANCE), c["muted"], 700, "middle")
    svg.add(f'<rect x="21" y="75" width="70" height="15" rx="7.5" fill="{c["accent"]}"/>')
    svg.text(56, 86, "IN PROGRESS", 7.5, c["bg"], 700, "middle")
    return svg


def post_row(post):
    """One full-width row of the Recent Posts list, like the blog's home page."""
    c, pad, size = DARK, 18, 14
    when = nice_date(post["date"])
    room = int((W - 2 * pad - tw("› ", size) - tw(when, 13) - 24) / cw(size))
    text = post["title"] if len(post["title"]) <= room else post["title"][: room - 1].rstrip() + "…"
    svg = Svg(W, 44, f'{post["title"]} · {when}')
    panel(svg, rx=8)
    svg.runs(pad, 27, [("› ", c["accent"], 700), (text, c["text"], 400)], size)
    svg.text(W - pad, 27, when, 13, c["muted"], 400, "end")
    return svg


def footer(c):
    before, heart, after = FOOTER
    size, heart_w = 14, 14 * 1.2
    svg = Svg(W, 56, before + heart + after)
    svg.add(f'<line x1="0" y1="10.5" x2="{W}" y2="10.5" stroke="{c["border"]}"/>')
    x = (W - tw(before, size) - heart_w - tw(after, size)) / 2
    x = svg.runs(x, 40, [(before, c["text"], 400)], size)
    svg.emoji(x, 40, heart, size)
    svg.runs(x + heart_w, 40, [(after, c["text"], 400)], size)
    return svg


# --- README blocks ---------------------------------------------------------

def nav_block(data):
    items = [
        f'<a href="{attr(url)}"><img src="assets/dist/nav-{slug(label)}.svg" height="34" alt="{attr(label)}"></a>'
        for label, url, _ in data["profile"]["links"]
    ]
    return "<p align=\"center\">\n" + "\n".join(items) + "\n</p>"


def projects_block(data):
    cards = [
        f'<a href="{attr(p["url"])}"><img src="assets/dist/project-{slug(p["name"])}.svg" width="49%" '
        f'alt="{attr(p["name"] + ": " + p["desc"])}"></a>'
        for p in data.get("projects", [])
    ]
    return "<p align=\"center\">\n" + "\n".join(cards) + "\n</p>"


def certs_block(data):
    earned = [
        f'<a title="{attr(c["name"])}" href="{attr(c["url"])}"><img src="{attr(c["img"])}" height="96" '
        f'alt="{attr(c["name"])}"></a>'
        for c in data.get("certs", [])
    ]
    studying = [
        f'<a title="{attr(s["name"])} · in progress" href="{attr(s["url"])}">'
        f'<img src="assets/dist/studying-{slug(s["name"])}.svg" height="96" '
        f'alt="{attr(s["name"] + " · " + s["title"] + " (" + s["issuer"] + ") · in progress")}"></a>'
        for s in data.get("studying", [])
    ]
    return "<p align=\"center\">\n" + "\n".join(earned + studying) + "\n</p>"


def cve_card_block(data):
    return f'<img src="assets/dist/cves.svg" width="100%" alt="{attr(cve_label(data))}">'


def cve_table(data):
    lines = [
        "| CVE | Vulnerability | CVSS 3.x | Exploit-DB |",
        "|:--|:--|:--|:--:|",
    ]
    for v in data["cves"]:
        name, _, _, emoji = severity(v["cvss"])
        edb = f'[{v["edb"]}](https://www.exploit-db.com/exploits/{v["edb"]})' if v["edb"] else "—"
        target = v["name"].replace("<=", "≤").replace("|", "\\|")
        lines.append(
            f'| [{v["id"]}](https://www.cve.org/CVERecord?id={v["id"]}) | '
            f'{target} — [{v["vuln"]}]({v["link"]}) | {emoji} `{v["cvss"]}` {name} | {edb} |'
        )
    return "\n".join(lines)


def posts_block(posts):
    rows = [
        f'<a href="{attr(p["url"])}"><img src="assets/dist/post-{i}.svg" width="100%" '
        f'alt="{attr(p["title"] + " · " + nice_date(p["date"]))}"></a>'
        for i, p in enumerate(posts, 1)
    ]
    return "<p align=\"center\">\n" + "\n".join(rows) + "\n</p>"


def footer_block(data):
    views = (
        f'https://komarev.com/ghpvc/?username={data["profile"]["github"]}'
        "&style=for-the-badge&label=PROFILE+VIEWS&color=00e5be"
    )
    return (
        f'<p align="center">\n<img src="{views}" alt="Profile views">\n</p>\n\n'
        "<picture>\n"
        '  <source media="(prefers-color-scheme: light)" srcset="assets/dist/footer-light.svg">\n'
        f'  <img src="assets/dist/footer-dark.svg" width="100%" alt="{"".join(FOOTER)}">\n'
        "</picture>"
    )


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def replace_block(text, tag, content):
    pattern = re.compile(rf"(<!-- {tag}:START -->).*?(<!-- {tag}:END -->)", re.S)
    if not pattern.search(text):
        sys.exit(f"README.md is missing the {tag}:START/END markers")
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", text)


# --- posts feed ------------------------------------------------------------

def fetch_posts(feed, count):
    req = urllib.request.Request(feed, headers={"User-Agent": "hackpuntes-profile (+https://github.com/JavierOlmedo)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        sys.exit(f"{feed} did not return an RSS feed")
    posts = []
    for item in root.iter("item"):
        when = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
        posts.append({
            "title": " ".join(item.findtext("title").split()),
            "url": item.findtext("link").strip(),
            "date": when.date().isoformat(),
        })
    if not posts:
        sys.exit(f"{feed} has no items")
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts[:count]


# --- main ------------------------------------------------------------------

def outputs(data, posts):
    files = {
        "header.svg": header(data),
        "whoami.svg": whoami(data),
        "cves.svg": cve_card(data),
        "footer-dark.svg": footer(DARK),
        "footer-light.svg": footer(LIGHT),
    }

    def put(name, svg):
        if name in files:
            sys.exit(f"two items would both write assets/dist/{name}; rename one of them")
        files[name] = svg

    for key, (icon, text, note) in data["titles"].items():
        note = fill_placeholders(note, data)
        put(f"title-{key}-dark.svg", title(icon, text, note, DARK))
        put(f"title-{key}-light.svg", title(icon, text, note, LIGHT))
    for label, _, icon in data["profile"]["links"]:
        put(f"nav-{slug(label)}.svg", pill(label, icon))
    for item in data.get("projects", []):
        put(f"project-{slug(item['name'])}.svg", project_card(item))
    for item in data.get("studying", []):
        put(f"studying-{slug(item['name'])}.svg", studying_badge(item))
    for i, post in enumerate(posts, 1):
        put(f"post-{i}.svg", post_row(post))
    return {name: svg.render() for name, svg in files.items()}


def main():
    args = sys.argv[1:]
    check = "--check" in args
    data = tomllib.loads((ROOT / "profile.toml").read_text(encoding="utf-8"))
    data["titles"] = {k: tuple(v) for k, v in data.get("titles", {}).items()}

    if "--fetch-posts" in args:
        posts = fetch_posts(data["posts"]["feed"], data["posts"]["count"])
        fresh = json.dumps(posts, indent=2, ensure_ascii=False) + "\n"
        if not POSTS.exists() or POSTS.read_text(encoding="utf-8") != fresh:
            POSTS.write_text(fresh, encoding="utf-8")
            print(f"wrote {POSTS.relative_to(REPO)} ({len(posts)} posts)")
    posts = json.loads(POSTS.read_text(encoding="utf-8")) if POSTS.exists() else []

    wanted = {DIST / name: body for name, body in outputs(data, posts).items()}
    readme = README.read_text(encoding="utf-8")
    readme_new = replace_block(readme, "NAV", nav_block(data))
    readme_new = replace_block(readme_new, "CERTS", certs_block(data))
    readme_new = replace_block(readme_new, "PROJECTS", projects_block(data))
    readme_new = replace_block(readme_new, "CVE-CARD", cve_card_block(data))
    readme_new = replace_block(readme_new, "CVE-TABLE", cve_table(data))
    readme_new = replace_block(readme_new, "POSTS", posts_block(posts))
    readme_new = replace_block(readme_new, "FOOTER", footer_block(data))
    if not readme_new.rstrip().endswith("<!-- FOOTER:END -->"):
        sys.exit("README.md must end with the FOOTER block (Made with ❤️ in Spain)")
    missing = sorted({m for m in re.findall(r"assets/dist/([\w.-]+\.svg)", readme_new) if DIST / m not in wanted})
    if missing:
        sys.exit("README.md references SVGs the build does not produce: " + ", ".join(missing))
    wanted[README] = readme_new

    stale = [p for p in DIST.glob("*.svg") if p not in wanted]
    changed = [p for p, body in wanted.items() if not p.exists() or p.read_text(encoding="utf-8") != body]
    if check:
        for p in changed + stale:
            print(f"out of date: {p.relative_to(REPO)}")
        sys.exit(1 if changed or stale else 0)

    DIST.mkdir(exist_ok=True)
    for p in changed:
        p.write_text(wanted[p], encoding="utf-8")
        print(f"wrote {p.relative_to(REPO)} ({len(wanted[p]) / 1024:.1f} KB)")
    for p in stale:
        p.unlink()
        print(f"removed {p.relative_to(REPO)}")
    if not changed and not stale:
        print("up to date")


if __name__ == "__main__":
    main()
