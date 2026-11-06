"""Local dashboard: upcoming schedule, post history, reach and the signals the bot learns from.

Run:  python autopilot.py dashboard
Opens http://127.0.0.1:8770 (or the next free port) in an app window. Read-only: it never posts or changes the schedule.
The server stops by itself 15 minutes after the window is closed.
"""
from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import analytics
import autopilot as ap
import trylinks

PORTS = range(8770, 8790)
PING_REPLY = b"linkedin-autopilot-dashboard"
IDLE_SHUTDOWN_SECONDS = 15 * 60
PAGE = Path(__file__).with_name("dashboard.html")
IMAGE_ROOTS = (ap.ASSETS.resolve(), ap.GENERATED.resolve())
AUTO_SLOTS_SHOWN = 3


# ------------------------------------------------------------------ helpers

def image_url(path: str) -> str:
    return "/img?p=" + quote(str(path))


def week_start(moment: datetime) -> str:
    return (moment - timedelta(days=moment.weekday())).strftime("%Y-%m-%d")


def first_line(text: str | None) -> str:
    return (text or "").strip().split("\n")[0]


def title_for(repo: str | None, angle: str) -> str:
    if repo:
        return repo
    return {"intro": "About me", "roundup": "Roundup"}.get(angle, "To be decided")


def upcoming_slot_times(config: dict, now: datetime, count: int) -> list[datetime]:
    """Next slot start times, including a slot that is still inside its catch-up window."""
    times: list[datetime] = []
    day = (now - timedelta(hours=config["catch_up_hours"])).date()
    while len(times) < count:
        for slot in config["slots"]:
            if ap.WEEKDAYS[day.weekday()] != slot["weekday"]:
                continue
            hour, minute = map(int, slot["time"].split(":"))
            start = datetime(day.year, day.month, day.day, hour, minute)
            if start + timedelta(hours=config["catch_up_hours"]) > now:
                times.append(start)
        day += timedelta(days=1)
    return sorted(times)[:count]


# ------------------------------------------------------------------ data

def forecast(db, config: dict, now: datetime) -> list[dict]:
    """Mirror of the picker's order: drafted slot > new GitHub event > prepared post due > picked on the day."""
    events = [row[0] for row in db.execute("SELECT repo FROM events WHERE consumed=0 ORDER BY id")]
    seeds = [
        dict(zip(("id", "slot_date", "repo", "angle", "body", "comment", "images"), row))
        for row in db.execute(
            "SELECT id, slot_date, repo, angle, body, comment, images FROM queue WHERE used=0 ORDER BY slot_date, id"
        )
    ]
    drafted = {
        row[0]: dict(zip(("slot", "status", "repo", "angle", "body", "comment", "images"), row))
        for row in db.execute("SELECT slot, status, repo, angle, body, comment, images FROM posts")
    }
    per_week: dict[str, int] = {}
    for (posted_at,) in db.execute("SELECT posted_at FROM posts WHERE status='posted'"):
        key = week_start(datetime.strptime(posted_at[:10], "%Y-%m-%d"))
        per_week[key] = per_week.get(key, 0) + 1

    plan: list[dict] = []
    auto_left = AUTO_SLOTS_SHOWN
    for start in upcoming_slot_times(config, now, 60):
        key = start.strftime("%Y-%m-%d %H:%M")
        row = drafted.get(key)
        if row and row["status"] in ("posted", "skipped"):
            continue
        entry = {"slot": key, "weekday": ap.WEEKDAYS[start.weekday()], "images": [], "body": "", "comment": ""}
        week = week_start(start)
        if per_week.get(week, 0) >= config["max_posts_per_week"]:
            plan.append({**entry, "kind": "cap", "title": "Skipped", "angle": "-", "repo": None,
                         "hook": "Weekly limit reached, so this slot stays empty."})
            continue
        per_week[week] = per_week.get(week, 0) + 1

        if row:
            images = json.loads(row["images"] or "[]")
            plan.append({**entry, "kind": "drafted", "status": row["status"], "repo": row["repo"], "angle": row["angle"],
                         "title": title_for(row["repo"], row["angle"]), "hook": first_line(row["body"]),
                         "body": row["body"] or "", "comment": row["comment"] or "",
                         "images": [image_url(path) for path in images]})
        elif events:
            repo = events.pop(0)
            plan.append({**entry, "kind": "event", "repo": repo, "angle": "launch", "title": repo,
                         "hook": "New on GitHub. The post is written at 08:00 from the README."})
        else:
            due = [seed for seed in seeds if seed["slot_date"] <= start.strftime("%Y-%m-%d")]
            if due:
                seed = due[0]
                seeds.remove(seed)
                plan.append({**entry, "kind": "prepared", "repo": seed["repo"], "angle": seed["angle"],
                             "title": title_for(seed["repo"], seed["angle"]), "hook": first_line(seed["body"]),
                             "body": seed["body"], "comment": seed["comment"],
                             "images": [image_url(ap.ASSETS / name) for name in json.loads(seed["images"])]})
            else:
                plan.append({**entry, "kind": "auto", "repo": None, "angle": "auto", "title": "Picked on the day",
                             "hook": "Something new on GitHub if there is one, otherwise a fresh take on a "
                                     "project that is doing well, or a roundup."})
                auto_left -= 1
        if auto_left == 0:
            break
    return plan


