"""Assemble assets/terminal-card.svg — burnt-orange terminal card with an ASCII render of gh-r.png.

Three things had to be solved before the portrait actually read as a face:

1. Background. The source sits on busy graffiti that turned the grid into noise, so the
   subject is segmented out first (OpenCV GrabCut seeded with a border/head/torso trimap)
   and luminance is measured *inside the mask only*.
2. Framing and resolution. Sampling the whole body left the head at ~30x21 cells — too
   coarse for eyes, lips or ears. The grid is now a head-and-shoulders crop anchored on the
   detected face box, at half the type size, which puts ~68x42 cells on the face itself.
3. Polarity and ink. At GitHub's render scale a cell is ~4x6 CSS px, where a glyph covers at
   most ~10% of its box with a 1.2em line, and coverage varies tenfold across a dark-to-light
   ramp. Density therefore cannot carry tone — it fights it, and the dark hair and hoodie
   either vanish or invert into a bright mass. So the line box is tightened to 0.95em and the
   ramp is restricted to glyphs of near-constant ink (~0.23-0.35 coverage): the character
   grid stays solid and colour carries the whole tonal range.

Build deps (dev-only, not shipped): numpy, opencv-python, scipy.
Run: python build_terminal_card.py
"""
import io
import json
import random
import re

import cv2
import numpy as np
from scipy import ndimage

SRC = "gh-r.png"
OUT = "assets/terminal-card.svg"
STATS = "assets/stats.json"   # written by build_stats.py; absent is fine, the card falls back

# ---- ASCII grid geometry ----
# Panel is x 40..412 (372 wide), y 104..620 (516 tall); art stops above the 592px HUD strip.
# A 70x52 grid put only ~41x26 cells on the face, which is too coarse for eyes, lips and
# ears: each feature landed on 2-3 cells. Halving the type size to 6.2 doubles the grid to
# 98x72 and, with a tighter crop, puts ~68x42 cells on the face — enough for eyelids, the
# lip line, ear cartilage and hair-strand banding. Glyph shape is unreadable at 4.6 CSS px
# anyway, so nothing is lost: the mosaic is what reads, and colour carries the tone.
COLS = 98
ROWS = 72
FONT_SIZE = 6.2
CHAR_W = FONT_SIZE * 0.6           # 3.72
LINE_H = FONT_SIZE * 0.95          # 5.89 — tight line box keeps glyph ink dense
ART_WIDTH = COLS * CHAR_W          # 364.56 -> locked via textLength, font-proof
X0 = 40 + (372 - ART_WIDTH) / 2    # 43.72, centered in the panel
Y0 = 154.0                         # first baseline (last lands at 572.2, clear of the HUD)

# ---- animation timeline (seconds) ----
# One clock for the whole card, so the boot screen, the raster beam and the info rows can
# never drift apart. Everything downstream is derived from BOOT_END.
BOOT_LINES = 6         # how many lines the boot screen types
BOOT_START = 0.20      # first boot line starts typing
BOOT_STEP = 0.34       # gap between boot lines
BOOT_TYPE = 0.42       # how long one line takes to type
BOOT_HOLD = 0.30       # beat after the last line before the screen clears
BOOT_FADE = 0.45       # boot screen fade-out
BOOT_END = BOOT_START + (BOOT_LINES - 1) * BOOT_STEP + BOOT_TYPE + BOOT_HOLD
ART_START = BOOT_END + BOOT_FADE * 0.6      # beam starts while the boot screen is fading
# The raster pass stays the quick beat of the sequence — everything around it is slower,
# so ~1s down the panel still reads as a fast sweep rather than a crawl.
ART_ROW_STEP = 0.011                        # per-row delay: 72 rows sweep in ~0.78s
ART_SWEEP = ART_ROW_STEP * (ROWS - 1) + 0.18
INFO_START = ART_START + 0.15
INFO_ROW_STEP = 0.055
# The idle sweep only takes over once the raster reveal has finished, so there is exactly
# one fast pass down the panel and then the slow ambient one.
IDLE_SCAN_START = ART_START + ART_SWEEP

# ---- text scramble (SYSTEM.INFO side) ----
# No JS is possible in a README SVG, so the scramble is baked: every value is emitted as a
# few pre-rendered garbage frames stacked in place, each visible for one slot. The RNG is
# seeded so a nightly rebuild with unchanged stats produces a byte-identical file and the
# workflow does not commit churn.
SCR_FRAMES = 4
SCR_FRAME = 0.075      # seconds per garbage frame
SCR_SEED = 0xE85D04
SCR_CHARS = "!<>-_\\/[]{}=+*^?#%$&@01xX"

