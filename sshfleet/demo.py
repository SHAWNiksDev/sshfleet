"""Synthetic fleet backend for offline exploration and preview rendering."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from sshfleet.models import (
    CpuTimes,
    HostSpec,
    HostState,
    ProbeData,
    ProbeFailure,
    ProbeResult,
    ProbeSuccess,
)


@dataclass(slots=True)
class _HostSim:
    load1: float
    mem_used_pct: float
    disk_used_pct: float
    uptime_days: float
    cpu_idle: float = 90_000.0
    cpu_busy: float = 10_000.0
    latency_ms: float = 20.0


@dataclass(slots=True)
class DemoProber:
    """Implements the collector interface against random-walking fake hosts.

    Host names ending in ``-down`` always answer as unreachable and ``-auth``
    as an authentication failure, which exercises every UI state.
    """

    hosts: Sequence[HostSpec]
    seed: int | None = None
    sims: dict[str, _HostSim] = field(init=False)
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = rng = random.Random(self.seed)
        self.sims = {
            spec.name: _HostSim(
                load1=rng.uniform(0.05, 2.5),
                mem_used_pct=rng.uniform(25, 85),
                disk_used_pct=rng.uniform(20, 75),
                uptime_days=rng.uniform(1, 400),
                latency_ms=rng.uniform(8, 140),
            )
            for spec in self.hosts
        }

    async def probe(self, spec: HostSpec) -> ProbeResult:
        sim = self.sims[spec.name]
        suffix = spec.name.rsplit("-", 1)[-1]
        await asyncio.sleep(min(sim.latency_ms / 1000 * 0.3, 0.05))
        if suffix == "down":
            return ProbeFailure(HostState.UNREACHABLE, "Connection refused by demo")
        if suffix == "auth":
            return ProbeFailure(HostState.AUTH_ERROR, "Permission denied (demo key)")

        rng = self._rng
        sim.load1 = max(0.02, min(sim.load1 + rng.uniform(-0.35, 0.35), 14.0))
        sim.mem_used_pct = max(5.0, min(sim.mem_used_pct + rng.uniform(-1.5, 1.5), 97.0))
        sim.disk_used_pct = max(1.0, min(sim.disk_used_pct + rng.uniform(-0.05, 0.06), 99.0))
        sim.uptime_days += 1 / 86400
        sim.cpu_idle = max(50.0, min(sim.cpu_idle + rng.uniform(-2500, 2500), 99_500.0))
        sim.latency_ms = max(6.0, min(sim.latency_ms + rng.uniform(-12, 12), 300.0))

        busy_share = rng.uniform(0.04, 0.22)
        idle_delta = rng.uniform(800, 2400) * (1.0 - busy_share)
        total_delta = idle_delta / max(1.0 - busy_share, 0.01)
        earlier = CpuTimes(idle=int(sim.cpu_idle), total=int(sim.cpu_idle + sim.cpu_busy))
        current = CpuTimes(
            idle=int(sim.cpu_idle + idle_delta),
            total=int(sim.cpu_idle + sim.cpu_busy + total_delta),
        )
        sim.cpu_idle += idle_delta
        sim.cpu_busy += total_delta - idle_delta

        return ProbeSuccess(
            data=ProbeData(
                hostname=spec.name,
                load1=sim.load1,
                load5=max(sim.load1 * 0.7, 0.01),
                load15=max(sim.load1 * 0.55, 0.01),
                cpu_times=current,
                mem_used_pct=sim.mem_used_pct,
                disk_used_pct=sim.disk_used_pct,
                uptime_days=sim.uptime_days,
            ),
            cpu_used_pct=current.used_pct_since(earlier),
            latency_ms=sim.latency_ms,
        )

    async def close(self) -> None:
        await asyncio.sleep(0)


def demo_hosts() -> tuple[HostSpec, ...]:
    names = (
        "edge-fra-01",
        "edge-fra-02",
        "app-iad-01",
        "app-iad-02",
        "app-sfo-01",
        "db-iad-01",
        "db-sin-01",
        "cache-sin-01",
        "lab-box-down",
        "legacy-ftp-auth",
    )
    return tuple(HostSpec(name=name, host=f"{name}.demo.internal") for name in names)