def history(db) -> list[dict]:
    metrics = {row[0]: row[1:] for row in db.execute("SELECT post_date, impressions, engagements FROM metrics")}
    github = {(record["repo"], record["date"]): record for record in analytics.github_performance(db)}
    posts = []
    for slot, status, repo, angle, body, url, posted_at, error, attempts in db.execute(
        "SELECT slot, status, repo, angle, body, url, posted_at, error, attempts FROM posts ORDER BY slot DESC"
    ):
        day = (posted_at or slot)[:10]
        impressions, engagements = metrics.get(day, (None, None))
        result = github.get((repo, day), {})
        posts.append({
            "slot": slot, "status": status, "repo": repo, "angle": angle, "title": title_for(repo, angle or ""),
            "hook": first_line(body), "body": body or "", "url": url, "posted_at": posted_at, "error": error,
            "attempts": attempts, "impressions": impressions, "engagements": engagements,
            "visitors": result.get("visitors"), "stars": result.get("stars"), "try_clicks": result.get("try_clicks"),
        })
    return posts


def github_section(db, now: datetime) -> dict:
    latest_day = db.execute("SELECT MAX(day) FROM github_signals").fetchone()[0]
    since = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    clicks_30d = dict(db.execute(
        "SELECT repo, SUM(clicks) FROM try_clicks WHERE day>=? GROUP BY repo", (since,)
    ).fetchall())
    last_posted = dict(db.execute(
        "SELECT repo, MAX(posted_at) FROM posts WHERE status='posted' AND repo IS NOT NULL GROUP BY repo"
    ).fetchall())
    post_counts = dict(db.execute(
        "SELECT repo, COUNT(*) FROM posts WHERE status='posted' AND repo IS NOT NULL GROUP BY repo"
    ).fetchall())
    repos = []
    if latest_day:
        for repo, views, uniques, stars in db.execute(
            "SELECT repo, li_views, li_uniques, stars FROM github_signals WHERE day=? ORDER BY stars DESC, repo",
            (latest_day,),
        ):
            repos.append({"repo": repo, "stars": stars, "li_uniques": uniques, "li_views": views,
                          "clicks_30d": clicks_30d.get(repo, 0), "last_posted": last_posted.get(repo),
                          "posts": post_counts.get(repo, 0)})
    daily = db.execute(
        "SELECT day, SUM(stars), SUM(li_uniques) FROM github_signals GROUP BY day ORDER BY day"
    ).fetchall()
    clicks_daily = db.execute("SELECT day, SUM(clicks) FROM try_clicks GROUP BY day ORDER BY day").fetchall()
    return {
        "latest_day": latest_day,
        "repos": repos,
        "stars_series": [{"x": day, "y": stars or 0} for day, stars, _ in daily],
        "visitors_series": [{"x": day, "y": uniques or 0} for day, _, uniques in daily],
        "clicks_series": [{"x": day, "y": clicks or 0} for day, clicks in clicks_daily],
        "clicks_30d_total": sum(clicks_30d.values()),
    }


