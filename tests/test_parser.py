from __future__ import annotations

import pytest

from sshfleet.parser import ProbeParseError, parse_probe_output
from tests.conftest import probe_output


def test_full_payload_parses_all_metrics() -> None:
    data = parse_probe_output(probe_output())

    assert data.hostname == "web-01"
    assert (data.load1, data.load5, data.load15) == (0.42, 0.35, 0.30)
    assert data.mem_used_pct == pytest.approx(50.0)
    assert data.disk_used_pct == pytest.approx(53.0)
    assert data.uptime_days == pytest.approx(4.0, abs=1e-3)
    assert data.cores == 4


def test_missing_cores_section_is_tolerated() -> None:
    payload = probe_output(cores=None)
    data = parse_probe_output(payload)
    assert data.cores is None


def test_garbage_cores_section_is_tolerated() -> None:
    data = parse_probe_output(probe_output(cores=0))
    assert data.cores is None


def test_cpu_idle_includes_iowait() -> None:
    data = parse_probe_output(probe_output())
    counters = [123_987, 1_002, 34_211, 8_123_456, 45_678, 0, 1_234, 0, 0, 0]
    assert data.cpu_times.total == sum(counters)
    assert data.cpu_times.idle == 8_123_456 + 45_678


def test_missing_section_is_reported_by_name() -> None:
    payload = probe_output().replace("@@mem\n", "").replace("MemTotal:       16384 kB\n", "")
    with pytest.raises(ProbeParseError, match="missing section 'mem'"):
        parse_probe_output(payload)


def test_empty_hostname_rejected() -> None:
    payload = probe_output(hostname="   ")
    with pytest.raises(ProbeParseError, match="hostname"):
        parse_probe_output(payload)


def test_loadavg_with_missing_fields_fails() -> None:
    payload = probe_output(loadavg_text="0.42 0.35")
    with pytest.raises(ProbeParseError, match="loadavg"):
        parse_probe_output(payload)


def test_df_without_root_mount_fails() -> None:
    good = probe_output()
    bad = good.replace("      53% /", "      53% /srv")
    with pytest.raises(ProbeParseError, match="mountpoint '/'"):
        parse_probe_output(bad)


def test_non_numeric_loadavg_fails() -> None:
    payload = probe_output(loadavg_text="high medium low")
    with pytest.raises(ProbeParseError, match="loadavg"):
        parse_probe_output(payload)


def test_guest_cpu_time_is_not_counted_twice() -> None:
    payload = probe_output().split("@@cpu\n")[0] + "@@cpu\ncpu 100 20 30 400 50 6 7 8 40 10\n"
    data = parse_probe_output(payload)
    assert data.cpu_times.total == 621
    assert data.cpu_times.idle == 450


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-0.1"])
def test_invalid_load_values_are_rejected(value: str) -> None:
    with pytest.raises(ProbeParseError, match="loadavg"):
        parse_probe_output(probe_output(loadavg_text=f"0.1 {value} 0.3"))


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-1", "broken"])
def test_invalid_uptime_is_reported_as_parse_error(value: str) -> None:
    payload = probe_output().replace("345600.25 1331200.10", f"{value} 1331200.10")
    with pytest.raises(ProbeParseError, match="uptime"):
        parse_probe_output(payload)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_nonfinite_disk_usage_is_rejected(value: str) -> None:
    payload = probe_output().replace("53% /", f"{value}% /")
    with pytest.raises(ProbeParseError, match="df"):
        parse_probe_output(payload)


@pytest.mark.parametrize("line", ["cpu0 1 2 3 4", "cpu 1 2 3 -4"])
def test_invalid_cpu_aggregate_is_rejected(line: str) -> None:
    payload = probe_output().split("@@cpu\n")[0] + f"@@cpu\n{line}\n"
    with pytest.raises(ProbeParseError, match=r"cpu|counter"):
        parse_probe_output(payload)
