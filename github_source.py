"""Read public project facts from GitHub through the already-authenticated gh CLI.

Everything a post says about a project comes from here (description, README
tagline and bullets, topics, releases, commit messages), so posts stay factual.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

CREATE_NO_WINDOW = 0x08000000

# UTF-8 text that was mis-decoded as Windows-1252 and saved again ("â‰¤" instead of "≤").
MOJIBAKE = re.compile(r"[\u00c2-\u00f4][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192\u02c6\u02dc\u2013-\u203a\u20ac\u2122]{1,3}")


def repair_mojibake(text: str) -> str:
    def fix(match: re.Match) -> str:
        try:
            return match.group(0).encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return match.group(0)

    return MOJIBAKE.sub(fix, text)

TRIVIAL_COMMIT = re.compile(
    r"^(merge|bump|typo|chore|wip|format|lint|ci|style|refactor|update readme|readme|docs?:|"
    r"initial commit|cleanup|clean up|minor|fix typo|regenerate|rebuild|deploy)",
    re.IGNORECASE,
)


def gh_api(path: str, raw: bool = False):
    args = ["gh", "api", path]
    if raw:
        args += ["-H", "Accept: application/vnd.github.raw"]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=90,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    if raw:
        return result.stdout
    return json.loads(result.stdout.decode("utf-8"))


@dataclass
class RepoFacts:
    name: str
    description: str
    html_url: str
    homepage: str
    topics: list[str]
    language: str
    pushed_at: str
    license: str
    tagline: str = ""
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    readme_images: list[str] = field(default_factory=list)
    has_ci: bool = False

    @property
    def display_name(self) -> str:
        if self.title:
            return self.title
        if self.name.lower() == self.name and "-" not in self.name:
            return self.name.capitalize()
        return self.name.replace("-", " ")

    def source_text(self) -> str:
        """All grounded text, used to check generated posts don't invent numbers."""
        return " ".join(
            [self.name, self.description, self.tagline, self.homepage, self.html_url]
            + self.bullets
            + self.topics
        )


def list_public_repos(user: str, exclude: list[str]) -> list[dict]:
    repos = gh_api(f"users/{user}/repos?per_page=100&type=owner&sort=pushed") or []
    excluded = {name.lower() for name in exclude}
    return [
        repo
        for repo in repos
        if not repo.get("fork")
        and not repo.get("archived")
        and not repo.get("private")
        and repo["name"].lower() not in excluded
    ]


def _clean_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -*|:")


DESCRIPTIVE_SECTION = re.compile(
    r"about|feature|highlight|what you get|what it does|how it works|overview|why|fitur|use the app|in action",
    re.IGNORECASE,
)
TECHNICAL_SECTION = re.compile(
    r"run|install|setup|deploy|build|structure|architecture|api|badge|config|license|contribut|develop|"
    r"inside|not done|roadmap|changelog|faq|credit|sign-in|upload|connect|settings|requirement|test|layout|files",
    re.IGNORECASE,
)
FILE_TOKEN = re.compile(r"\b[\w.-]+\.(html|js|py|css|json|md|ts|tsx|yml|yaml|env|cfg|toml|bat|sh)\b|\w/[:\s]|^\w+/")
INSTRUCTION = re.compile(
    r"^(install|download|clone|unzip|run|double-click|cd|pip|npm|set |copy |open |click |read the |enter |"
    r"select |press |tap |type |go to |visit |paste |drag |choose )",
    re.IGNORECASE,
)
LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.) (.+)")


def _join_wrapped_lines(readme: str) -> list[str]:
    """Markdown list items often wrap onto the next line; glue them back together."""
    lines: list[str] = []
    for line in readme.splitlines():
        continuation = (
            lines
            and LIST_ITEM.match(lines[-1])
            and line.strip()
            and not LIST_ITEM.match(line)
            and not line.lstrip().startswith(("#", "|", "```", "<", ">"))
        )
        if continuation:
            lines[-1] = lines[-1].rstrip() + " " + line.strip()
        else:
            lines.append(line)
    return lines


def _clean_bullet(text: str) -> str:
    text = _clean_markdown(text)
    text = re.sub(r"^[^\w\"'(]+", "", text)  # leading emoji / symbols
    text = re.sub(r"\s*\((see|details|more) (below|above)\)", "", text, flags=re.IGNORECASE)
    return text.strip()


def _title_from_readme(readme: str, repo_name: str) -> str:
    letters = re.sub(r"[^a-z0-9]", "", repo_name.lower())
    candidates = re.findall(r"alt=\"([^\":]+):", readme) + re.findall(r"^# (.+)$", readme, re.M)
    for candidate in candidates:
        clean = _clean_markdown(candidate).split(" - ")[0].split(":")[0].strip()
        if re.sub(r"[^a-z0-9]", "", clean.lower()) == letters:
            return clean
    return ""


