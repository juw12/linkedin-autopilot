"""ReachPilot hub: brings the private modules into the dashboard.

Modules live in modules/ (git-ignored, personal data) and keep running on their own schedules:
  jobs         company job boards -> scored tracker, briefs, autofill apply sessions
  email_apply  JobStreet / LinkedIn ads with a recruiter e-mail -> CV + cover letter via the Gmail relay
  job_digest   daily digest + Gmail relay credentials used by email_apply
  promote      (planned) promotion / UGC e-mails for projects

This file only reads their files, builds the "what I need from you" list, and runs a short whitelist of actions.
Every reader is defensive: a missing or half-written file shows as "not set up" instead of breaking the dashboard.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULES = ROOT / "modules"
JOBS = MODULES / "jobs"
EMAIL = MODULES / "email_apply"
DIGEST = MODULES / "job_digest"
PROMOTE = MODULES / "promote"
TASK_FOLDER = "\\ReachPilot\\"
TASKS = {
    "linkedin": "LinkedIn posts",
    "jobs": "Job search",
    "email": "Email applications",
    "bigtech": "Big Tech watch",
}
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
PYTHON = Path(sys.executable).with_name("python.exe")
NO_WINDOW = 0x08000000

_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, seconds: float, build):
    """Small time-based cache so the 60-second dashboard refresh stays cheap."""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < seconds:
        return hit[1]
    value = build()
    _cache[key] = (now, value)
    return value


def file_age_hours(path: Path) -> float | None:
    return (time.time() - path.stat().st_mtime) / 3600 if path.exists() else None


def stamp(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if path.exists() else None


# ------------------------------------------------------------------ scheduled tasks

def task_status() -> dict:
    """Last/next run and last result of every ReachPilot task (schtasks, cached 2 minutes)."""
    def build():
        result = {}
        for key, name in TASKS.items():
            try:
                output = subprocess.run(
                    ["schtasks", "/query", "/tn", TASK_FOLDER + name, "/fo", "CSV", "/v"],
                    capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
                ).stdout
                rows = list(csv.DictReader(output.splitlines()))
            except Exception:
                rows = []
            if not rows:
                result[key] = {"name": name, "installed": False}
                continue
            row = rows[0]
            last_result = row.get("Last Result", "").strip()
            result[key] = {
                "name": name,
                "installed": True,
                "state": row.get("Status", "").strip(),
                "last_run": _pretty_task_time(row.get("Last Run Time", "")),
                "next_run": _pretty_task_time(row.get("Next Run Time", "")),
                "last_result": last_result,
                "ok": last_result in ("0", "267009", "267011", "267014"),  # success, running, never run, terminated by us
            }
        return result

    return cached("tasks", 120, build)


def _pretty_task_time(raw: str) -> str:
    """Turn schtasks' locale time (e.g. '11/7/2026 12:30:00 PM') into 'Sat 7 Nov · 12:30'."""
    from datetime import datetime as _dt
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return _dt.strptime(raw, fmt).strftime("%a %#d %b · %H:%M")
        except ValueError:
            continue
    return raw


# ------------------------------------------------------------------ jobs module

