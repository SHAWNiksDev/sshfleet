"""Fleet overview table: keyed rows patched in place on every poll cycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import DataTable
from textual.widgets.data_table import CellDoesNotExist, ColumnKey, RowDoesNotExist

from sshfleet.models import HostSpec, HostState, HostView

COLUMNS: tuple[tuple[str, str], ...] = (
    ("host", "HOST"),
    ("state", "STATE"),
    ("load1", "LOAD 1M"),
    ("cpu", "CPU"),
    ("mem", "MEM"),
    ("disk", "DISK /"),
    ("uptime", "UPTIME"),
    ("rtt", "RTT"),
)

_STATE_STYLES = {
    HostState.PENDING: "dim",
    HostState.CONNECTING: "yellow",
    HostState.ONLINE: "green",
    HostState.UNREACHABLE: "red",
    HostState.AUTH_ERROR: "bold magenta",
}

# Sort order for the state column: healthy first, dead last.
_STATE_RANK = {
    HostState.ONLINE: 0,
    HostState.CONNECTING: 1,
    HostState.PENDING: 2,
    HostState.UNREACHABLE: 3,
    HostState.AUTH_ERROR: 4,
}

_DASH = Text("-", style="dim")


class CellText(Text):
    """Table cell that remembers the raw value the column is sorted by."""

    def __init__(self, text: str, *, sort_value: float | str, style: str = "") -> None:
        super().__init__(text, style=style)
        self.sort_value = sort_value


def fmt_uptime(days: float) -> str:
    if days >= 1:
        return f"{days:.1f}d"
    return f"{days * 24:.0f}h"


def _state_cell(state: HostState) -> CellText:
    return CellText(state.value, sort_value=_STATE_RANK[state], style=_STATE_STYLES[state])


class FleetTable(DataTable[Any]):
    def __init__(self, warn_pct: float = 70.0, crit_pct: float = 88.0) -> None:
        super().__init__(id="fleet-table")
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._warn_pct = warn_pct
        self._crit_pct = crit_pct
        self._base_labels = dict(COLUMNS)
        self._column_keys: dict[str, ColumnKey] = {}

    def setup(self) -> None:
        for key, label in COLUMNS:
            self._column_keys[key] = self.add_column(label, key=key)

    def set_sort_indicator(self, column: str | None, reverse: bool) -> None:
        for key, label in self._base_labels.items():
            marker = " ▼" if key == column and reverse else " ▲" if key == column else ""
            self.columns[self._column_keys[key]].label = Text(label + marker)
        self.refresh()

    def sort_hosts(self, column: str, reverse: bool) -> None:
        """Sort by column, keeping the cursor on the same host row."""
        selected = self.selected_host()
        self.sort(column, key=sort_key_for(column), reverse=reverse)
        self.set_sort_indicator(column, reverse)
        if selected is None:
            return
        try:
            row_index = self.get_row_index(selected)
        except RowDoesNotExist:
            return
        self.cursor_coordinate = Coordinate(row_index, self.cursor_column)

    def add_host(self, spec: HostSpec) -> None:
        self.add_row(
            CellText(spec.name, sort_value=spec.name),
            _state_cell(HostState.PENDING),
            _DASH,
            _DASH,
            _DASH,
            _DASH,
            _DASH,
            _DASH,
            key=spec.name,
        )

    def apply(self, view: HostView) -> None:
        success = view.success
        stale = success is not None and view.state is not HostState.ONLINE
        cells: dict[str, Any]
        if success is None:
            cells = {
                "state": _state_cell(view.state),
                "load1": _DASH,
                "cpu": _DASH,
                "mem": _DASH,
                "disk": _DASH,
                "uptime": _DASH,
                "rtt": _DASH,
            }
        else:
            data = success.data
            cells = {
                "state": _state_cell(view.state),
                "load1": self._fmt_load(data.load1, data.cores, stale),
                "cpu": self._fmt_pct(success.cpu_used_pct, stale),
                "mem": self._fmt_pct(data.mem_used_pct, stale),
                "disk": self._fmt_pct(data.disk_used_pct, stale),
                "uptime": self._fmt_uptime_cell(data.uptime_days, stale),
                "rtt": self._fmt_rtt(success.latency_ms, stale),
            }
        for col_key, value in cells.items():
            try:
                self.update_cell(view.spec.name, col_key, value)
            except CellDoesNotExist:
                continue

    def selected_host(self) -> str | None:
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        value = cell_key.row_key.value
        return str(value) if value is not None else None

    def _fmt_load(self, load1: float, cores: int | None, stale: bool = False) -> Text:
        if stale:
            style = "dim"
        elif cores is None:
            style = ""
        else:
            style = self._style_for(load1 / cores * 100)
        return CellText(f"{load1:.2f}", sort_value=load1, style=style)

    def _fmt_pct(self, value: float | None, stale: bool = False) -> Text:
        if value is None:
            return _DASH
        style = "dim" if stale else self._style_for(value)
        return CellText(f"{value:.0f}%", sort_value=value, style=style)

    def _fmt_uptime_cell(self, days: float, stale: bool) -> Text:
        return CellText(fmt_uptime(days), sort_value=days, style="dim" if stale else "")

    def _fmt_rtt(self, latency_ms: float, stale: bool) -> Text:
        return CellText(f"{latency_ms:.0f}ms", sort_value=latency_ms, style="dim" if stale else "")

    def _style_for(self, pct: float) -> str:
        if pct >= self._crit_pct:
            return "bold red"
        if pct >= self._warn_pct:
            return "yellow"
        return "green"


def _cell_sort_value(value: Any) -> float | str:
    if isinstance(value, CellText):
        return value.sort_value
    return value.plain if isinstance(value, Text) else str(value)


def sort_key_for(column: str) -> Callable[[Any], Any]:
    if column == "host":

        def lexical(value: Any) -> str:
            raw = _cell_sort_value(value)
            return raw if isinstance(raw, str) else str(raw)

        return lexical

    def numeric(value: Any) -> float:
        raw = _cell_sort_value(value)
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            return float(str(raw))
        except ValueError:
            return -1.0

    return numeric
