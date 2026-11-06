"""Render icons/icon.svg to PNGs and a multi-size Windows .ico, so every place uses the same mark.

usage: python icons/render_icons.py [OUT_DIR]   (default: this folder; needs Playwright + Edge)
Also writes preview.png (the mark at 16/24/32/48/64/128 px on light and dark strips).
"""
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ICONS = Path(__file__).resolve().parent
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ICONS
out.mkdir(parents=True, exist_ok=True)
svg = (ICONS / "icon.svg").read_text(encoding="utf-8")
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
renders = {}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 800, "height": 800})
    all_sizes = sorted(set(ICO_SIZES + [192, 512]))
    markup = "".join(
        svg.replace("<svg ", f'<svg id="s{size}" width="{size}" height="{size}" style="display:block;margin:4px" ', 1)
        for size in all_sizes
    )
    page.set_content(f'<html><body style="margin:0;background:transparent">{markup}</body></html>')
    for size in all_sizes:
        path = out / f"_render-{size}.png"
        page.locator(f"#s{size}").screenshot(path=str(path), omit_background=True)
        renders[size] = Image.open(path).convert("RGBA")
    page.close()
    browser.close()

renders[192].save(out / "icon-192.png")
renders[512].save(out / "icon-512.png")
renders[256].save(
    out / "app.ico",
    sizes=[(size, size) for size in ICO_SIZES],
    append_images=[renders[size] for size in ICO_SIZES if size != 256],
)
for path in out.glob("_render-*.png"):
    path.unlink()

preview_sizes = [16, 24, 32, 48, 64, 128]
width = sum(preview_sizes) + 20 * (len(preview_sizes) + 1)
sheet = Image.new("RGBA", (width, 2 * 148), "#ffffff")
sheet.paste(Image.new("RGBA", (width, 148), "#202020"), (0, 148))
for row in range(2):
    x = 20
    for size in preview_sizes:
        sheet.alpha_composite(renders[size if size in renders else 128].resize((size, size)), (x, row * 148 + 10))
        x += size + 20
sheet.save(out / "preview.png")
print("wrote", sorted(path.name for path in out.iterdir()))