def jobs_section() -> dict:
    tracker = JOBS / "jobs_tracker.xlsx"
    if not tracker.exists():
        return {"installed": False}

    def build():
        from openpyxl import load_workbook

        sheet = load_workbook(tracker)["Jobs"]
        headers = [cell.value for cell in sheet[1]]
        rows = []
        for cells in sheet.iter_rows(min_row=2):
            row = dict(zip(headers, (cell.value for cell in cells)))
            link = cells[headers.index("Link")]
            row["Link"] = link.hyperlink.target if link.hyperlink else None
            rows.append(row)

        today = date.today()
        week_ago = today - timedelta(days=7)

        def as_date(value):
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            try:
                return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        def brief(row):
            return {
                "score": row.get("Score"), "company": row.get("Company"), "title": row.get("Title"),
                "location": row.get("Location"), "status": row.get("Status"), "link": row.get("Link"),
                "notes": row.get("Notes") or "", "my_notes": row.get("My notes") or "",
                "source": str(row.get("Key", "")).split(":", 1)[0],
                "applied_on": str(as_date(row.get("Applied on")) or ""),
            }

        open_rows = [row for row in rows if row.get("Still open") == "Yes"]
        queue = sorted(
            (row for row in open_rows if row.get("Status") in ("New", "Shortlist")),
            key=lambda row: (row.get("Status") != "Shortlist", -(row.get("Score") or 0)),
        )
        applied = [row for row in rows if row.get("Status") in ("Applied", "Interview", "Offer")]
        follow_ups = []
        for row in rows:
            if row.get("Status") != "Applied":
                continue
            applied_on = as_date(row.get("Applied on"))
            if applied_on and applied_on + timedelta(days=7) <= today:
                follow_ups.append(brief(row))
        needs_you = [brief(row) for row in open_rows
                     if re.search(r"not submitted|needs you", str(row.get("My notes") or ""), re.I)
                     and row.get("Status") in ("New", "Shortlist")]
        status_counts: dict[str, int] = {}
        for row in rows:
            status_counts[row.get("Status") or "New"] = status_counts.get(row.get("Status") or "New", 0) + 1

        daily_target = 20
        try:
            daily_target = json.loads((JOBS / "config.json").read_text(encoding="utf-8")).get("daily_target", 20)
        except Exception:
            pass
        todays = queue[:daily_target]
        workday_companies = sorted({row.get("Company") for row in todays if str(row.get("Key", "")).startswith("workday:")})
        blocked_companies = sorted({row.get("Company") for row in todays if str(row.get("Key", "")).startswith("smartrecruiters:")})

        last_log = JOBS / "logs" / "last_run.log"
        summary, last_error = "", ""
        if last_log.exists():
            log_text = last_log.read_text(encoding="utf-8", errors="replace")
            for line in log_text.splitlines():
                if "openings scanned" in line:
                    summary = line.strip()
            if "No network route" in log_text:
                last_error = "The last search couldn't reach the internet (proxy / network down), so no new jobs were added."
            elif "Traceback" in log_text:
                last_error = "The last search crashed; see logs/last_run.log."
        fill_me = []
        answers = JOBS / "my_answers.txt"
        if answers.exists():
            fill_me = [line.split("|")[0].strip() for line in answers.read_text(encoding="utf-8").splitlines()
                       if "FILL ME" in line and "|" in line and not line.startswith("#")]
        return {
            "installed": True,
            "tracked": len(rows),
            "open": len(open_rows),
            "queue_total": len(queue),
            "today": [brief(row) for row in todays],
            "applied_total": len(applied),
            "applied_week": sum(1 for row in applied if (as_date(row.get("Applied on")) or date.min) >= week_ago),
            "applied": sorted((brief(row) for row in applied), key=lambda item: item["applied_on"], reverse=True)[:30],
            "follow_ups": follow_ups,
            "needs_you": needs_you,
            "status_counts": status_counts,
            "workday_companies": workday_companies,
            "blocked_companies": blocked_companies,
            "last_run": stamp(last_log),
            "last_summary": summary,
            "last_error": last_error,
            "fill_me": fill_me,
            "tracker": str(tracker),
        }

    return cached(f"jobs:{tracker.stat().st_mtime}", 60, build)


# ------------------------------------------------------------------ e-mail applications module

