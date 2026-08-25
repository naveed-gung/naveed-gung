"""Build assets/github-telemetry.svg — cyberpunk GitHub stats, streaks, and language matrix.

Reads assets/stats.json (written by build_stats.py) and outputs a self-contained,
animated 1200x420 SVG matching the theme of header.svg and terminal-card.svg.

Run: python build_telemetry_card.py
"""
import json
import os
import sys

STATS_FILE = "assets/stats.json"
OUT_FILE = "assets/github-telemetry.svg"

# Default fallback values if stats.json doesn't exist yet
DEFAULT_STATS = {
    "generated_at": "2026-08-25T20:50Z",
    "repos": 32,
    "stars": 34,
    "followers": 10,
    "account_age": "5y 5m",
    "last_push": "2026-08-25T20:42Z",
    "last_repo": "bazaar",
    "total_contributions": 480,
    "total_commits": 420,
    "total_prs": 28,
    "total_issues": 14,
    "current_streak": 14,
    "longest_streak": 34,
    "weekly_sparkline": [8, 14, 18, 12, 22, 28, 19, 25, 32, 30, 38, 45, 40, 48, 52, 49],
    "languages": [
        {"name": "TypeScript", "pct": 46.9, "color": "#3178c6"},
        {"name": "JavaScript", "pct": 14.0, "color": "#f7df1e"},
        {"name": "Dart", "pct": 10.2, "color": "#00B4AB"},
        {"name": "Python", "pct": 5.3, "color": "#3572A5"},
        {"name": "C#", "pct": 4.8, "color": "#178600"},
        {"name": "Rust / Go", "pct": 4.1, "color": "#dea584"},
    ],
}


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_STATS, **data}
        except Exception as e:
            print(f"Warning: Failed to load {STATS_FILE}: {e}, using defaults", file=sys.stderr)
    return DEFAULT_STATS


