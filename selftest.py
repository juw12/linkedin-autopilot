"""Offline-safe checks: README extraction, composer guards, little-text, analytics parsing, scoring."""
import json
import os
import random
import sys
import sqlite3
import tempfile
from pathlib import Path

from openpyxl import Workbook

import analytics
import composer
import github_source as gh
from linkedin_api import to_little_text
import trylinks

config = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
failures = []


def check(condition, message):
    print(("PASS " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


# 1) README extraction on every eligible repo (needs the gh CLI + network; skipped in CI)
if os.environ.get("CI"):
    print("Skipping live README extraction in CI")
else:
    repos = gh.list_public_repos(config["github_user"], config["exclude_repos"])
    print(f"\n{len(repos)} eligible repos")
    for repo in repos:
        facts = gh.repo_facts(config["github_user"], repo)
        bad = [bullet for bullet in facts.bullets if gh.FILE_TOKEN.search(bullet) or bullet[:1] in "-*|"]
        print(f"  {facts.display_name:<26} bullets={len(facts.bullets):<3} images={len(facts.readme_images)}  e.g. {facts.bullets[:1]}")
        check(not bad, f"{repo['name']}: no file-path/markup bullets {bad[:1]}")


# 2) Guards
source = "EarlyStar tracks 24 h / 7 d / 30 d star gains"
check(composer.invented_numbers("Gained 24 stars in 7 d", source) == [], "numbers present in source are allowed")
check(composer.invented_numbers("Used by 5000 developers", source) == ["5000"], "invented number is caught")
original = "One detail in Cairn I'm quietly proud of: TODO comments become tasks that remember file and line."
check(composer.similarity(original, original) == 1.0, "identical text similarity = 1")
check(composer.similarity(original, "Undanu sends wedding invitations over WhatsApp with QR check-in.") < 0.1, "different text similarity is low")

# 3) Little-text escaping keeps hashtags clickable
little = to_little_text("Try it (free) #OpenSource #AI_tools")
check("\\(free\\)" in little, "parentheses escaped")
check("{hashtag|\\#|OpenSource}" in little, "hashtag converted to template")

# 4) Analytics export parsing (LinkedIn layout: two tables side by side)
with tempfile.TemporaryDirectory() as tmp:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "TOP POSTS"
    sheet.append(["Maximum of 50 posts available to include in this list"])
    sheet.append(["Post URL", "Post publish date", "Engagements", None, "Post URL", "Post publish date", "Impressions"])
    sheet.append(["https://www.linkedin.com/feed/update/urn:li:activity:1/", "11/11/2026", 40, None,
                  "https://www.linkedin.com/feed/update/urn:li:activity:1/", "11/11/2026", 1000])
    sheet.append(["https://www.linkedin.com/feed/update/urn:li:activity:2/", "11/12/2026", 5, None,
                  "https://www.linkedin.com/feed/update/urn:li:activity:2/", "11/12/2026", 900])
    path = Path(tmp) / "export.xlsx"
    workbook.save(path)
    records = analytics.parse_export(path)
    first = records.get("https://www.linkedin.com/feed/update/urn:li:activity:1/", {})
    check(first.get("engagements") == 40 and first.get("impressions") == 1000 and first.get("post_date") == "2026-11-11",
          "xlsx export parsed (engagements + impressions + date)")

# 5) Thompson scorer prefers the stronger performer
records = [
    {"repo": "buddy", "angle": "launch", "impressions": 1000, "engagements": 40},
    {"repo": "earlystar", "angle": "launch", "impressions": 900, "engagements": 5},
]
scorer = analytics.ThompsonScorer(records, "repo")
rng = random.Random(1)
wins_vs_weak = sum(scorer.sample("buddy", rng) > scorer.sample("earlystar", rng) for _ in range(1000))
wins_vs_new = sum(scorer.sample("buddy", rng) > scorer.sample("never-posted-repo", rng) for _ in range(1000))
check(scorer.ranked()[0][0] == "buddy", "ranked: buddy first")
check(wins_vs_weak > 950, f"proven winner beats proven loser ({wins_vs_weak}/1000)")
check(600 < wins_vs_new < 990, f"untested projects still get explored ({1000 - wins_vs_new}/1000 picks)")

# 6) Automatic GitHub-based scoring: LinkedIn-referred visitors + stars after a post
from datetime import datetime, timedelta

memory_db = sqlite3.connect(":memory:")
memory_db.executescript(
    """
    CREATE TABLE posts(slot TEXT, source TEXT, repo TEXT, angle TEXT, status TEXT, posted_at TEXT);
    CREATE TABLE metrics(url TEXT, post_date TEXT, impressions INTEGER, engagements INTEGER);
    CREATE TABLE github_signals(day TEXT, repo TEXT, li_views INTEGER, li_uniques INTEGER, stars INTEGER);
    """
)
post_day = datetime.now() - timedelta(days=10)
memory_db.execute("INSERT INTO posts VALUES('s','recycle','buddy','launch','posted',?)", (post_day.strftime("%Y-%m-%d 08:00"),))
for offset, visitors, stars in [(-1, 2, 5), (0, 2, 5), (4, 14, 7), (8, 20, 9)]:
    memory_db.execute(
        "INSERT INTO github_signals VALUES(?, 'buddy', 0, ?, ?)",
        ((post_day + timedelta(days=offset)).strftime("%Y-%m-%d"), visitors, stars),
    )
scored = analytics.github_performance(memory_db)
check(len(scored) == 1 and scored[0]["visitors"] == 18 and scored[0]["stars"] == 2,
      f"GitHub score = +18 LinkedIn visitors, +2 stars in week one ({scored})")
check(scored and scored[0]["engagements"] == 18 + 3 * 2, "combined score = visitors + 3 x stars")

# 7) "Try it" go-links: detection, rewriting, page content, click scoring
comment = "Try it: https://juw12.github.io/watt-reserve/\nDemo: https://juw12.github.io/cairn/?demo=1\nLanding: https://juw12.github.io/p/x/"
links = trylinks.find_site_links(comment, "juw12")
check(set(links) == {"watt-reserve", "cairn"}, f"site links found, reserved paths skipped ({sorted(links)})")
rewritten = trylinks.rewrite_links(comment, "juw12", {"watt-reserve", "cairn"})
check("https://juw12.github.io/go/watt-reserve/" in rewritten and "go/cairn/" in rewritten and "/p/x/" in rewritten,
      "links rewritten to tracked go-links, others untouched")
page = trylinks.render_page("cairn", "https://juw12.github.io/cairn/?demo=1", "juw12")
check("juw12.goatcounter.com/count" in page and 'location.replace(target)' in page and "noindex" in page,
      "go page counts the click, redirects, and is hidden from search")
memory_db.execute("CREATE TABLE try_clicks(day TEXT, repo TEXT, clicks INTEGER)")
memory_db.execute("INSERT INTO try_clicks VALUES(?, 'buddy', 7)", ((post_day + timedelta(days=1)).strftime("%Y-%m-%d"),))
scored = analytics.github_performance(memory_db)
check(scored[0]["try_clicks"] == 7 and scored[0]["engagements"] == 18 + 7 + 3 * 2,
      f"'Try it' clicks are added to the score ({scored[0]['engagements']})")

print("\nALL PASSED" if not failures else f"\n{len(failures)} FAILED")
sys.exit(1 if failures else 0)
