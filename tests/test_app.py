from __future__ import annotations

import asyncio
import time

import pytest
from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from sshfleet.app import FleetApp
from sshfleet.config import Defaults, Settings
from sshfleet.demo import DemoProber, demo_hosts
from sshfleet.models import (
    CpuTimes,
    HostSpec,
    HostState,
    ProbeData,
    ProbeFailure,
    ProbeResult,
    ProbeSuccess,
)
from sshfleet.screens.detail import HostDetailScreen
from sshfleet.screens.help import _KEYS, HelpScreen
from sshfleet.widgets.fleet_table import COLUMNS, CellText, FleetTable

_COLUMN_INDEX = {key: index for index, (key, _) in enumerate(COLUMNS)}


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


class StubProber:
    """Prober whose per-host results can be swapped between cycles."""

    def __init__(self, results: dict[str, ProbeResult]) -> None:
        self.results = results

    async def probe(self, spec: HostSpec) -> ProbeResult:
        return self.results[spec.name]

    async def close(self) -> None:
        pass


class SlowProber:
    """Every probe takes `delay` seconds, longer than the poll interval."""

    def __init__(self, delay: float, result: ProbeResult) -> None:
        self.delay = delay
        self.result = result

    async def probe(self, spec: HostSpec) -> ProbeResult:
        await asyncio.sleep(self.delay)
        return self.result

    async def close(self) -> None:
        pass


def stub_success(mem_used_pct: float = 55.0) -> ProbeSuccess:
    return ProbeSuccess(
        data=ProbeData(
            hostname="stub",
            load1=0.5,
            load5=0.4,
            load15=0.3,
            cpu_times=CpuTimes(idle=100, total=200),
            mem_used_pct=mem_used_pct,
            disk_used_pct=10.0,
            uptime_days=12.0,
            cores=4,
        ),
        cpu_used_pct=11.0,
        latency_ms=42.0,
    )


def sort_value_at(table: FleetTable, row_index: int, column_index: int) -> float | str:
    cell = table.get_cell_at(Coordinate(row_index, column_index))
    if isinstance(cell, CellText):
        return cell.sort_value
    text = cell.plain if hasattr(cell, "plain") else str(cell)
    return -1.0 if text == "-" else text


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


async def test_sort_by_uptime_column_orders_rows() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: app._online_count >= 8)
        await pilot.pause()

        table = app.query_one(FleetTable)
        table.cursor_coordinate = (0, _COLUMN_INDEX["uptime"])
        await pilot.press("s")
        await pilot.pause()

        values = [
            float(sort_value_at(table, r, _COLUMN_INDEX["uptime"])) for r in range(table.row_count)
        ]
        assert values == sorted(values)
        assert "▲" in str(table.columns["uptime"].label)


async def test_sort_by_state_column_orders_rows() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: app._online_count >= 8)
        await pilot.pause()

        table = app.query_one(FleetTable)
        table.cursor_coordinate = (0, _COLUMN_INDEX["state"])
        await pilot.press("s")
        await pilot.pause()

        ranks = [
            float(sort_value_at(table, r, _COLUMN_INDEX["state"])) for r in range(table.row_count)
        ]
        assert ranks == sorted(ranks)
        assert ranks[0] == 0.0  # healthy hosts first


async def test_sort_keeps_cursor_on_same_host() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: app._online_count >= 8)
        await pilot.pause()

        table = app.query_one(FleetTable)
        table.cursor_coordinate = (2, 0)
        selected = table.selected_host()
        await pilot.press("s")  # host ascending
        await pilot.press("s")  # host descending: rows get reordered
        await pilot.pause()

        assert table.selected_host() == selected


async def test_column_header_click_sorts() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: app._online_count >= 8)
        await pilot.pause()

        table = app.query_one(FleetTable)
        event = DataTable.HeaderSelected(table, table._column_keys["cpu"], 3, Text("CPU"))
        app.on_data_table_header_selected(event)
        await pilot.pause()

        assert "▲" in str(table.columns["cpu"].label)
        values = [
            float(sort_value_at(table, r, _COLUMN_INDEX["cpu"])) for r in range(table.row_count)
        ]
        assert values == sorted(values)