# ---- subject segmentation (GrabCut trimap seeds, as fractions of image size) ----
SEG_ITERS = 10
BORDER_BG = 0.045      # side margins are pure background
TOP_BG = 0.05          # top strip is pure background
CORNER_BG = (0.30, 0.18)   # top-left/right corner blocks (h, w) — graffiti lives there
HEAD_FG = (0.50, 0.30, 0.15, 0.20)   # ellipse: cx, cy, rx, ry
TORSO_FG = (0.30, 0.62, 0.70, 0.99)  # rect: x0, y0, x1, y1
MIN_COVERAGE = 0.45    # a grid cell counts as subject once 45% of it is inside the mask
DENOISE_KERNEL = 3     # median blur radius — kills the paint splatter on the hoodie

# ---- bust framing (skin-tone face box drives the crop) ----
SKIN_Y = 60            # YCrCb gates; used only to locate the head
SKIN_CR = (133, 178)
SKIN_CB = (74, 130)
FRAME_WMUL = 1.78      # crop width as a multiple of the face-box width
FRAME_TOP_PAD = 0.12   # headroom above the hair, as a fraction of face height

# ---- tone curve ----
# Every term here was picked against a 3x preview of the face at GitHub's real render
# scale; the pipeline order is stretch -> unsharp -> gamma -> S-curve -> knee -> lift.
STRETCH = (2.0, 98.0)  # percentile clip over subject cells only
SHARPEN = (0.55, 1.3)  # grid-space unsharp (amount, sigma) — separates brow/eye/lip/ear
GAMMA = 0.70           # <1 lifts skin midtones out of the dark mass
SMOOTHSTEP = 0.25      # S-curve weight; a strong S crushed the hair into one flat tier
HI_KNEE = 0.80         # soft-compress above this, so cream stays a specular and the
                       # forehead keeps its modelling instead of clipping to one tier
SHADOW_LIFT = 0.10     # floor under the darks — hair and hoodie keep internal structure
FADE_BOTTOM = 0.38     # falloff below the chin so the lit hood stops out-shouting the face
COLOR_TIERS = 20       # colour quantisation — smooth enough, keeps tspan runs cheap

# Near-constant ink coverage (0.23-0.35 of the cell at render scale), ordered dim -> dense.
# Tone is carried by colour, so these vary texture without fighting the tonal signal.
GLYPHS = "0OHAGRD8%BWNQ&@"

# Continuous burnt-orange ramp: deep ember shadow -> hot orange -> warm cream highlight.
HUE_STOPS = [
    (0.00, (0x1a, 0x0b, 0x03)),   # hoodie core, hair shadow
    (0.20, (0x5e, 0x24, 0x05)),   # hoodie body, beard mass
    (0.40, (0xb0, 0x42, 0x08)),   # jaw, neck, shadowed cheek
    (0.58, (0xe8, 0x5d, 0x04)),   # brand orange — mid skin
    (0.76, (0xff, 0x8c, 0x1f)),   # lit skin
    (0.90, (0xff, 0xb5, 0x62)),   # brow ridge, nose, cheekbone
    (1.00, (0xff, 0xdd, 0xac)),   # specular highlights
]


def esc(s: str) -> str:
    # built via concatenation so the literals survive any entity decoding
    return s.replace("&", "&" + "amp;").replace("<", "&" + "lt;").replace(">", "&" + "gt;")


def hue(t: float) -> str:
    """Interpolate HUE_STOPS at t in [0, 1] and return a hex colour."""
    for i in range(len(HUE_STOPS) - 1):
        a, ca = HUE_STOPS[i]
        b, cb = HUE_STOPS[i + 1]
        if t <= b or i == len(HUE_STOPS) - 2:
            f = 0.0 if b == a else (min(max(t, a), b) - a) / (b - a)
            return "#%02x%02x%02x" % tuple(int(round(ca[k] + f * (cb[k] - ca[k]))) for k in range(3))
    raise AssertionError("unreachable")


