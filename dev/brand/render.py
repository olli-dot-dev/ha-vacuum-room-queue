"""Render brand/icon.png and icon@2x.png from icon.svg (needs playwright + chromium)."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE.parents[1] / "custom_components" / "vacuum_room_queue" / "brand"

svg = (HERE / "icon.svg").read_text()
OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(**({"executable_path": sys.argv[1]} if len(sys.argv) > 1 else {}))
    for scale, name in ((1, "icon.png"), (2, "icon@2x.png")):
        page = browser.new_page(viewport={"width": 256, "height": 256}, device_scale_factor=scale)
        page.set_content(f'<body style="margin:0;background:transparent">{svg}</body>')
        page.locator("svg").screenshot(path=str(OUT / name), omit_background=True)
        page.close()
    browser.close()
print("written to", OUT)
