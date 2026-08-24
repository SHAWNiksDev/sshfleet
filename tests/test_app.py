from __future__ import annotations

import asyncio
import time

import pytest

from sshfleet.app import FleetApp
from sshfleet.config import Defaults, Settings
from sshfleet.demo import DemoProber, demo_hosts
from sshfleet.models import HostState
from sshfleet.screens.detail import HostDetailScreen
from sshfleet.screens.help import _KEYS, HelpScreen
from sshfleet.widgets.fleet_table import FleetTable


async def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def demo_app(seed: int = 7, interval: float = 3600.0) -> FleetApp:
    hosts = demo_hosts()
    settings = Settings(defaults=Defaults(interval=interval), hosts=hosts)
    return FleetApp(settings, DemoProber(hosts, seed=seed))


async def test_initial_poll_fills_all_states() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        reached = await wait_until(
            lambda: all(
                v.state in (HostState.ONLINE, HostState.UNREACHABLE, HostState.AUTH_ERROR)
                for v in app._views.values()
            )
        )
        assert reached
        await pilot.pause()

        table = app.query_one(FleetTable)
        assert table.row_count == len(demo_hosts())
        states = [v.state for v in app._views.values()]
        assert states.count(HostState.ONLINE) == 8
        assert states.count(HostState.UNREACHABLE) == 1
        assert states.count(HostState.AUTH_ERROR) == 1
        assert "8/10 online" in app.sub_title


async def test_pause_binding_freezes_polling() -> None:
    app = demo_app(interval=0.05)
    async with app.run_test(size=(110, 32)) as pilot:
        assert not app.paused
        await pilot.press("p")
        assert app.paused
        assert "PAUSED" in app.sub_title

        await pilot.press("p")
        assert not app.paused


async def test_detail_screen_opens_and_closes() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: app._online_count > 0)
        await pilot.pause()
        await pilot.press("enter")

        assert isinstance(app.screen, HostDetailScreen)
        title_content = str(app.screen.query_one("#detail-title").render())
        assert "edge-fra-01" in title_content
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HostDetailScreen)


async def test_help_overlay() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        content = app.screen.query_one("HelpScreen Static")
        assert content.region.width > 20, "help content must not collapse"
        assert content.region.height == len(_KEYS)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_sort_by_cpu_column_orders_rows() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: app._online_count >= 8)
        await pilot.pause()

        table = app.query_one(FleetTable)
        table.cursor_coordinate = (0, 3)  # CPU column

        def column_values() -> list[float]:
            out = []
            for row_index in range(table.row_count):
                cell = table.get_cell_at((row_index, 3))
                text = cell.plain if hasattr(cell, "plain") else str(cell)
                out.append(-1.0 if text == "-" else float(text.rstrip("%")))
            return out

        await pilot.press("s")
        assert "▲" in str(table.columns["cpu"].label)
        assert column_values() == sorted(column_values())

        await pilot.press("s")
        assert "▼" in str(table.columns["cpu"].label)
        assert column_values() == sorted(column_values(), reverse=True)


async def test_quit_binding_exits_cleanly() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert not app.is_running


@pytest.mark.parametrize("size", [(80, 24), (140, 40)])
async def test_layout_survives_resize(size: tuple[int, int]) -> None:
    app = demo_app()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        table = app.query_one(FleetTable)
        assert table.region.width <= size[0]