def subject_mask(bgr):
    """GrabCut the person out of the graffiti background; return a clean float mask."""
    h, w = bgr.shape[:2]
    mask = np.full((h, w), cv2.GC_PR_BGD, np.uint8)

    m = int(BORDER_BG * w)
    mask[: int(TOP_BG * h), :] = cv2.GC_BGD
    mask[:, :m] = cv2.GC_BGD
    mask[:, w - m :] = cv2.GC_BGD
    ch, cw = CORNER_BG
    mask[: int(ch * h), : int(cw * w)] = cv2.GC_BGD
    mask[: int(ch * h), w - int(cw * w) :] = cv2.GC_BGD

    cx, cy, rx, ry = HEAD_FG
    cv2.ellipse(mask, (int(cx * w), int(cy * h)), (int(rx * w), int(ry * h)),
                0, 0, 360, cv2.GC_FGD, -1)
    tx0, ty0, tx1, ty1 = TORSO_FG
    cv2.rectangle(mask, (int(tx0 * w), int(ty0 * h)), (int(tx1 * w), int(ty1 * h)),
                  cv2.GC_FGD, -1)

    cv2.grabCut(bgr, mask, None, np.zeros((1, 65), np.float64),
                np.zeros((1, 65), np.float64), SEG_ITERS, cv2.GC_INIT_WITH_MASK)

    fg = np.isin(mask, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k, iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k, iterations=1)
    lbl, n = ndimage.label(fg)                      # keep the largest blob only
    if n:
        sizes = ndimage.sum(fg, lbl, range(1, n + 1))
        fg = (lbl == int(np.argmax(sizes)) + 1).astype(np.uint8)
    return ndimage.binary_fill_holes(fg).astype(np.float32)


def face_box(bgr, fg):
    """Locate the head by skin tone. cv2 5.x ships no Haar cascades, and a YCrCb gate is
    enough here: the convex hull of the largest skin blob bounds the face."""
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = ycc[:, :, 0], ycc[:, :, 1], ycc[:, :, 2]
    skin = ((y > SKIN_Y) & (cr >= SKIN_CR[0]) & (cr <= SKIN_CR[1])
            & (cb >= SKIN_CB[0]) & (cb <= SKIN_CB[1]) & (fg > 0.5)).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, k, iterations=1)
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, k, iterations=2)
    cnts, _ = cv2.findContours(skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise SystemExit("no skin blob found — cannot frame the bust")
    return cv2.boundingRect(cv2.convexHull(max(cnts, key=cv2.contourArea)))


def bust_crop(fg, box):
    """Square-ish crop centred on the face, opening just above the hair line."""
    h, w = fg.shape
    fx, fy, fw, fh = box
    cw = fw * FRAME_WMUL
    ch = cw * (ROWS * LINE_H) / (COLS * CHAR_W)     # isotropic in rendered space
    head_top = int(np.nonzero(fg[:, fx : fx + fw].sum(1) > 2)[0][0])
    x0 = max(0.0, min(fx + fw / 2 - cw / 2, w - cw))
    y0 = max(0.0, min(head_top - FRAME_TOP_PAD * fh, h - ch))
    return (int(round(x0)), int(round(y0)),
            int(round(min(x0 + cw, w))), int(round(min(y0 + ch, h))))


def build_ascii_rows():
    bgr = cv2.imread(SRC, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"cannot read {SRC}")
    fg = subject_mask(bgr)
    box = face_box(bgr, fg)
    x0, y0, x1, y1 = bust_crop(fg, box)
    # median alone smears the eyelids and hair strands; a bilateral pass after a light
    # median keeps those edges while still flattening the paint splatter on the hoodie
    lum = cv2.medianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[:, :, 0], DENOISE_KERNEL)
    lum = cv2.bilateralFilter(lum, 7, 45, 7)

    sub_l = lum[y0:y1, x0:x1].astype(np.float32)
    sub_m = fg[y0:y1, x0:x1]

    # mask-weighted box downsample: cell value = mean luminance of its subject pixels
    num = cv2.resize(sub_l * sub_m, (COLS, ROWS), interpolation=cv2.INTER_AREA)
    cov = cv2.resize(sub_m, (COLS, ROWS), interpolation=cv2.INTER_AREA)
    val = np.where(cov > 1e-3, num / np.maximum(cov, 1e-3), 0.0)
    inside = cov >= MIN_COVERAGE

    lo, hi = np.percentile(val[inside], STRETCH[0]), np.percentile(val[inside], STRETCH[1])
    v = np.clip((val - lo) / max(float(hi - lo), 1e-3), 0.0, 1.0)
    amt, sig = SHARPEN
    v = np.clip(v + amt * (v - cv2.GaussianBlur(v, (0, 0), sig)), 0.0, 1.0)
    v = v ** GAMMA
    v = (1 - SMOOTHSTEP) * v + SMOOTHSTEP * (v * v * (3 - 2 * v))
    k = HI_KNEE
    v = np.where(v > k, k + (v - k) * (1.0 - k) / max(1.0 - k, 1e-3) * 0.75, v)
    v = SHADOW_LIFT + (1.0 - SHADOW_LIFT) * v

    # the hood and chest are lit as brightly as the face; fade them so the head leads
    chin_row = (box[1] + box[3] - y0) / (y1 - y0) * ROWS
    rr = np.arange(ROWS, dtype=np.float32).reshape(-1, 1)
    drop = np.clip((rr - chin_row) / max(ROWS - 1 - chin_row, 1e-3), 0.0, 1.0)
    v = np.clip(v * (1.0 - FADE_BOTTOM * drop ** 1.4), 0.0, 1.0)

    palette = [hue(i / (COLOR_TIERS - 1)) for i in range(COLOR_TIERS)]
    rows = []
    for r in range(ROWS):
        runs = []           # (colour | None for background, chars)
        line_chars = ""
        for c in range(COLS):
            if inside[r, c]:
                t = float(v[r, c])
                glyph = GLYPHS[min(int(t * len(GLYPHS)), len(GLYPHS) - 1)]
                colour = palette[min(int(t * COLOR_TIERS), COLOR_TIERS - 1)]
            else:
                glyph, colour = " ", None
            line_chars += glyph
            if runs and runs[-1][0] == colour:
                runs[-1] = (colour, runs[-1][1] + glyph)
            else:
                runs.append((colour, glyph))
        # background spaces stay in the string (xml:space="preserve" in the template),
        # so every row is exactly COLS cells wide and the grid cannot drift
        markup = "".join(
            f'<tspan fill="{col}">{esc(ch)}</tspan>' if col else esc(ch)
            for col, ch in runs
        )
        rows.append((markup, line_chars))
    print(f"face box {box}  bust crop {(x0, y0, x1, y1)}  "
          f"head ~{box[2] / (x1 - x0) * COLS:.0f}x{box[3] / (y1 - y0) * ROWS:.0f} cells")
    return rows


