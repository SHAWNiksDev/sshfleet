"""Fleet overview table: keyed rows patched in place on every poll cycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.widgets import DataTable

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

_DASH = Text("-", style="dim")


def fmt_uptime(days: float) -> str:
    if days >= 1:
        return f"{days:.1f}d"
    return f"{days * 24:.0f}h"


class FleetTable(DataTable[Any]):
    def __init__(self) -> None:
        super().__init__(id="fleet-table")
        self.cursor_type = "row"
        self.zebra_stripes = True

    def setup(self) -> None:
        for key, label in COLUMNS:
            self.add_column(label, key=key)

    def add_host(self, spec: HostSpec) -> None:
        self.add_row(
            Text(spec.name),
            Text(HostState.PENDING.value, style=_STATE_STYLES[HostState.PENDING]),
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
        cells: dict[str, Any]
        if success is None:
            cells = {
                "state": Text(view.state.value, style=_STATE_STYLES[view.state]),
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
                "state": Text(view.state.value, style=_STATE_STYLES[view.state]),
                "load1": f"{data.load1:.2f}",
                "cpu": _fmt_pct(success.cpu_used_pct),
                "mem": _fmt_pct(data.mem_used_pct),
                "disk": _fmt_pct(data.disk_used_pct),
                "uptime": fmt_uptime(data.uptime_days),
                "rtt": f"{success.latency_ms:.0f}ms",
            }
        for col_key, value in cells.items():
            try:
                self.update_cell(view.spec.name, col_key, value)
            except Exception:
                continue

    def selected_host(self) -> str | None:
        try:
            cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        value = cell_key.row_key.value
        return str(value) if value is not None else None


def sort_key_for(column: str) -> Callable[[Any], Any]:
    if column == "host":

        def lexical(value: Any) -> str:
            return value.plain if isinstance(value, Text) else str(value)

        return lexical

    suffix = {"cpu": "%", "mem": "%", "disk": "%", "rtt": "ms"}.get(column, "")

    def numeric(value: Any) -> float:
        text = value.plain if isinstance(value, Text) else str(value)
        if not text or text == "-":
            return -1.0
        try:
            return float(text.rstrip(suffix))
        except ValueError:
            return -1.0

    return numeric


def _fmt_pct(value: float | None) -> Text | str:
    if value is None:
        return _DASH
    style = "green" if value < 70 else "yellow" if value < 88 else "bold red"
    return Text(f"{value:.0f}%", style=style)
