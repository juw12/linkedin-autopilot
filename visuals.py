"""Real visuals only: fresh screenshots of live project pages and media from the repos."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from github_source import RepoFacts, gh_api

FONT_DIR = Path("C:/Windows/Fonts")
PALETTES = [
    ("#0f172a", "#1e3a5f"), ("#0b1f4d", "#2563eb"), ("#083d38", "#0f766e"),
    ("#5b0a1f", "#e11d48"), ("#7c2d12", "#f97316"), ("#3d4a2a", "#6b7d4f"),
    ("#042f2e", "#14b8a6"), ("#2e1065", "#7c3aed"),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / "segoeui.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def capture(urls: dict[str, str], out_dir: Path) -> dict[str, Path]:
    """Screenshot several pages in one browser session. Returns name -> png path."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1.5,
            color_scheme="light",
        ).new_page()
        for name, url in urls.items():
            target = out_dir / f"{name}.png"
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(2500)
                page.screenshot(path=str(target))
                results[name] = target
            except Exception:
                continue
        browser.close()
    return results


def browser_frame(screenshot: Image.Image, url: str, width: int) -> Image.Image:
    shot = screenshot.convert("RGB")
    shot = shot.resize((width, int(shot.height * width / shot.width)), Image.LANCZOS)
    bar = 44
    framed = Image.new("RGB", (width, shot.height + bar), "#f1f3f5")
    draw = ImageDraw.Draw(framed)
    for index, color in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        center = 22 + index * 20
        draw.ellipse((center - 6, 16, center + 6, 28), fill=color)
    draw.rounded_rectangle((90, 9, width - 20, 35), radius=13, fill="white")
    draw.text((106, 12), url.replace("https://", "").rstrip("/"), font=_font(16), fill="#495057")
    framed.paste(shot, (0, bar))
    return framed


def compose_framed(screenshot_path: Path, url: str, output: Path, seed: str) -> Path:
    width, height = 1200, 900
    dark, light = PALETTES[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(PALETTES)]
    gradient = Image.linear_gradient("L").resize((width, height)).rotate(-30)
    canvas = Image.composite(Image.new("RGB", (width, height), light), Image.new("RGB", (width, height), dark), gradient)
    frame = browser_frame(Image.open(screenshot_path), url, width - 120)
    frame = frame.crop((0, 0, frame.width, min(frame.height, height - 100)))
    left = (width - frame.width) // 2
    top = (height - frame.height) // 2
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (left + 6, top + 14, left + frame.width + 6, top + frame.height + 14), radius=16, fill=(0, 0, 0, 110)
    )
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(14))).convert("RGB")
    mask = Image.new("L", frame.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, frame.width, frame.height), radius=14, fill=255)
    canvas.paste(frame, (left, top), mask)
    canvas.save(output, quality=92)
    return output


def repo_media(user: str, facts: RepoFacts, out_dir: Path, limit: int = 2) -> list[Path]:
    paths = []
    for relative in facts.readme_images[:limit]:
        clean = relative.lstrip("./")
        data = gh_api(f"repos/{user}/{facts.name}/contents/{clean}", raw=True)
        if not data:
            continue
        suffix = Path(clean).suffix.lower()
        target = out_dir / f"{facts.name}_{Path(clean).stem}{'.gif' if suffix == '.gif' else '.jpg'}"
        if suffix == ".gif":
            target.write_bytes(data)
        else:
            Image.open(io.BytesIO(data)).convert("RGB").save(target, quality=92)
        paths.append(target)
    return paths


def images_for_post(user: str, angle: str, subjects: list[RepoFacts], out_dir: Path) -> list[Path]:
    """Pick real visuals by angle so recycled posts don't reuse the same picture."""
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = {}
    for facts in subjects:
        if angle == "behind_the_build" or not facts.homepage:
            urls[f"{facts.name}_repo"] = facts.html_url
        if facts.homepage:
            urls[f"{facts.name}_site"] = facts.homepage
    shots = capture(urls, out_dir / "raw")

    framed: list[Path] = []
    for name, shot in shots.items():
        owner = next(facts for facts in subjects if name.startswith(facts.name + "_"))
        page_url = owner.homepage if name.endswith("_site") else owner.html_url
        framed.append(compose_framed(shot, page_url, out_dir / f"{name}.jpg", seed=name))

    media: list[Path] = []
    if angle != "roundup":
        media = repo_media(user, subjects[0], out_dir)

    if angle in ("deep_dive", "feedback") and media:
        ordered = media + framed
    else:
        ordered = framed + media
    return ordered[:6]