def learning(db) -> dict:
    records = analytics.performance(db)
    result = {"records": len(records)}
    for key in ("repo", "angle"):
        scorer = analytics.ThompsonScorer(records, key)
        counts: dict[str, int] = {}
        for record in records:
            if record.get(key) is not None:
                counts[record[key]] = counts.get(record[key], 0) + 1
        result[key] = [{"name": name, "score": round(score * 100, 1), "posts": counts.get(name, 0)}
                       for name, score in scorer.ranked()]
    return result


def overview(db, config: dict, now: datetime, plan: list[dict]) -> dict:
    token = ap.load_token()
    days_left = round((token["expires_at"] - time.time()) / 86400) if token else None
    last_tick = ap.kv_get(db, "last_tick")
    next_post = next((entry for entry in plan if entry["kind"] != "cap"), None)
    totals = db.execute(
        "SELECT COALESCE(SUM(impressions), 0), COALESCE(SUM(engagements), 0), COUNT(*) FROM metrics"
    ).fetchone()
    return {
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "mode": config["mode"],
        "paused": ap.PAUSE_FLAG.exists(),
        "token_days_left": days_left,
        "click_tracking": trylinks.load_goatcounter(config, ap.SECRETS_PATH) is not None,
        "posts_this_week": ap.posts_this_week(db, now),
        "max_per_week": config["max_posts_per_week"],
        "posted_total": db.execute("SELECT COUNT(*) FROM posts WHERE status='posted'").fetchone()[0],
        "failed_total": db.execute("SELECT COUNT(*) FROM posts WHERE status='failed'").fetchone()[0],
        "prepared_left": db.execute("SELECT COUNT(*) FROM queue WHERE used=0").fetchone()[0],
        "next_post": next_post,
        "last_tick": last_tick,
        "impressions": totals[0],
        "engagements": totals[1],
        "posts_with_linkedin_stats": totals[2],
        "slots": [f'{slot["weekday"]} {slot["time"]}' for slot in config["slots"]],
        "inbox": str(ap.INBOX),
    }


def log_tail(lines: int = 60) -> list[str]:
    path = ap.ROOT / "autopilot.log"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:][::-1]


def build_data() -> dict:
    config = ap.load_config()
    now = ap.local_now(config).replace(tzinfo=None)
    db = ap.connect_db()
    try:
        plan = forecast(db, config, now)
        return {
            "overview": overview(db, config, now, plan),
            "schedule": plan,
            "history": history(db),
            "github": github_section(db, now),
            "learning": learning(db),
            "log": log_tail(),
        }
    finally:
        db.close()


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.last_request = time.time()
        url = urlparse(self.path)
        if url.path == "/":
            self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/ping":
            self.send(200, PING_REPLY, "text/plain")
        elif url.path == "/api/data":
            try:
                self.send(200, json.dumps(build_data()).encode("utf-8"), "application/json")
            except Exception as error:
                self.send(500, json.dumps({"error": str(error)}).encode("utf-8"), "application/json")
        elif url.path == "/img":
            self.send_image(parse_qs(url.query).get("p", [""])[0])
        else:
            self.send(404, b"Not found", "text/plain")

    def send_image(self, raw_path: str) -> None:
        path = Path(raw_path).resolve()
        allowed = any(path.is_relative_to(root) for root in IMAGE_ROOTS)
        if not allowed or not path.is_file():
            self.send(404, b"Not found", "text/plain")
            return
        self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def open_window(url: str) -> None:
    """Open as a standalone app window (Edge --app), falling back to the default browser."""
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge = next((path for path in candidates if path and Path(path).exists()), None)
    if edge:
        subprocess.Popen([edge, f"--app={url}", "--window-size=1320,920"])
    else:
        webbrowser.open(url)


def run() -> None:
    """Reuse a dashboard that is already running, otherwise start one on the first free port."""
    server = None
    for port in PORTS:
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url + "ping", timeout=2) as reply:
                if reply.read() == PING_REPLY:
                    open_window(url)
                    return
        except Exception:
            pass  # nothing of ours here
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue  # port taken by another program
    if server is None:
        raise SystemExit("No free port for the dashboard (8770-8789).")
    server.last_request = time.time()

    def stop_when_idle() -> None:
        while True:
            time.sleep(30)
            if time.time() - server.last_request > IDLE_SHUTDOWN_SECONDS:
                server.shutdown()
                return

    threading.Thread(target=stop_when_idle, daemon=True).start()
    open_window(url)
    server.serve_forever()
