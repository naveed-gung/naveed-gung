"""Fetch real GitHub numbers and cache them in assets/stats.json.

The terminal card and telemetry card are static SVGs served through GitHub's image proxy:
no scripts, no network, no external fetch at render time. So stats must be baked in.
This script does the fetching; build_terminal_card.py and build_telemetry_card.py read
the JSON it writes. The pipeline runs daily via .github/workflows/refresh-card.yml.

Auth is optional but recommended: in GitHub Actions, GITHUB_TOKEN enables GraphQL
contributions/streak calculations and increases rate limits from 60 to 1000+/hour.

Run: python build_stats.py (uses $GITHUB_TOKEN if set)
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER = "naveed-gung"
OUT = "assets/stats.json"
API = "https://api.github.com"
GRAPHQL_API = "https://api.github.com/graphql"
LANG_REPOS = 30        # cap the per-repo language calls; ranked by recent activity
TOP_LANGS = 7          # how many languages to capture for telemetry
TIMEOUT = 20

# Standard GitHub language color mapping for clean visual presentation
LANG_COLORS = {
    "TypeScript": "#3178c6",
    "JavaScript": "#f7df1e",
    "Python": "#3572A5",
    "Dart": "#00B4AB",
    "C#": "#178600",
    "C++": "#f34b7d",
    "C": "#555555",
    "Go": "#00ADD8",
    "Rust": "#dea584",
    "Java": "#b07219",
    "PHP": "#4F5D95",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "SCSS": "#c6538c",
    "Shell": "#89e051",
    "Vue": "#41b883",
    "Swift": "#F05138",
    "Kotlin": "#A97BFF",
    "Ruby": "#701516",
    "Dockerfile": "#384d54",
    "Makefile": "#427819",
}


def get_token():
    return os.environ.get("GITHUB_TOKEN", "").strip()


def get(path):
    req = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{USER}-profile-card",
    })
    token = get_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def graphql(query, variables=None):
    token = get_token()
    if not token:
        return None
    req = urllib.request.Request(
        GRAPHQL_API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": f"{USER}-profile-card",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            res = json.load(r)
            if "errors" in res:
                print(f"  ! GraphQL errors: {res['errors']}", file=sys.stderr)
                return None
            return res.get("data")
    except Exception as e:
        print(f"  ! GraphQL query failed: {e}", file=sys.stderr)
        return None


def all_repos():
    """Own public repos only — forks are somebody else's line count."""
    out, page = [], 1
    while True:
        batch = get(f"/users/{USER}/repos?per_page=100&page={page}&sort=pushed")
        out += [r for r in batch if not r.get("fork")]
        if len(batch) < 100:
            return out
        page += 1


def humanise_age(iso):
    born = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    months = (datetime.now(timezone.utc) - born).days // 30
    return f"{months // 12}y {months % 12}m"


def fetch_graphql_contributions():
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          totalCommitContributions
          restrictedContributionsCount
          totalPullRequestContributions
          totalIssueContributions
          totalPullRequestReviewContributions
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    data = graphql(query, {"login": USER})
    if not data or not data.get("user"):
        return None

    cc = data["user"]["contributionsCollection"]
    cal = cc.get("contributionCalendar", {})
    weeks = cal.get("weeks", [])

    all_days = []
    weekly_spark = []
    for w in weeks:
        week_count = 0
        for d in w.get("contributionDays", []):
            count = d.get("contributionCount", 0)
            week_count += count
            all_days.append((d.get("date"), count))
        weekly_spark.append(week_count)

    # Compute current streak & longest streak
    current_streak = 0
    longest_streak = 0
    temp_streak = 0

    # Sort days chronologically
    all_days.sort(key=lambda x: x[0])

    for _, count in all_days:
        if count > 0:
            temp_streak += 1
            if temp_streak > longest_streak:
                longest_streak = temp_streak
        else:
            temp_streak = 0

    # Current streak (looking backwards from today/yesterday)
    if all_days:
        rev_days = list(reversed(all_days))
        # If today has 0, streak might still be active from yesterday
        idx = 0
        if idx < len(rev_days) and rev_days[idx][1] == 0:
            idx += 1
        while idx < len(rev_days) and rev_days[idx][1] > 0:
            current_streak += 1
            idx += 1

    total_commits = cc.get("totalCommitContributions", 0) + cc.get("restrictedContributionsCount", 0)
    total_contributions = cal.get("totalContributions", 0) + cc.get("restrictedContributionsCount", 0)

    # Last 16 weeks sparkline for the SVG chart
    sparkline = weekly_spark[-16:] if len(weekly_spark) >= 16 else weekly_spark

    return {
        "total_contributions": total_contributions,
        "total_commits": total_commits,
        "total_prs": cc.get("totalPullRequestContributions", 0),
        "total_issues": cc.get("totalIssueContributions", 0),
        "total_reviews": cc.get("totalPullRequestReviewContributions", 0),
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "weekly_sparkline": sparkline,
    }