MONO = "ui-monospace,SFMono-Regular,Consolas,monospace"

# (y, key, value) — key "DIV" renders an orange section divider, "NOTE" renders a muted note
ROWS_DATA = [
    (112, "Subject:", "Naveed Sohail Gung"),
    (136, "Role:", "Full-Stack Engineer · Security Builder"),
    (160, "Origin:", "Beirut, Lebanon"),
    (184, "Education:", "B.S. MIS (in progress)"),
    (208, "Status:", "Building · Shipping · Learning"),
    (232, "ToolChain:", "VS Code, Git, Docker, Postman"),
    (268, "DIV", "— STACK ——————————————————"),
    (294, "Core.Lang:", "JS, TS, Python, C#, Java, Go, Rust"),
    (318, "Core.Frontend:", "React, Bootstrap, Three.js, Tailwind"),
    (342, "Core.Backend:", "Node.js, Express, GraphQL, REST"),
    (366, "Core.Mobile:", "React Native, Flutter, Firebase"),
    (390, "Core.Data:", "MongoDB, PostgreSQL, SQL, Redis"),
    (414, "Core.DevOps:", "Docker, AWS, K8s, GitHub Actions"),
    (450, "DIV", "— CONTACT —————————————————"),
    (476, "Grid.Mail:", "naveedsohailg@gmail.com"),
    (500, "Grid.LinkedIn:", "naveed-sohail-gung"),
    (524, "Grid.Github:", "naveed-gung"),
    (548, "Grid.Portfolio:", "naveed-gung.dev"),
    (584, "DIV", "— LIVE STATS ——————————————"),
]


def load_stats():
    """Numbers baked by build_stats.py. Missing or malformed file -> static fallback,
    so a local build (or a rate-limited CI run) still produces a complete card."""
    try:
        with io.open(STATS, encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict) or "repos" not in st:
            raise ValueError("unexpected shape")
        return st
    except (OSError, ValueError) as e:
        print(f"no usable {STATS} ({e}) — card falls back to static copy")
        return None


def stats_rows(st):
    """The LIVE STATS block: two dense lines, because the panel only has ~50px left
    under the divider before the footer rule at y=656."""
    if not st:
        return [(608, "NOTE", "> Stats bake nightly — .github/workflows/refresh-card.yml")]

    def n(v):
        return f'<tspan fill="#ff8c42">{v}</tspan>'

    line1 = (f'Repos {n(st["repos"])}  ·  Stars {n(st["stars"])}  ·  '
             f'Followers {n(st["followers"])}  ·  Age {n(st["account_age"])}')
    langs = "  ·  ".join(esc(l["name"]) + " " + n(str(l["pct"]) + "%")
                         for l in st.get("languages", []))
    return [(608, "MARKUP", line1), (630, "MARKUP", f'Top  {langs}' if langs else "Top  —")]


RNG = random.Random(SCR_SEED)


def visible_len(markup):
    """Rendered length of a value, ignoring tspan wrappers and entity expansion."""
    return len(re.sub(r"&[a-z]+;", "x", re.sub(r"<[^>]+>", "", markup)))