def parse_readme(readme: str) -> tuple[str, list[str], list[str]]:
    readme = re.sub(r"<!--.*?-->", "", readme, flags=re.S)

    tagline = ""
    bold_html = re.search(r"<b>(.+?)</b>", readme)
    bold_md = re.search(r"^\*\*(.+?)\*\*", readme, re.M)
    if bold_html:
        tagline = _clean_markdown(bold_html.group(1))
    elif bold_md:
        tagline = _clean_markdown(bold_md.group(1))

    descriptive: list[str] = []
    fallback: list[str] = []
    section = ""
    in_code_block = False
    for line in _join_wrapped_lines(readme):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        heading = re.match(r"^## (.+)", line)
        if heading:
            section = heading.group(1)
            continue
        candidate = ""
        bullet_match = LIST_ITEM.match(line)
        table_match = re.match(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|", line)
        plain_table = re.match(r"^\|\s*([^|*]{3,40}?)\s*\|\s*([^|]{20,})\|", line)
        if bullet_match:
            candidate = _clean_bullet(bullet_match.group(1))
        elif table_match:
            candidate = f"{_clean_bullet(table_match.group(1))}: {_clean_markdown(table_match.group(2))}"
        elif plain_table and not set(plain_table.group(1)) <= set("-: "):
            candidate = f"{_clean_bullet(plain_table.group(1))}: {_clean_markdown(plain_table.group(2))}"
        if not candidate or not 25 <= len(candidate) <= 220:
            continue
        if re.search(r"https?://|\.exe|\.bat|\.zip|\$ |^cd |^python|~/|C:\\", candidate):
            continue
        if FILE_TOKEN.search(candidate) or INSTRUCTION.match(candidate) or candidate.endswith((",", ";", "(", ":")):
            continue
        if DESCRIPTIVE_SECTION.search(section) and candidate not in descriptive:
            descriptive.append(candidate)
        elif section and not TECHNICAL_SECTION.search(section) and candidate not in fallback:
            fallback.append(candidate)
    bullets = descriptive if len(descriptive) >= 4 else descriptive + fallback

    images = []
    for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)|<img[^>]+src=\"([^\"]+)\"", readme):
        path = match.group(1) or match.group(2)
        if path.startswith("http") or "shields.io" in path:
            continue
        if re.search(r"\.(png|jpe?g|gif)$", path, re.I) and path not in images:
            images.append(path)
    return tagline, bullets[:14], images[:4]


def repo_facts(user: str, repo: dict) -> RepoFacts:
    facts = RepoFacts(
        name=repo["name"],
        description=repair_mojibake((repo.get("description") or "").strip()),
        html_url=repo["html_url"],
        homepage=(repo.get("homepage") or "").strip(),
        topics=repo.get("topics") or [],
        language=repo.get("language") or "",
        pushed_at=repo.get("pushed_at") or "",
        license=((repo.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", ""),
    )
    readme_bytes = gh_api(f"repos/{user}/{repo['name']}/readme", raw=True)
    if readme_bytes:
        readme_text = repair_mojibake(readme_bytes.decode("utf-8", errors="replace"))
        facts.tagline, facts.bullets, facts.readme_images = parse_readme(readme_text)
        facts.title = _title_from_readme(readme_text, repo["name"])
    workflows = gh_api(f"repos/{user}/{repo['name']}/contents/.github/workflows")
    facts.has_ci = isinstance(workflows, list) and len(workflows) > 0
    return facts


def latest_release(user: str, name: str) -> dict | None:
    release = gh_api(f"repos/{user}/{name}/releases/latest")
    if not release or "tag_name" not in release:
        return None
    return {
        "tag": release["tag_name"],
        "name": release.get("name") or release["tag_name"],
        "body": release.get("body") or "",
        "published_at": release.get("published_at") or "",
    }


def meaningful_commits(user: str, name: str, since_iso: str) -> list[str]:
    commits = gh_api(f"repos/{user}/{name}/commits?since={since_iso}&per_page=60") or []
    messages: list[str] = []
    for commit in commits:
        first_line = commit["commit"]["message"].splitlines()[0].strip()
        first_line = re.sub(r"^(feat|fix|perf)(\([^)]*\))?:\s*", "", first_line, flags=re.I)
        if len(first_line) < 12 or TRIVIAL_COMMIT.match(first_line):
            continue
        if re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\b(auto|snapshot|data refresh|daily|nightly)\b", first_line, re.I):
            continue  # bot/cron commits, not human progress
        sentence = first_line[0].upper() + first_line[1:]
        if sentence not in messages:
            messages.append(sentence)
    return messages


def release_notes_bullets(body: str) -> list[str]:
    bullets = []
    for line in body.splitlines():
        match = re.match(r"^\s*[-*] (.+)", line)
        if match:
            text = _clean_markdown(match.group(1))
            if 12 <= len(text) <= 200 and not re.search(r"https?://", text):
                bullets.append(text)
    return bullets[:6]
