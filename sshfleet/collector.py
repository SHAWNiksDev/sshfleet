"""SSH probing layer: one persistent connection per host, bounded concurrency.

Each poll cycle runs a single compound command per host and measures its
round-trip time. Transient transport failures trigger one immediate reconnect
before the host is marked unreachable; authentication failures never retry.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import asyncssh

from sshfleet.config import Defaults
from sshfleet.models import (
    CpuTimes,
    HostSpec,
    HostState,
    ProbeFailure,
    ProbeResult,
    ProbeSuccess,
)
from sshfleet.parser import ProbeParseError, parse_probe_output

PROBE_COMMAND = (
    "echo @@host; uname -n; "
    "echo @@load; cat /proc/loadavg; "
    "echo @@cores; nproc 2>/dev/null || getconf _NPROCESSORS_ONLN; "
    "echo @@mem; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo; "
    "echo @@disk; df -kP /; "
    "echo @@up; cat /proc/uptime; "
    "echo @@cpu; head -n 1 /proc/stat"
)


class SupportsRun(Protocol):
    async def run(self, command: str) -> Any: ...


Connector = Callable[..., Awaitable[SupportsRun]]


class FleetCollector:
    def __init__(self, defaults: Defaults, *, connector: Connector | None = None) -> None:
        self._defaults = defaults
        self._connector: Connector = connector if connector is not None else asyncssh.connect
        self._connections: dict[str, SupportsRun] = {}
        self._prev_cpu: dict[str, CpuTimes] = {}
        self._gate = asyncio.Semaphore(defaults.max_concurrent)

    async def probe(self, spec: HostSpec) -> ProbeResult:
        async with self._gate:
            failure = await self._attempt(spec)
            if isinstance(failure, ProbeFailure):
                return failure
            return await self._collect(spec, fresh=False)

    async def close(self) -> None:
        connections, self._connections = self._connections, {}
        self._prev_cpu.clear()
        for conn in connections.values():
            closer = getattr(conn, "close", None)
            if callable(closer):
                with contextlib.suppress(Exception):
                    closer()
        waiters: list[Awaitable[Any]] = []
        for conn in connections.values():
            wait_closed = getattr(conn, "wait_closed", None)
            if callable(wait_closed):
                waiters.append(wait_closed())
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _attempt(self, spec: HostSpec) -> ProbeFailure | None:
        """Prime a connection so auth problems surface before measurement."""
        try:
            await self._acquire(spec)
        except asyncssh.PermissionDenied as exc:
            await self._drop(spec.name)
            return ProbeFailure(HostState.AUTH_ERROR, str(exc).strip() or type(exc).__name__)
        except ExceptionGroup as group:  # asyncssh wraps some failures in TaskGroups
            await self._drop(spec.name)
            return _classify_group(group)
        except (OSError, TimeoutError, asyncssh.Error) as exc:
            await self._drop(spec.name)
            return ProbeFailure(HostState.UNREACHABLE, str(exc).strip() or type(exc).__name__)
        return None

    async def _collect(self, spec: HostSpec, *, fresh: bool) -> ProbeResult:
        try:
            conn = await self._acquire(spec)
            started = time.monotonic()
            proc = await asyncio.wait_for(conn.run(PROBE_COMMAND), timeout=self._defaults.timeout)
            latency_ms = (time.monotonic() - started) * 1000.0
            data = parse_probe_output(proc.stdout)
        except asyncssh.PermissionDenied as exc:
            await self._drop(spec.name)
            return ProbeFailure(HostState.AUTH_ERROR, str(exc).strip() or type(exc).__name__)
        except (OSError, TimeoutError, asyncssh.Error) as exc:
            await self._drop(spec.name)
            if not fresh:
                return await self._retry_fresh(spec, cause=exc)
            return ProbeFailure(HostState.UNREACHABLE, str(exc).strip() or type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except ProbeParseError as exc:
            message = f"unexpected probe payload: {exc}"
            stderr = getattr(proc, "stderr", "")
            if proc.exit_status != 0 and stderr:
                message = f"exit {proc.exit_status}: {_trim(stderr)}"
            return ProbeFailure(HostState.UNREACHABLE, message)

        previous = self._prev_cpu.get(spec.name)
        self._prev_cpu[spec.name] = data.cpu_times
        cpu_used_pct = data.cpu_times.used_pct_since(previous) if previous else None
        return ProbeSuccess(data=data, cpu_used_pct=cpu_used_pct, latency_ms=latency_ms)

    async def _retry_fresh(self, spec: HostSpec, *, cause: Exception) -> ProbeResult:
        await self._drop(spec.name)
        primer = await self._attempt(spec)
        if primer is not None:
            detail = f"{cause}; reconnect failed: {primer.message}"[:200]
            return ProbeFailure(primer.state, detail)
        return await self._collect(spec, fresh=True)

    async def _acquire(self, spec: HostSpec) -> SupportsRun:
        existing = self._connections.get(spec.name)
        if existing is not None:
            return existing
        # login_timeout only starts once the TCP connection is established.
        connection = await asyncio.wait_for(
            self._connector(**self._connect_kwargs(spec)), timeout=self._defaults.timeout
        )
        self._connections[spec.name] = connection
        return connection

    async def _drop(self, name: str) -> None:
        self._prev_cpu.pop(name, None)
        conn = self._connections.pop(name, None)
        if conn is None:
            return
        closer = getattr(conn, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    def _connect_kwargs(self, spec: HostSpec) -> dict[str, Any]:
        # Anything left unset is deliberately omitted so that an OpenSSH
        # config file (when imported) can fill in user, port and identities,
        # exactly like the plain `ssh` client would.
        kwargs: dict[str, Any] = {"host": spec.host}
        port = spec.port if spec.port is not None else self._defaults.port
        if port is not None:
            kwargs["port"] = port
        user = spec.user or self._defaults.user
        if user:
            kwargs["username"] = user
        identity = spec.identity_file or self._defaults.identity_file
        if identity:
            kwargs["client_keys"] = [str(identity)]
        password = spec.password or self._defaults.password
        if password:
            kwargs["password"] = password
        if not self._defaults.import_ssh_config:
            kwargs["config"] = None
        elif self._defaults.ssh_config_paths:
            kwargs["config"] = list(self._defaults.ssh_config_paths)
        kwargs["login_timeout"] = self._defaults.timeout
        if not self._defaults.verify_hosts:
            kwargs["known_hosts"] = None
        return kwargs


def _classify_group(group: ExceptionGroup) -> ProbeFailure:
    auth_errors = group.subgroup(asyncssh.PermissionDenied)
    state = HostState.AUTH_ERROR if auth_errors else HostState.UNREACHABLE
    first: Exception = auth_errors or group
    while isinstance(first, ExceptionGroup):
        first = first.exceptions[0]
    return ProbeFailure(state, str(first).strip() or type(first).__name__)


def _trim(text: str, limit: int = 160) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"