def scramble(x, y, fs, fill, markup, n_chars, delay):
    """Stack SCR_FRAMES garbage frames in place, then the real text.

    Each frame owns one time slot and hides itself when the slot ends, so at most one is
    ever painted. The garbage has to match the final visible length or the line visibly
    changes width as it settles."""
    n = max(1, min(n_chars, 64))
    parts = []
    for k in range(SCR_FRAMES):
        junk = esc("".join(RNG.choice(SCR_CHARS) for _ in range(n)))
        parts.append(
            f'<text class="scr" style="animation-delay:{delay + k * SCR_FRAME:.2f}s"'
            f' xml:space="preserve" x="{x}" y="{y}" font-family="{MONO}" font-size="{fs}"'
            f' fill="#a8641f">{junk}</text>'
        )
    parts.append(
        f'<text class="scv" style="animation-delay:{delay + SCR_FRAMES * SCR_FRAME:.2f}s"'
        f' xml:space="preserve" x="{x}" y="{y}" font-family="{MONO}" font-size="{fs}"'
        f' fill="{fill}">{markup}</text>'
    )
    return "".join(parts)


def build_info_rows(st):
    """SYSTEM.INFO does not slide in — that reveal belongs to the ASCII panel. Here each
    line resolves out of character noise instead, one after the other."""
    out = []
    delay = INFO_START
    for y, key, value in ROWS_DATA + stats_rows(st):
        d = f"{delay:.2f}"
        if key == "DIV":
            out.append(
                f'<g class="rw" style="animation-delay:{d}s">'
                f'<text x="484" y="{y}" font-family="{MONO}" font-size="12" letter-spacing="2"'
                f' fill="#e85d04">{value}</text></g>'
            )
        elif key in ("NOTE", "MARKUP"):
            # MARKUP is pre-built SVG (stats_rows colours the numbers) — do not escape it
            markup = esc(value) if key == "NOTE" else value
            out.append(scramble(484, y, 13, "#8a7f74", markup, visible_len(markup), delay))
        else:
            out.append(
                f'<g class="rw" style="animation-delay:{d}s">'
                f'<text x="484" y="{y}" font-family="{MONO}" font-size="14" fill="#e85d04">{key}</text>'
                f'<text x="640" y="{y}" font-family="{MONO}" font-size="13"'
                f' fill="#3a2a1c">{"." * 28}</text></g>'
                + scramble(830, y, 13, "#e8e2da", esc(value), len(value), delay)
            )
        delay += INFO_ROW_STEP
    return "\n".join(out)


def build_ascii_block(rows):
    parts = ['<g class="asciiLive" clip-path="url(#feedClip)">']
    parts.append(f'<g font-family="{MONO}" font-size="{FONT_SIZE}" filter="url(#bloom)">')
    for r, (markup, plain) in enumerate(rows):
        if not plain.strip():
            continue
        d = f"{ART_START + r * ART_ROW_STEP:.2f}"
        parts.append(
            f'<text xml:space="preserve" x="{X0:.2f}" y="{Y0 + r * LINE_H:.1f}" class="ar"'
            f' style="animation-delay:{d}s" textLength="{ART_WIDTH:.2f}"'
            f' lengthAdjust="spacing">{markup}</text>'
        )
    parts.append("</g>")
    # CRT: scanlines are locked to the row pitch on purpose — any other period beats
    # against the glyph grid and reads as moire rather than as a tube.
    parts.append('<rect x="40" y="104" width="372" height="516" fill="url(#scanlines)"/>')
    parts.append('<rect x="40" y="104" width="372" height="516" fill="url(#vig)"/>')
    # the raster beam: one fast pass down the panel, in step with the row-by-row reveal
    parts.append('<rect class="scanhead" x="40" y="104" width="372" height="18" fill="url(#beamGrad)"/>')
    # ambient sweep, only after the reveal has finished
    parts.append('<rect class="scan" x="40" y="104" width="372" height="46" fill="url(#scanGrad)"/>')
    # bottom HUD strip
    parts.append('<rect x="40" y="592" width="372" height="28" fill="#0d0d0d" opacity="0.78"/>')
    parts.append(f'<text x="48" y="610" font-family="{MONO}" font-size="11" fill="#e8e2da">SUBJECT: NAVEED</text>')
    parts.append(f'<text x="404" y="610" text-anchor="end" font-family="{MONO}" font-size="11" fill="#ff8c42">ID: 0xE85D04</text>')
    parts.append("</g>")
    return "\n".join(parts)


# (line, status, status colour) — {GRID} is filled in by build_boot
BOOT_LOG = [
    ("> devos boot --profile naveed-gung", "", ""),
    ("> mount /dev/ascii", "OK", "#27c93f"),
    ("> load palette 0xE85D04", "OK", "#27c93f"),
    ("> decode gh-r.png -> {GRID}", "OK", "#27c93f"),
    ("> link github://naveed-gung/stats", "OK", "#27c93f"),
    ("> render profile.card", "READY", "#e85d04"),
]
BOOT_X = 60
BOOT_Y = 300
BOOT_LINE_H = 24
BOOT_FS = 14
BOOT_COL = 46          # column the [STATUS] tag is padded out to


