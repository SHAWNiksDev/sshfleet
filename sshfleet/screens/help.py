"""Overlay with the keymap, reachable via ``?``."""

from __future__ import annotations

from typing import ClassVar

from rich.table import Table as RichTable
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_KEYS: tuple[tuple[str, str], ...] = (
    ("r", "poll every host right now"),
    ("p", "pause / resume background polling"),
    ("enter", "open detail view for selected host"),
    ("s", "sort by column under cursor (toggles direction)"),
    ("?", "this help"),
    ("esc", "close overlay"),
    ("q", "quit"),
)


def build_table() -> RichTable:
    table = RichTable.grid(padding=(0, 3))
    table.add_column(style="bold cyan")
    table.add_column()
    for key, description in _KEYS:
        table.add_row(key, description)
    return table


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,q,question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(build_table())
