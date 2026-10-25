"""SSHFleet application: poll orchestration and screen composition."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, Protocol

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header

from sshfleet.config import Settings
from sshfleet.models import HostSpec, HostState, HostView, ProbeResult, ProbeSuccess
from sshfleet.screens.detail import HostDetailScreen
from sshfleet.screens.help import HelpScreen
from sshfleet.widgets.fleet_table import COLUMNS, FleetTable

_HISTORY_LIMIT = 240


class Prober(Protocol):
    async def probe(self, spec: HostSpec) -> ProbeResult: ...

    async def close(self) -> None: ...


class FleetApp(App[None]):
    TITLE = "SSHFleet"
    CSS_PATH = "theme.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "poll_now", "Poll now"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("s", "sort_by_cursor", "Sort"),
        Binding("enter", "host_details", "Details"),
        Binding("?", "help", "Keys"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings, prober: Prober) -> None:
        super().__init__()
        self._settings = settings
        self._prober = prober
        self._views = {spec.name: HostView(spec=spec) for spec in settings.hosts}
        self._history: dict[str, deque[float]] = {
            spec.name: deque(maxlen=_HISTORY_LIMIT) for spec in settings.hosts
        }
        self._poll_timer: Timer | None = None
        self._paused = False
        self._sort_column = "host"
        self._sort_reverse = False
        self._online_count = 0
        self._cycle_busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield FleetTable(
            warn_pct=self._settings.defaults.warn_pct,
            crit_pct=self._settings.defaults.crit_pct,
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(FleetTable)
        table.setup()
        for view in self._views.values():
            table.add_host(view.spec)
        self.sub_title = f"{len(self._views)} hosts"
        self._poll_timer = self.set_interval(self._settings.defaults.interval, self.action_poll_now)
        self.action_poll_now()

    async def on_unmount(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._prober.close(), timeout=3.0)

    @property
    def paused(self) -> bool:
        return self._paused

    def get_view(self, name: str) -> HostView | None:
        return self._views.get(name)

    def load_history(self, name: str) -> Sequence[float]:
        return tuple(self._history.get(name, ()))

    def action_poll_now(self) -> None:
        # A cycle slower than the interval must not cancel itself mid-flight:
        # skip the tick and let the running cycle finish instead.
        if self._cycle_busy:
            return
        self._start_cycle()

    def action_toggle_pause(self) -> None:
        assert self._poll_timer is not None  # set in on_mount
        self._paused = not self._paused
        if self._paused:
            self._poll_timer.pause()
        else:
            self._poll_timer.resume()
        self._refresh_sub_title()

    def action_sort_by_cursor(self) -> None:
        table = self.query_one(FleetTable)
        index = table.cursor_column
        if not 0 <= index < len(COLUMNS):
            return
        self._sort_by_column(COLUMNS[index][0])

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        column = str(event.column_key.value)
        if column in dict(COLUMNS):
            self._sort_by_column(column)
            event.stop()

    def _sort_by_column(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self.query_one(FleetTable).sort_hosts(column, self._sort_reverse)

    def action_host_details(self) -> None:
        name = self.query_one(FleetTable).selected_host()
        if name and name in self._views:
            self.push_screen(self._detail_screen(name))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name and name in self._views:
            event.stop()
            self.push_screen(self._detail_screen(str(name)))

    def _detail_screen(self, name: str) -> HostDetailScreen:
        return HostDetailScreen(
            self,
            name,
            warn_pct=self._settings.defaults.warn_pct,
            crit_pct=self._settings.defaults.crit_pct,
        )

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @work(exclusive=True)
    async def _start_cycle(self) -> None:
        self._cycle_busy = True
        try:
            await self._run_cycle()
        finally:
            self._cycle_busy = False

    async def _run_cycle(self) -> None:
        views = list(self._views.values())
        for view in views:
            if view.state is not HostState.ONLINE:
                view.state = HostState.CONNECTING
                self.query_one(FleetTable).apply(view)

        results = await asyncio.gather(
            *(self._prober.probe(v.spec) for v in views), return_exceptions=True
        )

        stamped = time.time()
        online = 0
        for view, result in zip(views, results, strict=True):
            # Last-known metrics are kept on failure: the state column reports
            # the outage while the cells show stale values dimmed.
            if isinstance(result, BaseException):
                view.state = HostState.UNREACHABLE
                view.error = f"{type(result).__name__}: {result}"
                view.updated_at = stamped
            elif isinstance(result, ProbeSuccess):
                view.state = HostState.ONLINE
                view.success = result
                view.error = None
                view.updated_at = stamped
                self._history[view.spec.name].append(result.data.load1)
                online += 1
            else:
                view.state = result.state
                view.error = result.message
                view.updated_at = stamped
            self.query_one(FleetTable).apply(view)
        self._online_count = online
        self._refresh_sub_title()

    def _refresh_sub_title(self) -> None:
        total = len(self._views)
        stamp = datetime.now().strftime("%H:%M:%S")
        suffix = " · PAUSED" if self._paused else ""
        self.sub_title = f"{self._online_count}/{total} online · last poll {stamp}{suffix}"