def build_boot():
    """Six typed log lines over a full-card blackout, then the group fades away.

    Typing is a clipPath rect scaled on the x axis rather than `clip-path: inset()` on the
    text: scaleX with transform-box:fill-box is the same technique .barfill already uses,
    and its worst-case failure mode is a wrong-looking reveal that still ends fully
    visible, whereas an unsupported inset() would leave the line permanently clipped."""
    assert len(BOOT_LOG) == BOOT_LINES, "BOOT_LINES must match BOOT_LOG"
    cw = BOOT_FS * 0.6
    clips, lines = [], []
    for i, (txt, status, col) in enumerate(BOOT_LOG):
        txt = txt.replace("{GRID}", f"{COLS}x{ROWS} grid")
        tag = f"[ {status} ]" if status else ""
        body = txt + (" " + "." * max(3, BOOT_COL - len(txt) - len(tag) - 2) + " " if tag else "")
        y = BOOT_Y + i * BOOT_LINE_H
        w = (len(body) + len(tag)) * cw + 4
        d = BOOT_START + i * BOOT_STEP
        clips.append(
            f'<clipPath id="bc{i}"><rect class="bl" style="animation-delay:{d:.2f}s"'
            f' x="{BOOT_X}" y="{y - BOOT_FS}" width="{w:.1f}" height="{BOOT_FS + 6}"/></clipPath>'
        )
        lines.append(
            f'<text clip-path="url(#bc{i})" xml:space="preserve" x="{BOOT_X}" y="{y}"'
            f' font-family="{MONO}" font-size="{BOOT_FS}" fill="#b9ada0">'
            f'<tspan fill="#e85d04">{esc(body[:1])}</tspan>{esc(body[1:])}'
            + (f'<tspan fill="{col}">{esc(tag)}</tspan>' if tag else "")
            + "</text>"
        )
    cursor_y = BOOT_Y + len(BOOT_LOG) * BOOT_LINE_H
    header = (
        f'<text x="{BOOT_X}" y="{BOOT_Y - 46}" font-family="{MONO}" font-size="12"'
        f' letter-spacing="3" fill="#e85d04">DEVOS 5.2 // PROFILE LOADER</text>'
        f'<line x1="{BOOT_X}" y1="{BOOT_Y - 36}" x2="{BOOT_X + 430}" y2="{BOOT_Y - 36}"'
        f' stroke="#2a1c10" stroke-width="1"/>'
    )
    return (
        f'<g class="bootwrap" style="animation-delay:{BOOT_END:.2f}s">'
        '<rect x="1" y="1" width="1178" height="698" rx="14" fill="#0d0d0d"/>'
        + header + "".join(clips) + "\n" + "\n".join(lines)
        + f'<text x="{BOOT_X}" y="{cursor_y}" font-family="{MONO}" font-size="{BOOT_FS}"'
          f' fill="#e85d04"><tspan class="cursor">&#9612;</tspan></text>'
        "</g>"
    )