async def test_failure_keeps_last_known_metrics_dimmed() -> None:
    hosts = demo_hosts()[:2]
    online = {spec.name: stub_success(55.0) for spec in hosts}
    prober = StubProber(dict(online))
    settings = Settings(defaults=Defaults(interval=3600.0), hosts=hosts)
    app = FleetApp(settings, prober)

    async with app.run_test(size=(110, 32)) as pilot:
        await wait_until(lambda: all(v.state is HostState.ONLINE for v in app._views.values()))
        await pilot.pause()

        prober.results = {**online, hosts[0].name: ProbeFailure(HostState.UNREACHABLE, "boom")}
        app.action_poll_now()
        assert await wait_until(lambda: app._views[hosts[0].name].state is HostState.UNREACHABLE)
        await pilot.pause()

        view = app.get_view(hosts[0].name)
        assert view is not None and view.success is not None

        table = app.query_one(FleetTable)
        row = table.get_row(hosts[0].name)
        assert row[_COLUMN_INDEX["state"]].plain == "unreachable"
        mem_cell = row[_COLUMN_INDEX["mem"]]
        assert isinstance(mem_cell, CellText)
        assert mem_cell.plain == "55%"
        assert mem_cell.sort_value == 55.0
        assert mem_cell.style == "dim"


async def test_slow_cycle_is_not_cancelled_by_timer() -> None:
    hosts = demo_hosts()[:2]
    prober = SlowProber(delay=0.3, result=stub_success())
    settings = Settings(defaults=Defaults(interval=0.05), hosts=hosts)
    app = FleetApp(settings, prober)

    async with app.run_test(size=(110, 32)) as pilot:
        reached = await wait_until(
            lambda: all(v.state is HostState.ONLINE for v in app._views.values())
        )
        assert reached
        await pilot.pause()


def test_detail_screen_uses_configured_thresholds() -> None:
    screen = HostDetailScreen(None, "x", warn_pct=50.0, crit_pct=80.0)  # type: ignore[arg-type]
    assert screen._colored_pct(30).style == "green"
    assert screen._colored_pct(60).style == "yellow"
    assert screen._colored_pct(90).style == "bold red"
    assert screen._colored_pct(None).plain == "-"


async def test_arrow_keys_choose_sort_column() -> None:
    app = demo_app()
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("right", "right", "right")
        table = app.query_one(FleetTable)
        assert table.cursor_column == _COLUMN_INDEX["cpu"]
        await pilot.press("s")
        assert "▲" in str(table.columns["cpu"].label)
        await pilot.press("left")
        assert table.cursor_column == _COLUMN_INDEX["load1"]


@pytest.mark.parametrize("reverse", [False, True])
async def test_sort_updates_after_poll_and_preserves_selection(reverse: bool) -> None:
    hosts = (HostSpec("z", "z.test"), HostSpec("a", "a.test"))
    prober = StubProber({"z": stub_success(20), "a": stub_success(80)})
    app = FleetApp(Settings(Defaults(interval=3600), hosts), prober)

    async with app.run_test() as pilot:
        assert await wait_until(lambda: app._online_count == 2)
        await pilot.pause()
        table = app.query_one(FleetTable)
        assert [table.get_row_at(i)[0].plain for i in range(2)] == ["a", "z"]
        assert "▲" in str(table.columns["host"].label)
        app._sort_by_column("mem")
        if reverse:
            app._sort_by_column("mem")
        selected = table.selected_host()
        prober.results = {"z": stub_success(90), "a": stub_success(10)}
        await app._run_cycle()

        values = [sort_value_at(table, i, _COLUMN_INDEX["mem"]) for i in range(2)]
        assert values == sorted(values, reverse=reverse)
        assert table.selected_host() == selected


@pytest.mark.parametrize("overlay", ["enter", "?"])
async def test_poll_while_overlay_is_open(overlay: str) -> None:
    hosts = demo_hosts()[:2]
    prober = StubProber({host.name: stub_success(20) for host in hosts})
    app = FleetApp(Settings(Defaults(interval=3600), hosts), prober)
    async with app.run_test() as pilot:
        assert await wait_until(lambda: app._online_count == 2)
        await pilot.press(overlay)
        prober.results = {host.name: stub_success(80) for host in hosts}
        await app._run_cycle()
        await pilot.press("escape")
        table = app.query_one(FleetTable)
        assert table.get_row(hosts[0].name)[_COLUMN_INDEX["mem"]].plain == "80%"
