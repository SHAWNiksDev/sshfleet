"""Per-host drill-down: live stats plus a load-1 sparkline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, Protocol

from rich.table import Table as RichTable
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Sparkline, Static

from sshfleet.models import HostState, HostView

_STATE_STYLES = {
    HostState.PENDING: "dim",
    HostState.CONNECTING: "yellow",
    HostState.ONLINE: "green",
    HostState.UNREACHABLE: "red",
    HostState.AUTH_ERROR: "bold magenta",
}


class FleetStore(Protocol):
    def get_view(self, name: str) -> HostView | None: ...

    def load_history(self, name: str) -> Sequence[float]: ...


class HostDetailScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,q", "dismiss", "Back")]

    def __init__(
        self,
        store: FleetStore,
        name: str,
        *,
        warn_pct: float = 70.0,
        crit_pct: float = 88.0,
    ) -> None:
        super().__init__()
        self._store = store
        self._host_name = name
        self._warn_pct = warn_pct
        self._crit_pct = crit_pct

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-box"):
            yield Static("", id="detail-title")
            yield Sparkline([], id="detail-spark")
            yield Static("", id="detail-stats")
            yield Static("", id="detail-error")

    def on_mount(self) -> None:
        self._refresh_content()
        self.set_interval(1.0, self._refresh_content)

    def _refresh_content(self) -> None:
        view = self._store.get_view(self._host_name)
        if view is None:  # pragma: no cover - host removed mid-session
            self.dismiss()
            return

        spec = view.spec
        peer = spec.host if spec.port is None else f"{spec.host}:{spec.port}"
        user = spec.user or "ssh config / agent"
        self.query_one("#detail-title", Static).update(
            Text.assemble(
                (self._host_name, "bold"),
                (" · ", "dim"),
                (peer, ""),
                (" · ", "dim"),
                (user, "italic"),
            )
        )

        history = list(self._store.load_history(self._host_name))
        spark = self.query_one("#detail-spark", Sparkline)
        spark.data = history[-120:] if history else []

        self.query_one("#detail-stats", Static).update(self._stats_table(view))
        error = self.query_one("#detail-error", Static)
        if view.error:
            error.update(Text(view.error, style="red"))
        else:
            error.update("")

    def _stats_table(self, view: HostView) -> RichTable:
        table = RichTable.grid(padding=(0, 2))
        table.add_column(style="dim", justify="left")
        table.add_column(justify="left")

        table.add_row("state", Text(view.state.value, style=_STATE_STYLES[view.state]))
        success = view.success
        if success is None:
            table.add_row("metrics", Text("no successful probe yet", style="dim"))
            return table

        data = success.data
        table.add_row("hostname", data.hostname)
        load_cell = f"{data.load1:.2f} / {data.load5:.2f} / {data.load15:.2f}"
        if data.cores:
            load_cell += f"  ({data.load1 / data.cores * 100:.0f}% of {data.cores} cores)"
        table.add_row("load 1/5/15", load_cell)
        table.add_row("cpu used", self._colored_pct(success.cpu_used_pct))
        table.add_row("memory", self._colored_pct(data.mem_used_pct))
        table.add_row("disk /", self._colored_pct(data.disk_used_pct))
        table.add_row("uptime", _fmt_uptime_days(data.uptime_days))
        table.add_row("probe rtt", f"{success.latency_ms:.0f} ms")
        return table

    def _colored_pct(self, value: float | None) -> Text:
        if value is None:
            return Text("-", style="dim")
        if value >= self._crit_pct:
            style = "bold red"
        elif value >= self._warn_pct:
            style = "yellow"
        else:
            style = "green"
        return Text(f"{value:.0f}%", style=style)


def _fmt_uptime_days(days: float) -> str:
    whole = int(days)
    hours = int((days - whole) * 24)
    if whole >= 1:
        return f"{whole}d {hours}h"
    return f"{hours}h"