TEMPLATE = """<svg viewBox="0 0 1180 700" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="borderGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#e85d04" stop-opacity="0.65"/>
      <stop offset="100%" stop-color="#7a3200" stop-opacity="0.9"/>
    </linearGradient>
    <linearGradient id="scanGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ff8c42" stop-opacity="0"/>
      <stop offset="50%" stop-color="#ff8c42" stop-opacity="0.85"/>
      <stop offset="100%" stop-color="#ff8c42" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="beamGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ff8c42" stop-opacity="0"/>
      <stop offset="60%" stop-color="#ffb27a" stop-opacity="0.18"/>
      <stop offset="90%" stop-color="#fff3e0" stop-opacity="0.80"/>
      <stop offset="100%" stop-color="#ff8c42" stop-opacity="0"/>
    </linearGradient>
    <pattern id="scanlines" width="4" height="{LINE_H}" patternUnits="userSpaceOnUse"
             patternTransform="translate(0,{SCAN_PHASE})">
      <rect width="4" height="{SCAN_BAND}" fill="#000" opacity="0.30"/>
    </pattern>
    <radialGradient id="vig" cx="50%" cy="44%" r="74%">
      <stop offset="52%" stop-color="#000" stop-opacity="0"/>
      <stop offset="100%" stop-color="#000" stop-opacity="0.62"/>
    </radialGradient>
    <filter id="bloom" x="-4%" y="-3%" width="108%" height="106%" color-interpolation-filters="sRGB">
      <feGaussianBlur stdDeviation="1.5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="feedClip"><rect x="40" y="104" width="372" height="516" rx="6"/></clipPath>
  </defs>

  <style>
    /* --- boot screen ------------------------------------------------------------ */
    .bootwrap { animation: bootOut {BOOT_FADE}s ease-in forwards; }
    @keyframes bootOut { to { opacity: 0; visibility: hidden; } }
    .bl { transform-box: fill-box; transform-origin: left center; transform: scaleX(0);
          animation: type {BOOT_TYPE}s steps(26, end) forwards; }
    @keyframes type { to { transform: scaleX(1); } }

    /* --- ASCII panel: raster reveal ---------------------------------------------- */
    .ar { opacity: 0; animation: arIn .30s ease-out forwards; }
    @keyframes arIn { to { opacity: 1; } }
    .scanhead { opacity: 0; animation: beam {ART_SWEEP}s linear {ART_START}s forwards; }
    @keyframes beam {
      0% { transform: translateY(34px); opacity: 0; }
      10% { opacity: 1; }
      88% { opacity: 1; }
      100% { transform: translateY(472px); opacity: 0; }
    }
    /* ambient sweep takes over only once the raster pass is done */
    .scan { opacity: 0; animation: sweep 4.2s linear {IDLE_SCAN_START}s infinite; }
    @keyframes sweep {
      0% { transform: translateY(-60px); opacity: 0; }
      6% { opacity: .5; }
      45% { opacity: .5; }
      55% { transform: translateY(576px); opacity: 0; }
      100% { transform: translateY(576px); opacity: 0; }
    }
    .asciiLive { animation: livePulse 5s ease-in-out {IDLE_SCAN_START}s infinite; }
    @keyframes livePulse { 0%,100% { opacity: 1; } 50% { opacity: .94; } }

    /* --- SYSTEM.INFO: text scramble ---------------------------------------------- */
    .rw { opacity: 0; animation: fin .14s linear forwards; }
    .scv { opacity: 0; animation: fin .22s ease-out forwards; }
    @keyframes fin { to { opacity: 1; } }
    .scr { opacity: 0; animation: scr {SCR_FRAME}s linear forwards; }
    @keyframes scr { 0%, 96% { opacity: .85; } 100% { opacity: 0; } }

    /* --- chrome ------------------------------------------------------------------ */
    .cursor { animation: blink 1s steps(2, jump-none) infinite; }
    @keyframes blink { 50% { opacity: 0; } }
    .rec { animation: blink 1.4s steps(2, jump-none) infinite; }
    .glow { animation: pulse 3s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { stroke-opacity: .35; } 50% { stroke-opacity: .9; } }
    .label { opacity: 0; animation: fin .4s ease-out {ART_START}s forwards; }
    .hud { opacity: 0; animation: hudIn .8s ease-out {IDLE_SCAN_START}s forwards; }
    @keyframes hudIn { to { opacity: .95; } }
    .barfill { transform-box: fill-box; transform-origin: left center; animation: load 3.4s ease-in-out infinite; }
    @keyframes load { 0% { transform: scaleX(.06); } 60% { transform: scaleX(1); } 82% { transform: scaleX(1); opacity: 1; } 100% { transform: scaleX(1); opacity: 0; } }

    /* Vestibular safety: no boot screen, no beam, no flicker — just the finished card. */
    @media (prefers-reduced-motion: reduce) {
      .ar, .rw, .scv, .label, .hud { opacity: 1 !important; animation: none !important; }
      .hud { opacity: .95 !important; }
      .bootwrap, .scr, .scan, .scanhead { display: none !important; }
      .cursor, .rec, .glow, .asciiLive { animation: none !important; }
      .barfill { animation: none !important; transform: scaleX(1) !important; }
    }
  </style>

  <rect x="1" y="1" width="1178" height="698" rx="14" fill="#0d0d0d" stroke="url(#borderGrad)" stroke-width="1.5" class="glow"/>

  <circle cx="28" cy="24" r="6" fill="#ff5f56"/>
  <circle cx="50" cy="24" r="6" fill="#ffbd2e"/>
  <circle cx="72" cy="24" r="6" fill="#27c93f"/>
  <text x="100" y="29" font-family="{MONO}" font-size="14" fill="#8a7f74">naveed-gung / README.md</text>
  <text x="1140" y="29" text-anchor="end" font-family="{MONO}" font-size="13" fill="#6e6257">naveed@devos ~ % ./profile.sh --live<tspan class="cursor">&#9612;</tspan></text>
  <line x1="0" y1="46" x2="1180" y2="46" stroke="#2a1c10" stroke-width="1"/>
  <line x1="452" y1="46" x2="452" y2="699" stroke="#2a1c10" stroke-width="1"/>

  <text x="40" y="78" class="label" font-family="{MONO}" font-size="13" letter-spacing="2" fill="#e85d04">ASCII.RENDER</text>
  <line x1="40" y1="88" x2="412" y2="88" stroke="#2a1c10" stroke-width="1"/>

{ASCII_BLOCK}

  <g class="hud">
    <text x="48" y="121" font-family="{MONO}" font-size="11" fill="#ffb27a" opacity="0.9">RENDER: ASCII // LIVE</text>
    <text x="48" y="136" font-family="{MONO}" font-size="10" fill="#c9a27a" opacity="0.65">{GRID} CHARS // 0xE85D04</text>
    <circle cx="386" cy="117" r="4" fill="#ff4d1a" class="rec"/>
    <text x="374" y="121" text-anchor="end" font-family="{MONO}" font-size="11" fill="#ffb27a">REC</text>
    <g fill="none" stroke="#ff8c42" stroke-width="1.5">
      <path d="M40 122 V108 Q40 104 44 104 H58"/>
      <path d="M394 104 H408 Q412 104 412 108 V122"/>
      <path d="M412 602 V616 Q412 620 408 620 H394"/>
      <path d="M58 620 H44 Q40 620 40 616 V602"/>
    </g>
    <g stroke="#ff8c42" stroke-width="1.5" opacity="0.85">
      <line x1="226" y1="104" x2="226" y2="112"/>
      <line x1="226" y1="620" x2="226" y2="612"/>
      <line x1="40" y1="362" x2="48" y2="362"/>
      <line x1="412" y1="362" x2="404" y2="362"/>
    </g>
  </g>

  <text x="484" y="78" class="label" font-family="{MONO}" font-size="13" letter-spacing="2" fill="#e85d04">SYSTEM.INFO</text>
  <line x1="484" y1="88" x2="1140" y2="88" stroke="#2a1c10" stroke-width="1"/>
{INFO_ROWS}

  <line x1="0" y1="656" x2="1180" y2="656" stroke="#2a1c10" stroke-width="1"/>
  <circle cx="40" cy="677" r="4" fill="#ff8c42" class="rec"/>
  <text x="52" y="681" font-family="{MONO}" font-size="12" fill="#e8e2da">SYS.ONLINE</text>
  <text x="170" y="681" font-family="{MONO}" font-size="12" fill="#6e6257">SYNC.GITHUB</text>
  <rect x="268" y="671" width="200" height="10" rx="5" fill="#1f150c" stroke="#3a2417" stroke-width="1"/>
  <rect x="270" y="673" width="196" height="6" rx="3" fill="#e85d04" class="barfill"/>
  <text x="490" y="681" font-family="{MONO}" font-size="12" fill="#6e6257">{FOOT_SYNC}</text>
  <text x="1140" y="681" text-anchor="end" font-family="{MONO}" font-size="12" fill="#ff8c42">BUILD: PASSING &#10003;</text>

{BOOT}
</svg>
"""


