"""Shared fixtures: realistic probe payloads and scripted SSH fakes."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

DISK_BLOCKS = 41_254_400


def probe_output(
    *,
    hostname: str = "web-01",
    loadavg: tuple[float, float, float] = (0.42, 0.35, 0.30),
    mem_total_kib: int = 16_384,
    mem_available_kib: int = 8_192,
    disk_use_pct: int = 53,
    uptime_seconds: float = 345_600.25,
    cpu_idle: int = 8_123_456,
    cpu_total: int = 8_329_568,
    loadavg_text: str | None = None,
) -> str:
    iowait = 45_678
    nice = 1_002
    system = 34_211
    softirq = 1_234
    user = cpu_total - cpu_idle - iowait - nice - system - softirq
    used_blocks = DISK_BLOCKS * disk_use_pct // 100
    available_blocks = DISK_BLOCKS - used_blocks
    load_section = (
        loadavg_text if loadavg_text is not None else f"{loadavg[0]} {loadavg[1]} {loadavg[2]}"
    )
    return (
        f"@@host\n{hostname}\n"
        f"@@load\n{load_section} 2/567 12345\n"
        f"@@mem\nMemTotal:       {mem_total_kib} kB\n"
        f"MemAvailable:   {mem_available_kib} kB\n"
        "@@disk\n"
        "Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
        f"/dev/sda1          {DISK_BLOCKS} {used_blocks:>10} {available_blocks:>9}"
        f"      {disk_use_pct}% /\n"
        f"@@up\n{uptime_seconds} 1331200.10\n"
        f"@@cpu\ncpu  {user} {nice} {system} {cpu_idle} {iowait} 0 {softirq} 0 0 0\n"
    )


@dataclass
class FakeProc:
    stdout: str = ""
    stderr: str = ""
    exit_status: int = 0


@dataclass
class ScriptedConn:
    """Connection whose successive ``run`` calls replay scripted outcomes."""

    outcomes: deque[FakeProc | Exception] = field(default_factory=deque)
    commands: list[str] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        self.outcomes = deque(self.outcomes)

    async def run(self, command: str) -> FakeProc:
        self.commands.append(command)
        if not self.outcomes:
            return FakeProc(stdout=probe_output())
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeFactory:
    """Stands in for ``asyncssh.connect``, handing out scripted connections."""

    conns: dict[str, ScriptedConn]
    kwargs_log: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, **kwargs: Any) -> ScriptedConn:
        self.kwargs_log.append(dict(kwargs))
        return self.conns[str(kwargs["host"])]
