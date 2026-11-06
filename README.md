<p align="center">
  <a href="https://github.com/juw12/linkedin-autopilot"><img src="docs/media/banner.jpg" alt="LinkedIn Autopilot: turns your GitHub into a LinkedIn schedule." width="100%"></a>
</p>

<p align="center">
  <b>Turns your GitHub into a LinkedIn schedule.</b><br>
  Posts your projects on a schedule through the official API, with real facts and real screenshots, and learns which projects people actually click.
</p>

<p align="center">
  <a href="https://github.com/juw12/linkedin-autopilot/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/juw12/linkedin-autopilot/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <img src="https://img.shields.io/badge/Windows-24292f" alt="Windows">
  <img src="https://img.shields.io/badge/Python_3.10+-24292f" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-2563eb" alt="MIT">
</p>

## Why

I build a lot of side projects. Writing a LinkedIn post for each one, finding a screenshot, picking a time,
then doing it again next week for the older ones: that's the part that never happens.
This does it for me, without making anything up.

## What it does

Every 30 minutes (Windows Task Scheduler), one run:

1. **Watches GitHub** through the `gh` CLI: new public repos, new releases, real commit activity.
2. **Picks what to post** on the next slot (Tue/Wed/Thu 08:00 by default), in this order:
   new repo → new release → your hand-written queue → active-repo update → top-performer roundup → a recycled project from a fresh angle.
3. **Writes the post from facts only**: README tagline and feature bullets, description, topics, commit messages.
   A guard rejects any number that isn't in the source. No invented users, stars or backstory.
4. **Uses real visuals**: fresh screenshots of the live site (and the repo page), plus media from the README.
5. **Never repeats itself**: four angles (launch, one-feature deep dive, behind the build, ask-for-feedback),
   different README facts each time, a 21-day cooldown per project and a similarity cap against every past post.
   If nothing fresh qualifies, it skips the slot instead of reposting.
6. **Learns what lands**, automatically:
   - visitors GitHub attributes to linkedin.com / lnkd.in (repo traffic API),
   - stars gained in the week after a post,
   - "Try it" clicks: site links go through `<you>.github.io/go/<project>/`, a tiny page that counts the click with
     [GoatCounter](https://www.goatcounter.com) and forwards in well under a second.

   Projects and angles are then chosen with Thompson sampling: winners get posted more, untested ones still get a turn.

It also repairs mojibake in READMEs ("â‰¤" → "≤"), skips install/run steps when picking feature bullets,
and posts links as the first comment.

## What it deliberately doesn't do

- No browser automation, scraping, auto-likes or auto-comments on other people's posts. Only the official
  **Share on LinkedIn** API, so the account stays in good standing.
- No AI-invented claims. An optional local model (Ollama) can rephrase, but its output is rejected if it adds numbers.

## Setup

```powershell
pip install requests openpyxl pillow playwright
gh auth login                        # GitHub CLI, used for repos, READMEs and traffic
python autopilot.py init             # baseline your repos (+ optional seed_queue.json of hand-written posts)
python autopilot.py auth             # LinkedIn: app with "Share on LinkedIn" + "Sign In with LinkedIn (OpenID)",
                                     # redirect URL http://localhost:8765/callback
python autopilot.py goatcounter      # optional: free GoatCounter site + API token for "Try it" clicks
python autopilot.py install          # scheduled task: every 30 min + at logon
```

Edit `config.json` for your GitHub user, time zone, slots, caps and excluded repos.

| Command | |
|---|---|
| `status` | token expiry, queue, recent posts, best performers, click counts |
| `preview 5` | write the next 5 posts to `drafts/` without posting |
| `approve` | in `"mode": "review"`, approve the waiting draft |
| `PAUSE` file | kill switch |

LinkedIn tokens for self-serve apps last 60 days with no refresh token; you get a Windows notification a week before.

## Files

`autopilot.py` scheduler and commands · `composer.py` post writing and guards · `github_source.py` README/commit facts ·
`visuals.py` screenshots · `linkedin_api.py` OAuth, images, posts, comments · `analytics.py` scoring ·
`trylinks.py` tracked go-links · `selftest.py` tests (run in CI)

## License

Released under the MIT License © 2026 Juwen Lie.