def email_section() -> dict:
    log_csv = EMAIL / "applications_log.csv"
    if not log_csv.exists():
        return {"installed": False}

    def build():
        with open(log_csv, encoding="utf-8-sig", newline="") as handle:
            sends = [row for row in csv.DictReader(handle) if row.get("at")]
        today = date.today()
        by_day: dict[str, int] = {}
        regions: dict[str, int] = {}
        for row in sends:
            day = row["at"][:10]
            by_day[day] = by_day.get(day, 0) + 1
            region = row.get("region") or "Other"
            regions[region] = regions.get(region, 0) + 1
        series = []
        for offset in range(13, -1, -1):
            day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            series.append({"x": day, "y": by_day.get(day, 0)})
        week_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")

        leads, leads_total = [], 0
        state_path = EMAIL / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                queued = [item for item in state.get("site_queue", {}).values() if item.get("status") == "queued"]
                leads_total = len(queued)
                leads = sorted(queued, key=lambda item: -(item.get("fit") or 0))[:15]
            except (ValueError, OSError):
                pass

        config = {}
        try:
            config = json.loads((EMAIL / "auto_apply_config.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
        recent = [
            {"at": row["at"].replace("T", " ")[:16], "company": row.get("company"), "title": row.get("title"),
             "region": row.get("region"), "source": row.get("source"), "url": row.get("url"),
             "to_domain": (row.get("to") or "").split("@")[-1]}
            for row in reversed(sends[-40:])
        ]
        return {
            "installed": True,
            "sent_total": len(sends),
            "sent_today": by_day.get(today.strftime("%Y-%m-%d"), 0),
            "sent_week": sum(count for day, count in by_day.items() if day >= week_start),
            "series": series,
            "regions": sorted(regions.items(), key=lambda pair: -pair[1])[:8],
            "recent": recent,
            "leads_total": leads_total,
            "leads": [{"title": item.get("title"), "company": item.get("company"), "region": item.get("region"),
                       "url": item.get("url"), "fit": item.get("fit"), "salary": item.get("salary")} for item in leads],
            "relay_ok": (EMAIL / "relay_v2_ok.flag").exists(),
            "followups_enabled": bool(config.get("followup", {}).get("enabled")),
            "followup_after_days": config.get("followup", {}).get("after_days"),
            "max_per_day": config.get("max_emails_per_day"),
            "report_to": config.get("report_to"),
            "last_run": stamp(EMAIL / "auto_apply.log"),
            "last_run_age_hours": file_age_hours(EMAIL / "auto_apply.log"),
            "bigtech_last_run": stamp(EMAIL / "bigtech_watch.log"),
            "log_tail": tail(EMAIL / "auto_apply.log", 25),
        }

    return cached(f"email:{log_csv.stat().st_mtime}:{int(time.time() // 120)}", 60, build)


def tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 20000))
        text = handle.read().decode("utf-8", errors="replace")
    return text.splitlines()[-lines:][::-1]


# ------------------------------------------------------------------ promote module (planned)

def promote_section() -> dict:
    return {
        "installed": PROMOTE.exists() and any(PROMOTE.iterdir()),
        "planned": [
            "Find creators / communities that fit each project (UGC, newsletters, small YouTubers)",
            "Write a short, personal pitch per contact from the project's real README facts",
            "Send from your Gmail relay with daily caps and one polite follow-up",
            "Track replies and which projects get picked up, next to LinkedIn reach",
        ],
        "folder": str(PROMOTE),
    }


# ------------------------------------------------------------------ what I need from you

