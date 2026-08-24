"""Parsing of raw probe payloads into typed metric snapshots.

The collector runs a single compound command per host whose output is split
into labeled sections; every function here is pure and trivially testable.
"""

from __future__ import annotations

from sshfleet.models import CpuTimes, ProbeData

SECTION_PREFIX = "@@"


class ProbeParseError(ValueError):
    pass


def parse_probe_output(raw: str) -> ProbeData:
    sections = _split_sections(raw)
    try:
        hostname = sections["host"].strip()
        if not hostname:
            raise ProbeParseError("empty hostname section")
        load1, load5, load15 = _parse_loadavg(sections["load"])
        mem_used_pct = _parse_meminfo(sections["mem"])
        disk_used_pct = _parse_disk(sections["disk"])
        uptime_days = _parse_uptime(sections["up"])
        cpu_times = _parse_stat(sections["cpu"])
    except KeyError as exc:
        raise ProbeParseError(f"missing section {exc.args[0]!r}") from exc

    try:
        cores: int | None = _parse_cores(sections.get("cores", ""))
    except ProbeParseError:
        cores = None

    return ProbeData(
        hostname=hostname,
        load1=load1,
        load5=load5,
        load15=load15,
        cpu_times=cpu_times,
        mem_used_pct=mem_used_pct,
        disk_used_pct=disk_used_pct,
        uptime_days=uptime_days,
        cores=cores,
    )


def _split_sections(raw: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in raw.splitlines():
        if line.startswith(SECTION_PREFIX):
            current = sections.setdefault(line[len(SECTION_PREFIX) :].strip(), [])
            continue
        if current is not None:
            current.append(line)
    return {key: "\n".join(value) for key, value in sections.items()}


def _first_line(block: str, section: str) -> str:
    for line in block.splitlines():
        if line.strip():
            return line.strip()
    raise ProbeParseError(f"section {section!r} has no data")


def _parse_loadavg(block: str) -> tuple[float, float, float]:
    parts = _first_line(block, "load").split()
    try:
        values = tuple(float(p) for p in parts[:3])
    except ValueError as exc:
        raise ProbeParseError(f"malformed loadavg: {parts[:3]}") from exc
    if len(values) != 3:
        raise ProbeParseError(f"loadavg needs 3 fields, got {len(values)}")
    return values[0], values[1], values[2]


def _parse_meminfo(block: str) -> float:
    totals: dict[str, int] = {}
    for line in block.splitlines():
        key, _, rest = line.partition(":")
        key = key.strip()
        if key in {"MemTotal", "MemAvailable"}:
            fields = rest.split()
            if not fields:
                raise ProbeParseError(f"meminfo field {key!r} without value")
            try:
                totals[key] = int(fields[0])
            except ValueError as exc:
                raise ProbeParseError(f"non-numeric meminfo value for {key}") from exc
    total = totals.get("MemTotal")
    available = totals.get("MemAvailable")
    if total is None or available is None:
        raise ProbeParseError("MemTotal/MemAvailable missing from meminfo")
    if total <= 0:
        raise ProbeParseError(f"implausible MemTotal={total}")
    used_ratio = (total - available) / total
    return max(0.0, min(100.0, used_ratio * 100))


def _parse_disk(block: str) -> float:
    rows = [line.split() for line in block.splitlines() if line.strip()]
    data_rows = [row for row in rows if row and row[0] != "Filesystem"]
    if not data_rows:
        raise ProbeParseError("df produced no data rows")
    for row in data_rows:
        if len(row) >= 6 and row[-1] == "/":
            try:
                pct = float(row[4].rstrip("%"))
            except (ValueError, IndexError) as exc:
                raise ProbeParseError(f"malformed df Use% field: {row}") from exc
            return max(0.0, min(100.0, pct))
    raise ProbeParseError("no df row for mountpoint '/'")


def _parse_uptime(block: str) -> float:
    seconds = float(_first_line(block, "uptime").split()[0])
    if seconds < 0:
        raise ProbeParseError(f"negative uptime: {seconds}")
    return seconds / 86400


def _parse_cores(block: str) -> int | None:
    line = _first_line(block, "cores")
    try:
        cores = int(line.split()[0])
    except (ValueError, IndexError) as exc:
        raise ProbeParseError(f"malformed core count: {line[:40]}") from exc
    if cores <= 0:
        raise ProbeParseError(f"implausible core count: {cores}")
    return cores


def _parse_stat(block: str) -> CpuTimes:
    line = _first_line(block, "cpu")
    if not line.startswith("cpu"):
        raise ProbeParseError(f"/proc/stat does not start with cpu aggregate: {line[:40]}")
    fields = line.split()[1:]
    try:
        counters = [int(v) for v in fields]
    except ValueError as exc:
        raise ProbeParseError("non-numeric counters in /proc/stat") from exc
    if len(counters) < 4:
        raise ProbeParseError(f"expected >=4 cpu counters, got {len(counters)}")
    idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
    return CpuTimes(idle=idle, total=sum(counters))