def main():
    rows = build_ascii_rows()
    st = load_stats()

    # terminal preview so the mapping can be verified at a glance
    print("--- ASCII preview (terminal) ---")
    for _, line in rows:
        print(line)
    print("--- end preview ---\n")

    # scanline period is locked to the row pitch; phase it so the dark band sits between
    # baselines rather than straight through the glyph bodies
    phase = (Y0 - FONT_SIZE * 0.72) % LINE_H

    svg = (
        TEMPLATE
        .replace("{ASCII_BLOCK}", build_ascii_block(rows))
        .replace("{INFO_ROWS}", build_info_rows(st))
        .replace("{BOOT}", build_boot())
        .replace("{MONO}", MONO)
        .replace("{GRID}", f"{COLS}x{ROWS}")
        .replace("{LINE_H}", f"{LINE_H:.2f}")
        .replace("{SCAN_PHASE}", f"{phase:.2f}")
        .replace("{SCAN_BAND}", f"{LINE_H * 0.34:.2f}")
        .replace("{BOOT_TYPE}", f"{BOOT_TYPE:.2f}")
        .replace("{BOOT_FADE}", f"{BOOT_FADE:.2f}")
        .replace("{ART_START}", f"{ART_START:.2f}")
        .replace("{ART_SWEEP}", f"{ART_SWEEP:.2f}")
        .replace("{IDLE_SCAN_START}", f"{IDLE_SCAN_START:.2f}")
        .replace("{SCR_FRAME}", f"{SCR_FRAME:.3f}")
        .replace("{FOOT_SYNC}", f"SYNCED {esc(st['generated_at'])}"
                 if st and st.get("generated_at") else "UPTIME 99.98%")
    )
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {OUT} ({len(svg):,} chars, {len(svg)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
