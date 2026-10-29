#!/usr/bin/env python3
"""Build the Hackpuntes Dark profile assets.

Reads assets/profile.toml and writes:
  * assets/dist/*.svg  - header, cards, section titles, nav pills, footer
  * README.md          - the blocks between NAV, PROJECTS and CVE-TABLE markers

Every SVG embeds a JetBrains Mono subset with only the glyphs it uses, so
GitHub renders it with no external requests.

Usage: python assets/build.py [--check]
Needs: fonttools, brotli  (pip install fonttools brotli)
"""
import base64
import io
import re
import sys
import textwrap
import tomllib
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DIST = ROOT / "dist"
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
W = 880
CARD_W = 430
ADVANCE = 0.6  # JetBrains Mono advance width, in em


def cw(size):
    """Width of one character at the given font size."""
    return size * ADVANCE


def attr(text):
    return escape(text, {'"': "&quot;"})


def tw(text, size):
    return len(text) * cw(size)


def severity(score):
    return next(s for s in SEVERITY if score >= s[1])


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


def panel(svg, c=DARK):
    svg.add(
        f'<rect x=".5" y=".5" width="{svg.w - 1}" height="{svg.h - 1}" rx="12" '
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


def fill_placeholders(text, data):
    return text.replace("{cves}", str(len(data["cves"])))


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

    logo, lw = logo_markup((W - 152 * 2042 / 1550) / 2, 22, 152, c["accent"])
    svg.add(f'<g filter="url(#glow)" class="anim pulse">{logo}</g>')
    svg.text(W / 2, 214, p["name"], 34, c["accent"], 700, "middle", ' filter="url(#glow)"')
    svg.text(W / 2, 246, p["tagline"], 15, c["text"], 400, "middle")

    # Terminal line typed out once, then a blinking block cursor.
    size, y = 15, 290
    prompt, typed = p["prompt"] + " ", fill_placeholders(p["typed"], data)
    x0 = (W - tw(prompt + typed + " ", size)) / 2
    x1 = svg.runs(x0, y, [(prompt, c["accent"], 700), (typed, c["head"], 400)], size)
    reveal, steps = x1 - (x0 + tw(prompt, size)), len(typed)
    start = x0 + tw(prompt, size)
    svg.css.append(
        f".cover{{transform:translateX({reveal:.1f}px);animation:type 2.2s steps({steps}) .8s both}}"
        f".cursor{{transform:translateX({reveal:.1f}px);animation:type 2.2s steps({steps}) .8s both,"
        "blink 1.1s step-end 3s infinite}"
        f"@keyframes type{{from{{transform:translateX(0)}}to{{transform:translateX({reveal:.1f}px)}}}}"
        "@keyframes blink{50%{opacity:0}}"
        ".pulse{animation:pulse 4s ease-in-out infinite}"
        "@keyframes pulse{50%{opacity:.82}}"
        + REDUCED_MOTION
    )
    svg.defs.append(
        f'<clipPath id="line"><rect x="{start:.1f}" y="{y - 16}" width="{reveal + 2:.1f}" height="22"/></clipPath>'
    )
    svg.add(
        f'<g clip-path="url(#line)"><rect class="anim cover" x="{start:.1f}" y="{y - 16}" '
        f'width="{reveal + 2:.1f}" height="22" fill="{c["bg"]}"/></g>'
        f'<rect class="anim cursor" x="{start + 1:.1f}" y="{y - 14}" width="{cw(size) - 1:.1f}" height="18" '
        f'fill="{c["accent"]}"/>'
    )
    return svg


def pill(label):
    size, pad = 13, 14
    text = "› " + label
    svg = Svg(round(tw(text, size) + pad * 2), 34, label)
    svg.add(
        f'<rect x=".5" y=".5" width="{svg.w - 1}" height="33" rx="7" '
        f'fill="{DARK["bg"]}" stroke="{DARK["border"]}"/>'
    )
    svg.runs(pad, 21.5, [("› ", DARK["accent"], 700), (label, DARK["text"], 400)], size)
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
    h = top + 30 + lh * (len(rows) + 1) + 18
    svg = Svg(W, h, "whoami: " + "; ".join(f"{k}: {v}" for _, k, v in rows))
    svg.defs.append(f'<clipPath id="clip"><rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="11"/></clipPath>')
    panel(svg)
    svg.add(f'<g clip-path="url(#clip)"><rect width="{W}" height="{top}" fill="{c["panel"]}"/>')
    logo, lw = logo_markup(W - 186, h - 150, 124, c["accent"])
    svg.add(f'<g opacity=".07">{logo}</g></g>')
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
    y += lh
    px = svg.runs(x, y, [(p["prompt"] + " ", c["accent"], 700)], size)
    svg.css.append(".cursor{animation:blink 1.1s step-end infinite}@keyframes blink{50%{opacity:0}}" + REDUCED_MOTION)
    svg.add(f'<rect class="anim cursor" x="{px:.1f}" y="{y - 13}" width="{cw(size) - 1:.1f}" height="17" fill="{c["accent"]}"/>')
    return svg


def cve_card(data):
    cves, c = data["cves"], DARK
    total = len(cves)
    years = sorted({int(v["id"][4:8]) for v in cves})
    on_edb = sum(1 for v in cves if v["edb"])
    top_score = max(v["cvss"] for v in cves)
    sev = {s[0]: 0 for s in SEVERITY}
    for v in cves:
        sev[severity(v["cvss"])[0]] += 1
    classes = {}
    for v in cves:
        classes[v["class"]] = classes.get(v["class"], 0) + 1
    classes = sorted(classes.items(), key=lambda kv: (-kv[1], kv[0]))

    h = max(232, 74 + 28 * len(classes))
    label = (
        f"{total} CVEs published {years[0]}-{years[-1]}; "
        + ", ".join(f"{n} {k}" for k, n in sev.items() if n)
        + "; " + ", ".join(f"{n} {k}" for k, n in classes)
    )
    svg = Svg(W, h, label)
    svg.defs.append(GLOW.format(r=5, a=0.5))
    panel(svg)

    # Left: the headline number.
    svg.text(32, 108, str(total), 76, c["accent"], 700, attrs=' filter="url(#glow)"')
    svg.text(32 + tw(str(total), 76) + 12, 82, "CVEs", 22, c["head"], 700)
    svg.text(32 + tw(str(total), 76) + 12, 106, "published", 14, c["muted"])
    svg.text(32, 148, f"{years[0]} — {years[-1]}", 13, c["text"])
    svg.runs(32, 174, [(str(on_edb), c["accent"], 700), (" exploits on Exploit-DB", c["text"], 400)], 13)
    svg.runs(32, 200, [("max CVSS ", c["text"], 400), (f"{top_score}", severity(top_score)[2], 700)], 13)

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

    colours = {s[0]: s[2] for s in SEVERITY}
    bars(300, "severity · CVSS 3.x", list(sev.items()), colours.get, 84, 150)
    bars(598, "by class", classes, lambda _: c["accent"], 128, 110)
    svg.add(f'<line x1="276.5" y1="28" x2="276.5" y2="{h - 28}" stroke="{c["border"]}"/>')
    svg.add(f'<line x1="574.5" y1="28" x2="574.5" y2="{h - 28}" stroke="{c["border"]}"/>')
    return svg


def project_card(item):
    c, size, pad = DARK, 13, 22
    lines = textwrap.wrap(item["desc"], int((CARD_W - 2 * pad) / cw(size)))[:3]
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


def footer(data, c):
    p = data["profile"]
    svg = Svg(W, 56, f'{p["name"]} · hackpuntes.com')
    svg.add(f'<line x1="0" y1="10.5" x2="{W}" y2="10.5" stroke="{c["border"]}"/>')
    parts = [(p["name"], c["accent"], 700), ("  ·  ", c["muted"], 400), ("hackpuntes.com", c["text"], 400),
             ("  ·  ", c["muted"], 400), ("Toledo, Spain", c["text"], 400)]
    total = sum(tw(s, 13) for s, _, _ in parts)
    svg.runs((W - total) / 2, 40, parts, 13)
    return svg


# --- README blocks ---------------------------------------------------------

def nav_block(data):
    items = [
        f'<a href="{url}"><img src="assets/dist/nav-{slug(label)}.svg" height="34" alt="{label}"></a>'
        for label, url in data["profile"]["links"]
    ]
    return "<p align=\"center\">\n" + "\n".join(items) + "\n</p>"


def projects_block(data):
    cards = [
        f'<a href="{p["url"]}"><img src="assets/dist/project-{slug(p["name"])}.svg" width="49%" '
        f'alt="{attr(p["name"] + ": " + p["desc"])}"></a>'
        for p in data.get("projects", [])
    ]
    return "<p align=\"center\">\n" + "\n".join(cards) + "\n</p>"


def cve_table(data):
    lines = [
        "| CVE | Target | Vulnerability | CVSS 3.x | Exploit-DB |",
        "|:--|:--|:--|:--|:--:|",
    ]
    for v in data["cves"]:
        name, _, colour, emoji = severity(v["cvss"])
        edb = f'[{v["edb"]}](https://www.exploit-db.com/exploits/{v["edb"]})' if v["edb"] else "—"
        target = v["name"].replace("<=", "≤").replace("|", "\\|")
        lines.append(
            f'| [{v["id"]}](https://www.cve.org/CVERecord?id={v["id"]}) | {target} | '
            f'[{v["vuln"]}]({v["link"]}) | {emoji} `{v["cvss"]}` {name} | {edb} |'
        )
    return "\n".join(lines)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def replace_block(text, tag, content):
    pattern = re.compile(rf"(<!-- {tag}:START -->).*?(<!-- {tag}:END -->)", re.S)
    if not pattern.search(text):
        sys.exit(f"README.md is missing the {tag}:START/END markers")
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", text)


# --- main ------------------------------------------------------------------

def outputs(data):
    files = {
        "header.svg": header(data),
        "whoami.svg": whoami(data),
        "cves.svg": cve_card(data),
        "footer-dark.svg": footer(data, DARK),
        "footer-light.svg": footer(data, LIGHT),
    }
    for key, (icon, text, note) in data["titles"].items():
        note = fill_placeholders(note, data)
        files[f"title-{key}-dark.svg"] = title(icon, text, note, DARK)
        files[f"title-{key}-light.svg"] = title(icon, text, note, LIGHT)
    for label, _ in data["profile"]["links"]:
        files[f"nav-{slug(label)}.svg"] = pill(label)
    for item in data.get("projects", []):
        files[f"project-{slug(item['name'])}.svg"] = project_card(item)
    return {name: svg.render() for name, svg in files.items()}


def main():
    check = "--check" in sys.argv[1:]
    data = tomllib.loads((ROOT / "profile.toml").read_text(encoding="utf-8"))
    data["titles"] = {k: tuple(v) for k, v in data.get("titles", {}).items()}

    wanted = {DIST / name: body for name, body in outputs(data).items()}
    readme = README.read_text(encoding="utf-8")
    readme_new = replace_block(readme, "NAV", nav_block(data))
    readme_new = replace_block(readme_new, "PROJECTS", projects_block(data))
    readme_new = replace_block(readme_new, "CVE-TABLE", cve_table(data))
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
