from __future__ import annotations

import asyncio

import asyncssh
import pytest

from sshfleet.collector import PROBE_COMMAND, FleetCollector
from sshfleet.config import Defaults
from sshfleet.models import HostSpec, HostState
from tests.conftest import FakeFactory, FakeProc, ScriptedConn, probe_output


def spec(name: str = "h1", host: str | None = None) -> HostSpec:
    return HostSpec(name=name, host=host or f"{name}.test")


def collector_with(conn: ScriptedConn, defaults: Defaults | None = None):
    factory = FakeFactory(conns={spec().host: conn})
    return FleetCollector(defaults or Defaults(), connector=factory), factory


async def test_first_probe_reports_metrics_without_cpu_delta() -> None:
    conn = ScriptedConn(outcomes=[FakeProc(stdout=probe_output())])
    collector, factory = collector_with(conn)

    result = await collector.probe(spec())

    assert result.data.hostname == "web-01"
    assert result.cpu_used_pct is None
    assert result.latency_ms >= 0.0
    assert conn.commands == [PROBE_COMMAND]
    assert factory.kwargs_log == [{"host": spec().host, "port": 22, "login_timeout": 8.0}]


async def test_second_probe_computes_cpu_usage() -> None:
    first = probe_output(cpu_idle=8_123_456, cpu_total=8_329_568)
    second = probe_output(cpu_idle=8_153_456, cpu_total=8_369_568)
    conn = ScriptedConn(outcomes=[FakeProc(stdout=first), FakeProc(stdout=second)])
    collector, _ = collector_with(conn)

    await collector.probe(spec())
    result = await collector.probe(spec())

    assert result.cpu_used_pct == pytest.approx(25.0)


async def test_nonzero_exit_status_maps_to_unreachable_with_stderr() -> None:
    conn = ScriptedConn(
        outcomes=[FakeProc(stdout="", stderr="df: /: Permission denied\n", exit_status=1)]
    )
    collector, _ = collector_with(conn)

    result = await collector.probe(spec())

    assert not hasattr(result, "data")
    assert result.state is HostState.UNREACHABLE
    assert "Permission denied" in result.message


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (asyncssh.PermissionDenied("bad key"), HostState.AUTH_ERROR),
        (OSError("network is down"), HostState.UNREACHABLE),
        (TimeoutError(), HostState.UNREACHABLE),
        (asyncssh.Error("proto mismatch", "code=2"), HostState.UNREACHABLE),
    ],
)
async def test_connect_failures_classify_states(error: Exception, expected: HostState) -> None:
    class ExplodingFactory:
        calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            raise error

    factory = ExplodingFactory()
    collector = FleetCollector(Defaults(), connector=factory)

    result = await collector.probe(spec())

    assert result.state is expected
    assert factory.calls == 1


async def test_stale_connection_reconnects_once_and_succeeds() -> None:
    good = FakeProc(stdout=probe_output())
    conn_a = ScriptedConn(outcomes=[good, OSError("connection reset by peer")])
    conn_b = ScriptedConn(outcomes=[FakeProc(stdout=probe_output())])
    factory = FakeFactory(conns={spec().host: conn_a})
    collector = FleetCollector(Defaults(), connector=factory)

    assert hasattr(await collector.probe(spec()), "data")

    factory.conns[spec().host] = conn_b
    second = await collector.probe(spec())

    assert hasattr(second, "data")
    assert conn_a.closed and not conn_b.closed


async def test_auth_failure_never_retries() -> None:
    conn = ScriptedConn(outcomes=[asyncssh.PermissionDenied("nope")])
    factory = FakeFactory(conns={spec().host: conn})
    collector = FleetCollector(Defaults(), connector=factory)

    result = await collector.probe(spec())

    assert result.state is HostState.AUTH_ERROR
    assert len(factory.kwargs_log) == 1


async def test_semaphore_bounds_concurrency() -> None:
    limit = Defaults(max_concurrent=2)
    active = peak = 0

    class SlowConn:
        async def run(self, command: str) -> FakeProc:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return FakeProc(stdout=probe_output())

        def close(self) -> None:
            pass

    conns = {f"host-{i}.test": SlowConn() for i in range(6)}
    factory = FakeFactory(conns=conns)
    collector = FleetCollector(limit, connector=factory)

    specs = [spec(f"h{i}", f"host-{i}.test") for i in range(6)]
    results = await asyncio.gather(*(collector.probe(s) for s in specs))

    assert all(hasattr(r, "data") for r in results)
    assert peak <= 2


async def test_close_shuts_connections_down() -> None:
    conn = ScriptedConn()
    collector, _ = collector_with(conn)
    await collector.probe(spec())

    await collector.close()

    assert conn.closed


async def test_kwargs_include_identity_and_known_hosts_policy(tmp_path) -> None:
    key = tmp_path / "id_test"
    key.write_text("key", encoding="utf-8")
    defaults = Defaults(identity_file=key, verify_hosts=False, user="ops")
    conn = ScriptedConn(outcomes=[FakeProc(stdout=probe_output())])
    factory = FakeFactory(conns={spec().host: conn})
    collector = FleetCollector(defaults, connector=factory)

    await collector.probe(spec())

    sent = factory.kwargs_log[0]
    assert sent["username"] == "ops"
    assert sent["client_keys"] == [str(key)]
    assert sent["known_hosts"] is None
