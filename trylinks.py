"""Measure "Try it" clicks without touching the project sites.

Every live-site link the bot posts (https://<user>.github.io/<name>/...) is swapped for a tracked
go-link, https://<user>.github.io/go/<name>/. That tiny page (committed to the <user>.github.io repo)
pings GoatCounter and forwards the visitor to the real site in well under a second. Link-preview
crawlers don't run JavaScript, so they are not counted.

GoatCounter (free, open source, privacy-friendly) stores the counts; the bot reads them back daily
through its API with a token kept in secrets.json.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests

CREATE_NO_WINDOW = 0x08000000
RESERVED_NAMES = {"go", "p", "assets", "static"}

GO_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="{title}">
<meta property="og:url" content="{target}">
<link rel="canonical" href="{target}">
<noscript><meta http-equiv="refresh" content="0;url={target}"></noscript>
<script>
(function () {{
  var target = "{target_js}";
  var done = false;
  function go() {{ if (!done) {{ done = true; location.replace(target); }} }}
  var ping = new Image();
  ping.onload = ping.onerror = go;
  ping.src = "https://{code}.goatcounter.com/count?p=" + encodeURIComponent("/go/{name}")
    + "&t=" + encodeURIComponent("{name}")
    + "&r=" + encodeURIComponent(document.referrer || "")
    + "&rnd=" + Math.random();
  setTimeout(go, 700);
}})();
</script>
<style>body{{font:16px system-ui,sans-serif;margin:3rem;color:#334155}}</style>
</head>
<body><p>Opening <a href="{target}">{title}</a>&hellip;</p></body>
</html>
"""


def go_url(user: str, name: str) -> str:
    return f"https://{user}.github.io/go/{name}/"


def find_site_links(text: str, user: str) -> dict[str, str]:
    """name -> original URL for every https://<user>.github.io/<name>/... link in the text."""
    pattern = re.compile(rf"https://{re.escape(user)}\.github\.io/([A-Za-z0-9_.-]+)/[^\s)\]]*", re.IGNORECASE)
    links = {}
    for match in pattern.finditer(text):
        name = match.group(1)
        if name.lower() not in RESERVED_NAMES:
            links.setdefault(name, match.group(0))
    return links


def rewrite_links(text: str, user: str, available: set[str]) -> str:
    for name, url in find_site_links(text, user).items():
        if name in available:
            text = text.replace(url, go_url(user, name))
    return text


def render_page(name: str, target: str, code: str) -> str:
    title = name.replace("-", " ").title()
    return GO_PAGE.format(
        title=html.escape(title),
        target=html.escape(target, quote=True),
        target_js=target.replace("\\", "\\\\").replace('"', '\\"'),
        code=code,
        name=name,
    )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=CREATE_NO_WINDOW,
    )


def sync_go_pages(user: str, targets: dict[str, str], code: str, checkout: Path, log) -> set[str]:
    """Write/update go/<name>/index.html in the <user>.github.io repo; one commit, only if changed."""
    if not checkout.exists():
        result = subprocess.run(
            ["gh", "repo", "clone", f"{user}/{user}.github.io", str(checkout), "--", "--depth", "1"],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            log.warning("Could not clone %s.github.io: %s", user, result.stderr[-300:])
            return set()
    _git(["pull", "--ff-only", "--quiet"], checkout)

    changed = []
    for name, target in targets.items():
        page = checkout / "go" / name / "index.html"
        content = render_page(name, target, code)
        if page.exists() and page.read_text(encoding="utf-8") == content:
            continue
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(content, encoding="utf-8", newline="\n")
        changed.append(name)

    if changed:
        _git(["add", "go"], checkout)
        commit = _git(["commit", "-m", f"Tracked go-links: {', '.join(sorted(changed))}"], checkout)
        push = _git(["push", "--quiet"], checkout)
        if push.returncode != 0:
            log.warning("Pushing go-links failed: %s", (push.stderr or commit.stderr)[-300:])
            return set()
        log.info("Published %d go-link page(s): %s", len(changed), ", ".join(sorted(changed)))

    existing = {path.parent.name for path in (checkout / "go").glob("*/index.html")}
    return existing


def check_goatcounter(code: str, token: str) -> str:
    response = requests.get(
        f"https://{code}.goatcounter.com/api/v0/me",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    user = response.json().get("user") or response.json()
    return user.get("email") or user.get("name") or "ok"


def fetch_go_clicks(code: str, token: str, day: str) -> dict[str, int]:
    """Clicks per go-link name for one day (YYYY-MM-DD)."""
    start = datetime.strptime(day, "%Y-%m-%d")
    end = start + timedelta(days=1) - timedelta(seconds=1)
    response = requests.get(
        f"https://{code}.goatcounter.com/api/v0/stats/hits",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 100,
        },
        timeout=30,
    )
    response.raise_for_status()
    clicks: dict[str, int] = {}
    for hit in response.json().get("hits", []):
        path = hit.get("path") or ""
        if not path.startswith("/go/"):
            continue
        name = path.strip("/").split("/")[1] if path.count("/") >= 2 else ""
        if not name:
            continue
        count = hit.get("count")
        if count is None:
            count = sum(day_stat.get("daily", 0) for day_stat in hit.get("stats", []))
        clicks[name] = clicks.get(name, 0) + int(count or 0)
    return clicks


def load_goatcounter(config: dict, secrets_path: Path) -> tuple[str, str] | None:
    code = config.get("goatcounter_code", "").strip()
    if not code or not secrets_path.exists():
        return None
    token = json.loads(secrets_path.read_text(encoding="utf-8")).get("goatcounter_token", "")
    return (code, token) if token else None