def todo_items(linkedin: dict | None, jobs: dict, email: dict, tasks: dict) -> list[dict]:
    items: list[dict] = []

    def add(priority, module, title, detail, action=None, action_label=None, links=None):
        items.append({"priority": priority, "module": module, "title": title, "detail": detail,
                      "action": action, "action_label": action_label, "links": links or []})

    if linkedin:
        days = linkedin.get("token_days_left")
        if days is None:
            add(1, "LinkedIn", "Connect LinkedIn", "Posting is off until you sign in once.", "linkedin_auth", "Sign in to LinkedIn")
        elif days < 10:
            add(1, "LinkedIn", f"LinkedIn login expires in {days} days",
                "LinkedIn only allows 60-day logins for this kind of app. Takes 30 seconds.", "linkedin_auth", "Renew login")
        if linkedin.get("paused"):
            add(2, "LinkedIn", "LinkedIn posting is paused", "No posts go out while the PAUSE switch is on.",
                "linkedin_toggle_pause", "Resume posting")
        if linkedin.get("failed_total"):
            add(2, "LinkedIn", f"{linkedin['failed_total']} LinkedIn post(s) failed", "See the Log tab for the reason.")
        if linkedin.get("mode") == "review" and linkedin.get("awaiting"):
            add(1, "LinkedIn", "A LinkedIn draft is waiting for your OK", "Mode is 'review', so it won't post until approved.",
                "linkedin_approve", "Approve draft")

    if jobs.get("installed"):
        if jobs.get("last_error"):
            add(1, "Jobs", "Today's job search failed", jobs["last_error"] + " It retries at the next slot, or run it now.",
                "jobs_search_now", "Search now")
        if jobs["needs_you"]:
            add(1, "Jobs", f"{len(jobs['needs_you'])} application(s) stopped and need you",
                "Usually an e-mail verification code or a question only you can answer. Open the apply session to finish them.",
                "jobs_apply", "Start apply session",
                [{"label": f"{job['company']} - {job['title']}", "url": job["link"], "note": job["my_notes"]} for job in jobs["needs_you"]])
        if jobs["today"]:
            add(2, "Jobs", f"{len(jobs['today'])} jobs ready to apply today",
                "The form fills itself (CV, cover letter, answers). You check it and press Submit, about 30 seconds each.",
                "jobs_apply", "Start apply session")
        if jobs["workday_companies"]:
            add(3, "Jobs", "One-time sign-in on Workday sites",
                "These companies use Workday: the first time, click 'Sign in with Google' in the apply session. After that it remembers you: "
                + ", ".join(jobs["workday_companies"]))
        if jobs["blocked_companies"]:
            add(3, "Jobs", "Some sites block automation",
                "SmartRecruiters jobs (" + ", ".join(jobs["blocked_companies"]) + ") open in your normal Chrome with the cover letter ready to paste.")
        if jobs["follow_ups"]:
            add(2, "Jobs", f"{len(jobs['follow_ups'])} application(s) are a week old",
                "Worth a short follow-up or a status check on the company site.", None, None,
                [{"label": f"{job['company']} - {job['title']} (applied {job['applied_on']})", "url": job["link"]} for job in jobs["follow_ups"][:10]])
        if jobs["fill_me"]:
            add(1, "Jobs", "Some answers are still empty", "Fill: " + ", ".join(jobs["fill_me"]), "open_answers", "Open my answers")

    if email.get("installed"):
        if not email["relay_ok"]:
            add(1, "Email", "Gmail sending is not confirmed", "Run one test send so real applications can go out.", "email_test", "Send test e-mail")
        if email.get("last_run_age_hours") is not None and email["last_run_age_hours"] > 6:
            add(2, "Email", "E-mail applications haven't run for a while",
                f"Last activity {email['last_run']}. Your PC may have been off; it catches up automatically, or run it now.",
                "email_run_now", "Run now")
        if email["leads_total"]:
            add(3, "Email", f"{email['leads_total']} good jobs have no recruiter e-mail",
                "They need applying on the company site. The leads page lists them best-fit first.", "email_open_leads", "Open leads page")

    for key, task in tasks.items():
        if task.get("installed") and not task.get("ok") and task.get("last_result") not in ("", None):
            add(2, "System", f"'{task['name']}' last run reported an error (code {task['last_result']})",
                "Check the module's log; it will retry on its next run.")
        if not task.get("installed"):
            add(2, "System", f"Scheduled task '{task['name']}' is missing", "That automation won't run on its own until it's re-installed.")

    return sorted(items, key=lambda item: item["priority"])


# ------------------------------------------------------------------ help text