def generate_svg(stats):
    repos = stats.get("repos", 32)
    stars = stats.get("stars", 34)
    followers = stats.get("followers", 10)
    account_age = stats.get("account_age", "5y 5m")
    last_push = stats.get("last_push", "2026-08-25T20:42Z")
    last_repo = stats.get("last_repo", "bazaar")
    total_contribs = stats.get("total_contributions", 480)
    total_commits = stats.get("total_commits", 420)
    total_prs = stats.get("total_prs", 28)
    total_issues = stats.get("total_issues", 14)
    current_streak = stats.get("current_streak", 14)
    longest_streak = stats.get("longest_streak", 34)
    weekly_sparkline = stats.get("weekly_sparkline", [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85])
    languages = stats.get("languages", DEFAULT_STATS["languages"])

    # Build stacked language bar segments
    bar_width = 508.0
    bar_segments = []
    curr_x = 640.0
    for idx, lang in enumerate(languages):
        pct = max(lang.get("pct", 0), 1.0)
        seg_w = (pct / 100.0) * bar_width
        color = lang.get("color", "#e85d04")
        bar_segments.append(
            f'<rect x="{curr_x:.1f}" y="112" width="{seg_w:.1f}" height="14" fill="{color}" rx="0">'
            f'<title>{lang["name"]}: {lang["pct"]}%</title></rect>'
        )
        curr_x += seg_w

    stacked_bar_svg = "\n        ".join(bar_segments)

    # Build individual language rows (up to 6 languages in a 2-column layout)
    lang_rows = []
    top_langs = languages[:6]
    for idx, lang in enumerate(top_langs):
        col = idx % 2
        row = idx // 2
        lx = 640 + col * 264
        ly = 156 + row * 46
        color = lang.get("color", "#e85d04")
        name = lang.get("name", "Unknown")
        pct = lang.get("pct", 0)
        prog_w = min(150, max(12, int(pct * 1.5)))

        lang_rows.append(f"""
        <!-- Lang {name} -->
        <g class="fadeUp" style="animation-delay: {0.3 + idx * 0.08:.2f}s;">
          <circle cx="{lx + 6}" cy="{ly + 6}" r="5" fill="{color}" />
          <text x="{lx + 18}" y="{ly + 10}" class="langName">{name}</text>
          <text x="{lx + 240}" y="{ly + 10}" text-anchor="end" class="langPct">{pct}%</text>
          <!-- Track -->
          <rect x="{lx + 18}" y="{ly + 16}" width="222" height="5" rx="2.5" fill="#1c2128" />
          <!-- Fill -->
          <rect x="{lx + 18}" y="{ly + 16}" width="{prog_w}" height="5" rx="2.5" fill="{color}" />
        </g>
        """)

    languages_svg = "\n".join(lang_rows)

    # Build activity sparkline bars (16 bars across width ~500)
    spark_bars = []
    max_val = max(weekly_sparkline) if weekly_sparkline and max(weekly_sparkline) > 0 else 50
    start_sx = 62.0
    bar_gap = 32.0
    for i, val in enumerate(weekly_sparkline[-16:]):
        bx = start_sx + i * bar_gap
        # Bar height between 8 and 48 px
        bar_h = max(8, int((val / max_val) * 46))
        by = 352 - bar_h
        spark_bars.append(
            f'<rect x="{bx:.1f}" y="{by}" width="16" height="{bar_h}" rx="3" '
            f'class="sparkBar" fill="url(#sparkGrad)" style="animation-delay: {0.4 + i * 0.04:.2f}s;">'
            f'<title>Week {i+1}: {val} contributions</title></rect>'
        )

    sparkline_svg = "\n        ".join(spark_bars)

    svg_content = f"""<svg viewBox="0 0 1200 420" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <!-- Gradients -->
    <radialGradient id="cardGlow" cx="25%" cy="30%" r="85%">
      <stop offset="0%" stop-color="#e85d04" stop-opacity="0.18"/>
      <stop offset="60%" stop-color="#b34700" stop-opacity="0.04"/>
      <stop offset="100%" stop-color="#0d0d0d" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="langGlow" cx="80%" cy="40%" r="75%">
      <stop offset="0%" stop-color="#e85d04" stop-opacity="0.14"/>
      <stop offset="70%" stop-color="#0d0d0d" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="tBorder" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#e85d04" stop-opacity="0.8"/>
      <stop offset="50%" stop-color="#7a3200" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="#e85d04" stop-opacity="0.7"/>
    </linearGradient>
    <linearGradient id="tileBorder" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#e85d04" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#2d1500" stop-opacity="0.6"/>
    </linearGradient>
    <linearGradient id="valGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="60%" stop-color="#ffd9a8"/>
      <stop offset="100%" stop-color="#ffa94d"/>
    </linearGradient>
    <linearGradient id="sparkGrad" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0%" stop-color="#7a3200"/>
      <stop offset="40%" stop-color="#e85d04"/>
      <stop offset="100%" stop-color="#ffa94d"/>
    </linearGradient>
    <linearGradient id="scanSweep" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ff8c42" stop-opacity="0"/>
      <stop offset="50%" stop-color="#ff8c42" stop-opacity="0.08"/>
      <stop offset="100%" stop-color="#ff8c42" stop-opacity="0"/>
    </linearGradient>

    <!-- Clip Paths -->
    <clipPath id="cardClip">
      <rect x="0" y="0" width="1200" height="420" rx="16"/>
    </clipPath>
    <clipPath id="langBarClip">
      <rect x="640" y="112" width="508" height="14" rx="7"/>
    </clipPath>
  </defs>

  <style>
    .mono {{ font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace; }}
    .headPrompt {{ font-family: ui-monospace, monospace; font-size: 13px; fill: #adbac7; }}
    .headStatus {{ font-family: ui-monospace, monospace; font-size: 11px; fill: #3fb950; font-weight: 700; }}
    .secTitle {{ font-family: ui-monospace, monospace; font-size: 12px; fill: #e85d04; font-weight: 700; letter-spacing: 1.2px; }}
    .cardLabel {{ font-family: ui-monospace, monospace; font-size: 10px; fill: #768390; letter-spacing: 0.8px; }}
    .cardVal {{ font-family: ui-monospace, monospace; font-size: 24px; font-weight: 800; fill: url(#valGrad); }}
    .cardSub {{ font-family: ui-monospace, monospace; font-size: 11px; fill: #adbac7; }}
    .langName {{ font-family: ui-monospace, monospace; font-size: 12px; fill: #f0f6fc; font-weight: 600; }}
    .langPct {{ font-family: ui-monospace, monospace; font-size: 11px; fill: #adbac7; font-weight: 700; }}
    .diagLabel {{ font-family: ui-monospace, monospace; font-size: 11px; fill: #768390; }}
    .diagVal {{ font-family: ui-monospace, monospace; font-size: 11px; fill: #ffa94d; font-weight: 600; }}

    /* Animations */
    .pulseDot {{ animation: dotPulse 2s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }}
    @keyframes dotPulse {{ 0%, 100% {{ opacity: 0.4; transform: scale(0.85); }} 50% {{ opacity: 1; transform: scale(1.15); }} }}

    .fadeUp {{ opacity: 0; animation: fUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards; }}
    @keyframes fUp {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: translateY(0); }} }}

    .sparkBar {{ transform-box: fill-box; transform-origin: bottom center; animation: barGrow 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards; }}
    @keyframes barGrow {{ from {{ transform: scaleY(0); }} to {{ transform: scaleY(1); }} }}

    .sweepLine {{ animation: tSweep 7s linear infinite; opacity: 0; }}
    @keyframes tSweep {{
      0% {{ transform: translateY(-100px); opacity: 0; }}
      10% {{ opacity: 1; }}
      60% {{ opacity: 1; }}
      70% {{ transform: translateY(440px); opacity: 0; }}
      100% {{ transform: translateY(440px); opacity: 0; }}
    }}
  </style>

  <g clip-path="url(#cardClip)">
    <!-- Base Background -->
    <rect width="1200" height="420" fill="#0d0d0d"/>
    <rect width="1200" height="420" fill="url(#cardGlow)"/>
    <rect width="1200" height="420" fill="url(#langGlow)"/>

    <!-- Subtle Background Grid -->
    <g stroke="#ffffff" stroke-opacity="0.025" stroke-width="1">
      <line x1="0" y1="46" x2="1200" y2="46"/>
      <line x1="0" y1="180" x2="1200" y2="180"/>
      <line x1="0" y1="280" x2="1200" y2="280"/>
      <line x1="600" y1="46" x2="600" y2="420"/>
      <line x1="300" y1="46" x2="300" y2="420"/>
      <line x1="900" y1="46" x2="900" y2="420"/>
    </g>

    <!-- Radar Scanline Sweep -->
    <rect class="sweepLine" x="0" y="0" width="1200" height="90" fill="url(#scanSweep)" pointer-events="none"/>

    <!-- ==================== TOP WINDOW BAR ==================== -->
    <rect x="0" y="0" width="1200" height="46" fill="#13171d" fill-opacity="0.85"/>
    <line x1="0" y1="46" x2="1200" y2="46" stroke="#e85d04" stroke-opacity="0.3" stroke-width="1"/>

    <!-- Window Dots -->
    <circle cx="26" cy="23" r="5.5" fill="#e85d04"/>
    <circle cx="44" cy="23" r="5.5" fill="#ffa94d" fill-opacity="0.8"/>
    <circle cx="62" cy="23" r="5.5" fill="#2ea043" fill-opacity="0.8"/>

    <!-- Terminal Command -->
    <text x="88" y="27" class="headPrompt">
      <tspan fill="#e85d04" font-weight="700">naveed-gung@hub</tspan><tspan fill="#768390">:</tspan><tspan fill="#ffa94d">~</tspan><tspan fill="#768390">$</tspan> git telemetry --live --visualize
    </text>

    <!-- Status Beacon -->
    <g class="pulseDot" style="animation-duration: 2.2s;">
      <circle cx="1040" cy="23" r="4.5" fill="#3fb950"/>
    </g>
    <text x="1054" y="27" class="headStatus">SYS_ONLINE</text>
    <text x="1136" y="27" class="headPrompt" font-size="11" fill="#768390">2026.Q3</text>

    <!-- ==================== LEFT PANEL: METRICS & STREAK ==================== -->
    <!-- Section Title -->
    <text x="40" y="78" class="secTitle">◈ ACTIVITY &amp; STREAK MATRIX</text>

    <!-- 4 Metric Tiles -->
    <!-- Tile 1: Total Contributions -->
    <g class="fadeUp" style="animation-delay: 0.1s;">
      <rect x="40" y="94" width="250" height="74" rx="8" fill="#161b22" fill-opacity="0.9" stroke="url(#tileBorder)" stroke-width="1"/>
      <text x="56" y="114" class="cardLabel">TOTAL CONTRIBUTIONS</text>
      <text x="56" y="146" class="cardVal">{total_contribs:,}</text>
      <text x="156" y="145" class="cardSub">({total_commits:,} commits)</text>
    </g>

    <!-- Tile 2: Streak -->
    <g class="fadeUp" style="animation-delay: 0.18s;">
      <rect x="306" y="94" width="250" height="74" rx="8" fill="#161b22" fill-opacity="0.9" stroke="url(#tileBorder)" stroke-width="1"/>
      <text x="322" y="114" class="cardLabel">STREAK VELOCITY</text>
      <text x="322" y="146" class="cardVal">{current_streak} <tspan font-size="14" font-weight="600" fill="#ffa94d">DAYS</tspan></text>
      <text x="442" y="145" class="cardSub">(best: {longest_streak}d)</text>
    </g>

    <!-- Tile 3: Repos & Stars -->
    <g class="fadeUp" style="animation-delay: 0.26s;">
      <rect x="40" y="178" width="250" height="74" rx="8" fill="#161b22" fill-opacity="0.9" stroke="url(#tileBorder)" stroke-width="1"/>
      <text x="56" y="198" class="cardLabel">REPOSITORIES &amp; STARS</text>
      <text x="56" y="230" class="cardVal">{repos} <tspan font-size="14" font-weight="600" fill="#ffa94d">REPOS</tspan></text>
      <text x="180" y="229" class="cardSub">({stars} ★ earned)</text>
    </g>

    <!-- Tile 4: PRs & Issues -->
    <g class="fadeUp" style="animation-delay: 0.34s;">
      <rect x="306" y="178" width="250" height="74" rx="8" fill="#161b22" fill-opacity="0.9" stroke="url(#tileBorder)" stroke-width="1"/>
      <text x="322" y="198" class="cardLabel">PULL REQUESTS &amp; ISSUES</text>
      <text x="322" y="230" class="cardVal">{total_prs} <tspan font-size="14" font-weight="600" fill="#ffa94d">PRS</tspan></text>
      <text x="420" y="229" class="cardSub">({total_issues} issues filed)</text>
    </g>

    <!-- Activity Sparkline Chart -->
    <g class="fadeUp" style="animation-delay: 0.4s;">
      <rect x="40" y="262" width="516" height="120" rx="8" fill="#12161c" fill-opacity="0.8" stroke="#30363d" stroke-width="1"/>
      <text x="56" y="284" class="cardLabel">ACTIVITY VELOCITY (RECENT 16 WEEKS)</text>
      <text x="502" y="284" text-anchor="end" class="headPrompt" font-size="10" fill="#e85d04">● CONSISTENT CADENCE</text>
      
      <!-- Sparkline Base Axis -->
      <line x1="56" y1="354" x2="540" y2="354" stroke="#21262d" stroke-width="1"/>

      <!-- Bars -->
      {sparkline_svg}
    </g>

    <!-- ==================== CENTER DIVIDER ==================== -->
    <line x1="596" y1="64" x2="596" y2="394" stroke="#30363d" stroke-opacity="0.8" stroke-dasharray="4 4" stroke-width="1"/>

    <!-- ==================== RIGHT PANEL: LANGUAGES & STACK ==================== -->
    <!-- Section Title -->
    <text x="640" y="78" class="secTitle">◈ LANGUAGE &amp; STACK TELEMETRY</text>

    <!-- Multi-Color Progress Bar Container -->
    <rect x="640" y="112" width="508" height="14" rx="7" fill="#161b22"/>
    <g clip-path="url(#langBarClip)">
      {stacked_bar_svg}
    </g>

    <!-- Language Grid Breakdown -->
    {languages_svg}

    <!-- Bottom Diagnostic Info Box -->
    <g class="fadeUp" style="animation-delay: 0.7s;">
      <rect x="640" y="306" width="508" height="76" rx="8" fill="#161b22" fill-opacity="0.9" stroke="url(#tileBorder)" stroke-width="1"/>
      
      <text x="656" y="330" class="diagLabel">LATEST ACTIVITY REPO:</text>
      <text x="815" y="330" class="diagVal">{last_repo}</text>
      
      <text x="656" y="352" class="diagLabel">LAST PUSH TIMESTAMP:</text>
      <text x="815" y="352" class="diagVal">{last_push}</text>
      
      <text x="656" y="372" class="diagLabel">ACCOUNT MATURITY:</text>
      <text x="815" y="372" class="diagVal">{account_age} · {followers} Followers</text>

      <circle cx="1126" cy="344" r="10" fill="#e85d04" fill-opacity="0.15"/>
      <path d="M1122 344 L1125 347 L1131 341" stroke="#e85d04" stroke-width="2" fill="none"/>
    </g>

    <!-- Card Outer Border -->
    <rect x="0.5" y="0.5" width="1199" height="419" rx="16" fill="none" stroke="url(#tBorder)" stroke-width="1.5"/>
  </g>
</svg>
"""
    return svg_content


def main():
    stats = load_stats()
    svg = generate_svg(stats)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Generated {OUT_FILE} successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
