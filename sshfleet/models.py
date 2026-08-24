from __future__ import annotations

import enum
from dataclasses import dataclass, field


class HostState(enum.StrEnum):
    PENDING = "pending"
    CONNECTING = "connecting"
    ONLINE = "online"
    UNREACHABLE = "unreachable"
    AUTH_ERROR = "auth-error"


@dataclass(frozen=True, slots=True)
class HostSpec:
    name: str
    host: str
    user: str | None = None
    port: int = 22
    identity_file: str | None = None
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CpuTimes:
    idle: int
    total: int

    def used_pct_since(self, earlier: CpuTimes) -> float | None:
        total_delta = self.total - earlier.total
        if total_delta <= 0:
            return None
        idle_delta = self.idle - earlier.idle
        busy = 1.0 - idle_delta / total_delta
        return max(0.0, min(100.0, busy * 100))


@dataclass(frozen=True, slots=True)
class ProbeData:
    hostname: str
    load1: float
    load5: float
    load15: float
    cpu_times: CpuTimes
    mem_used_pct: float
    disk_used_pct: float
    uptime_days: float


@dataclass(frozen=True, slots=True)
class ProbeSuccess:
    data: ProbeData
    cpu_used_pct: float | None
    latency_ms: float


@dataclass(frozen=True, slots=True)
class ProbeFailure:
    state: HostState
    message: str


ProbeResult = ProbeSuccess | ProbeFailure


@dataclass(slots=True)
class HostView:
    spec: HostSpec
    state: HostState = HostState.PENDING
    success: ProbeSuccess | None = None
    error: str | None = None
    updated_at: float | None = None