def help_sections(tasks: dict) -> list[dict]:
    def when(key, fallback):
        task = tasks.get(key, {})
        return f"Next run: {task['next_run']}" if task.get("next_run") and task.get("installed") else fallback

    return [
        {"module": "LinkedIn", "runs": "Every 30 minutes; posts Tue/Wed/Thu 08:00. " + when("linkedin", ""),
         "auto": "Picks a project, writes the post from real README facts, adds screenshots, posts through the official API, learns from clicks and stars.",
         "you": "Renew the LinkedIn login every 60 days (you get a reminder here and a Windows notification)."},
        {"module": "Jobs", "runs": "Searches company job boards at 07:30 and 12:30. " + when("jobs", ""),
         "auto": "Finds roles that fit your CV (37 companies, SEA/TW/CN), scores them, writes cover letters, keeps the tracker. The apply session fills each form for you.",
         "you": "Open the apply session, glance at each form, press Submit and type an e-mailed code if a site asks. Sites with a human check never get submitted by the bot."},
        {"module": "Email", "runs": "E-mail applications every hour; Big Tech watch every 4 hours. " + when("email", ""),
         "auto": "Reads JobStreet and LinkedIn ads, and when an ad lists a recruiter e-mail it sends your CV and a tailored letter from your sending Gmail; replies go to your personal inbox. One follow-up after 7 days if no reply, plus a daily report.",
         "you": "Reply to recruiters who answer. Jobs without an e-mail land on the leads page for you to apply on the site."},
        {"module": "Promote", "runs": "Not built yet.",
         "auto": "Planned: promotion and UGC outreach e-mails for your projects, tracked next to LinkedIn reach.",
         "you": "Tell Vector when you're ready and it plugs into this tab."},
    ]


# ------------------------------------------------------------------ actions (whitelist only)

def _console(command: list[str], cwd: Path) -> None:
    """Open a visible console window for things that need you (sign-in, apply session)."""
    subprocess.Popen(command, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)


def _start_task(key: str) -> None:
    subprocess.run(["schtasks", "/run", "/tn", TASK_FOLDER + TASKS[key]], capture_output=True, creationflags=NO_WINDOW)
    _cache.pop("tasks", None)


def run_action(name: str) -> str:
    import autopilot as ap

    if name == "jobs_apply":
        _console(["cmd", "/k", str(JOBS / "autofill.bat")], JOBS)
        return "Apply session opened in a new window."
    if name == "jobs_search_now":
        _start_task("jobs")
        return "Job search started; new jobs appear in a few minutes."
    if name == "jobs_open_tracker":
        os.startfile(JOBS / "jobs_tracker.xlsx")
        return "Tracker opened."
    if name == "open_answers":
        os.startfile(JOBS / "my_answers.txt")
        return "Answers file opened."
    if name == "email_run_now":
        _start_task("email")
        return "E-mail applications started in the background."
    if name == "email_open_leads":
        os.startfile(EMAIL / "leads_to_apply.html")
        return "Leads page opened."
    if name == "email_preview":
        _console([str(PYTHON), "auto_apply.py", "--dry-run"], EMAIL)
        return "Dry run started; it writes outbox_preview.html and sends nothing."
    if name == "email_test":
        _console([str(PYTHON), "auto_apply.py", "--test"], EMAIL)
        return "Test send started in a new window."
    if name == "linkedin_auth":
        _console([str(PYTHON), "autopilot.py", "auth"], ROOT)
        return "LinkedIn sign-in opened."
    if name == "linkedin_approve":
        _console([str(PYTHON), "autopilot.py", "approve"], ROOT)
        return "Approval window opened."
    if name == "linkedin_run_now":
        _start_task("linkedin")
        return "LinkedIn check started."
    if name == "linkedin_toggle_pause":
        if ap.PAUSE_FLAG.exists():
            ap.PAUSE_FLAG.unlink()
            return "LinkedIn posting resumed."
        ap.PAUSE_FLAG.write_text("paused from ReachPilot dashboard")
        return "LinkedIn posting paused."
    if name == "open_modules":
        os.startfile(MODULES)
        return "Modules folder opened."
    raise ValueError(f"Unknown action: {name}")


# ------------------------------------------------------------------ everything for the dashboard

def build(linkedin_overview: dict | None) -> dict:
    tasks = task_status()
    sections = {}
    for key, reader in (("jobs", jobs_section), ("email", email_section), ("promote", promote_section)):
        try:
            sections[key] = reader()
        except Exception as error:  # one broken module must not take the dashboard down
            sections[key] = {"installed": False, "error": f"{type(error).__name__}: {error}"}
    return {
        **sections,
        "tasks": tasks,
        "todo": todo_items(linkedin_overview, sections["jobs"], sections["email"], tasks),
        "help": help_sections(tasks),
    }
