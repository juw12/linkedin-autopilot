<p align="center">
  <a href="https://github.com/juw12/reachpilot"><img src="docs/media/banner.jpg" alt="ReachPilot (formerly LinkedIn Autopilot): turns your GitHub into a LinkedIn schedule." width="100%"></a>
</p>

<p align="center">
  <b>Turns your GitHub into a LinkedIn schedule.</b><br>
  Posts your projects on a schedule through the official API, with real facts and real screenshots, and learns which projects people actually click.
</p>

<p align="center">
  <a href="https://github.com/juw12/reachpilot/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/juw12/reachpilot/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <img src="https://img.shields.io/badge/Windows-24292f" alt="Windows">
  <img src="https://img.shields.io/badge/Python_3.10+-24292f" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-2563eb" alt="MIT">
</p>

<p align="center"><a href="https://juw12.github.io/reachpilot/"><b>Live demo</b></a> (the real dashboard with made-up data)</p>

## ReachPilot hub

The app is now called **ReachPilot** (formerly LinkedIn Autopilot). The LinkedIn poster is still the core;
the local dashboard has become a hub for all of my outreach automations:

- **Overview / To do**: one list of the few things that need a human right now (renew a login, a site asking
  for a verification code, a stopped task), with a button for each.
- **Schedule**: every job runs from Windows Task Scheduler under the `\ReachPilot\` folder.
- **Private modules**: anything personal (job search, e-mail applications) lives in `modules/`,
  which is git-ignored and never committed. The dashboard only shows a module if it exists on disk.
- **Help**: the `?` button explains, per module, when it runs, what it does by itself and what it needs from you.

Dashboard actions are whitelisted and require a per-session token from the same origin.

## Why

I build a lot of side projects. Writing a LinkedIn post for each one, finding a screenshot, picking a time,
then doing it again next week for the older ones: that's the part that never happens.
This does it for me, without making anything up.

## What it does

Every 30 minutes (Windows Task Scheduler), one run:

1. **Watches GitHub** through the `gh` CLI: new public repos, new releases, real commit activity.
2. **Picks what to post** on the next slot (Tue/Wed/Thu 08:00 by default), in this order:
   new repo â†’ new release â†’ your hand-written queue â†’ active-repo update â†’ top-performer roundup â†’ a recycled project from a fresh angle.
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

It also repairs mojibake in READMEs ("Ã¢â€°Â¤" â†’ "â‰¤"), skips install/run steps when picking feature bullets,
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
| `dashboard` | local app: upcoming schedule with post previews, every post's reach, GitHub visitors, stars and "Try it" clicks, what the bot has learned, and the log |
| `preview 5` | write the next 5 posts to `drafts/` without posting |
| `approve` | in `"mode": "review"`, approve the waiting draft |
| `PAUSE` file | kill switch |

LinkedIn tokens for self-serve apps last 60 days with no refresh token; you get a Windows notification a week before.

## Files

`autopilot.py` scheduler and commands Â· `composer.py` post writing and guards Â· `github_source.py` README/commit facts Â·
`visuals.py` screenshots Â· `linkedin_api.py` OAuth, images, posts, comments Â· `analytics.py` scoring Â·
`trylinks.py` tracked go-links Â· `selftest.py` tests (run in CI) Â· `dashboard.py` + `dashboard.html` local dashboard

## License

Released under the MIT License Â© 2026 Juwen Lie.