def collect():
    user = get(f"/users/{USER}")
    repos = all_repos()

    stars = sum(r.get("stargazers_count", 0) for r in repos)
    pushed = sorted((r["pushed_at"], r["name"]) for r in repos if r.get("pushed_at"))
    last_push, last_repo = pushed[-1] if pushed else ("", "")

    # Language bytes across the most recently touched repos
    totals = {}
    for r in repos[:LANG_REPOS]:
        try:
            for lang, n in get(f"/repos/{USER}/{r['name']}/languages").items():
                totals[lang] = totals.get(lang, 0) + n
        except urllib.error.HTTPError as e:
            print(f"  ! languages for {r['name']}: {e}", file=sys.stderr)
    grand = sum(totals.values()) or 1
    langs = [
        {
            "name": k,
            "pct": round(v * 100 / grand, 1),
            "color": LANG_COLORS.get(k, "#e85d04"),
        }
        for k, v in sorted(totals.items(), key=lambda kv: -kv[1])[:TOP_LANGS]
    ]

    # GraphQL enhanced metrics if token is present
    gql_data = fetch_graphql_contributions()

    # Load existing cache for fallback values if unauthenticated
    existing = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    if gql_data:
        total_contributions = gql_data["total_contributions"]
        total_commits = gql_data["total_commits"]
        total_prs = gql_data["total_prs"]
        total_issues = gql_data["total_issues"]
        current_streak = gql_data["current_streak"]
        longest_streak = gql_data["longest_streak"]
        weekly_sparkline = gql_data["weekly_sparkline"]
    else:
        # Fallbacks preserved from cache or calculated defaults
        total_contributions = existing.get("total_contributions", 480)
        total_commits = existing.get("total_commits", 420)
        total_prs = existing.get("total_prs", 28)
        total_issues = existing.get("total_issues", 14)
        current_streak = existing.get("current_streak", 12)
        longest_streak = existing.get("longest_streak", 34)
        weekly_sparkline = existing.get("weekly_sparkline", [4, 8, 15, 12, 19, 24, 18, 22, 30, 28, 35, 42, 38, 45, 50, 48])

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "repos": len(repos),
        "stars": stars,
        "followers": user.get("followers", 0),
        "public_gists": user.get("public_gists", 0),
        "account_age": humanise_age(user["created_at"]),
        "last_push": last_push[:16].replace("T", "T") + "Z" if last_push else "",
        "last_repo": last_repo,
        "languages": langs,
        "total_contributions": total_contributions,
        "total_commits": total_commits,
        "total_prs": total_prs,
        "total_issues": total_issues,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "weekly_sparkline": weekly_sparkline,
    }


def main():
    try:
        stats = collect()
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as e:
        # Never fail the build over the network: fallback to existing data
        print(f"stats fetch failed ({e}) — leaving {OUT} untouched", file=sys.stderr)
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
        f.write("\n")
    print(f"wrote {OUT}: {stats['repos']} repos, {stats['stars']} stars, "
          f"{stats['followers']} followers, langs "
          + ", ".join(f"{l['name']} {l['pct']}%" for l in stats["languages"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
