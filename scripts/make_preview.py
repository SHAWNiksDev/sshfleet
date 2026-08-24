"""Render demo-fleet screenshots into assets/ without opening a terminal.

Usage: python scripts/make_preview.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sshfleet.app import FleetApp
from sshfleet.config import Defaults, Settings
from sshfleet.demo import DemoProber, demo_hosts
from sshfleet.screens.detail import HostDetailScreen

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZE = (112, 34)


async def _settle(app: FleetApp, pilot, cycles: int) -> None:
    loop = asyncio.get_running_loop()
    for _ in range(cycles):
        app.action_poll_now()
        await pilot.pause()
    deadline = loop.time() + 10
    while app._online_count < len(demo_hosts()) - 2:
        if loop.time() > deadline:
            break
        await asyncio.sleep(0.05)


async def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    hosts = demo_hosts()
    settings = Settings(defaults=Defaults(interval=3600), hosts=hosts)

    app = FleetApp(settings, DemoProber(hosts, seed=20260824))
    async with app.run_test(size=SIZE) as pilot:
        await _settle(app, pilot, cycles=3)
        (ASSETS / "preview.svg").write_text(app.export_screenshot(), encoding="utf-8")

        table = app.query_one("FleetTable")
        name = table.selected_host() or hosts[0].name
        app.push_screen(HostDetailScreen(app, name))
        await pilot.pause(0.2)
        (ASSETS / "preview-detail.svg").write_text(app.export_screenshot(), encoding="utf-8")

    print(f"written: {ASSETS / 'preview.svg'}")
    print(f"written: {ASSETS / 'preview-detail.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
