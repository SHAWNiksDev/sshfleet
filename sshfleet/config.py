"""TOML fleet configuration: defaults plus an explicit host inventory."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from sshfleet.models import HostSpec


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Defaults:
    user: str | None = None
    port: int | None = None
    interval: float = 10.0
    timeout: float = 8.0
    max_concurrent: int = 8
    identity_file: Path | None = None
    password: str | None = field(default=None, repr=False)
    verify_hosts: bool = True
    import_ssh_config: bool = True
    warn_pct: float = 70.0
    crit_pct: float = 88.0
    ssh_config_paths: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class Settings:
    defaults: Defaults
    hosts: tuple[HostSpec, ...]

    def with_interval(self, interval: float) -> Settings:
        return Settings(defaults=replace(self.defaults, interval=interval), hosts=self.hosts)


DEFAULT_SSH_CONFIG = Path("~/.ssh/config")


DEFAULT_CONFIG_PATHS = (Path("fleet.toml"), Path("~/.config/sshfleet/fleet.toml"))


def resolve_config_path(explicit: str | Path | None) -> Path | None:
    if explicit is not None:
        return Path(explicit).expanduser()
    for candidate in DEFAULT_CONFIG_PATHS:
        path = candidate.expanduser()
        if path.is_file():
            return path
    return None


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path).expanduser()
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {config_path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    return settings_from_mapping(payload)


def settings_from_mapping(raw: Mapping[str, Any]) -> Settings:
    unknown_top = set(raw) - {"defaults", "hosts"}
    if unknown_top:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown_top))}")

    defaults = _parse_defaults(raw.get("defaults", {}))
    hosts_raw = raw.get("hosts")

    if not isinstance(hosts_raw, list) or not hosts_raw:
        raise ConfigError("[[hosts]] array must exist and contain at least one entry")

    seen_names: set[str] = set()
    specs: list[HostSpec] = []
    for index, entry in enumerate(hosts_raw):
        spec = _parse_host(entry, defaults, index)
        if spec.name in seen_names:
            raise ConfigError(f"duplicate host name {spec.name!r}")
        seen_names.add(spec.name)
        specs.append(spec)
    return Settings(defaults=defaults, hosts=tuple(specs))


def _require_table(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} must be a table")
    return value


def _parse_defaults(table: Any) -> Defaults:
    data = _require_table(table, "[defaults]")
    allowed = {
        "user",
        "port",
        "interval",
        "timeout",
        "max_concurrent",
        "identity_file",
        "password",
        "verify_hosts",
        "import_ssh_config",
        "warn_pct",
        "crit_pct",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown [defaults] keys: {', '.join(sorted(unknown))}")

    port = data.get("port")
    if port is not None and (
        not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
    ):
        raise ConfigError(f"[defaults].port must be an integer in 1..65535, got {port!r}")

    interval = _positive_number(data, "interval", default=10.0)
    timeout = _positive_number(data, "timeout", default=8.0)

    max_concurrent = data.get("max_concurrent", 8)
    if (
        not isinstance(max_concurrent, int)
        or isinstance(max_concurrent, bool)
        or max_concurrent < 1
    ):
        raise ConfigError(
            f"[defaults].max_concurrent must be a positive integer, got {max_concurrent!r}"
        )

    identity = data.get("identity_file")
    if identity is not None and not isinstance(identity, str):
        raise ConfigError("[defaults].identity_file must be a string path")

    verify_hosts = data.get("verify_hosts", True)
    if not isinstance(verify_hosts, bool):
        raise ConfigError("[defaults].verify_hosts must be true or false")

    import_ssh_config = data.get("import_ssh_config", True)
    if not isinstance(import_ssh_config, bool):
        raise ConfigError("[defaults].import_ssh_config must be true or false")

    warn_pct = _threshold(data, "warn_pct", default=70.0)
    crit_pct = _threshold(data, "crit_pct", default=88.0)
    if crit_pct <= warn_pct:
        raise ConfigError(
            f"[defaults].crit_pct ({crit_pct}) must be greater than warn_pct ({warn_pct})"
        )

    return Defaults(
        user=_optional_string(data, "user"),
        port=port,
        interval=interval,
        timeout=timeout,
        max_concurrent=max_concurrent,
        identity_file=Path(identity).expanduser() if identity else None,
        password=_optional_string(data, "password"),
        verify_hosts=verify_hosts,
        import_ssh_config=import_ssh_config,
        warn_pct=warn_pct,
        crit_pct=crit_pct,
        ssh_config_paths=_resolve_ssh_config_paths(import_ssh_config),
    )


def _threshold(data: dict[str, Any], key: str, *, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100:
        raise ConfigError(f"[defaults].{key} must be a number in (0, 100], got {value!r}")
    return float(value)


def _resolve_ssh_config_paths(enabled: bool) -> tuple[str, ...]:
    if not enabled:
        return ()
    path = DEFAULT_SSH_CONFIG.expanduser()
    return (str(path),) if path.is_file() else ()


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[defaults].{key} must be a non-empty string")
    return value


def _positive_number(data: dict[str, Any], key: str, *, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"[defaults].{key} must be a positive number, got {value!r}")
    return float(value)


def _parse_host(entry: Any, defaults: Defaults, index: int) -> HostSpec:
    data = _require_table(entry, f"hosts[{index}]")
    allowed = {"name", "host", "user", "port", "identity_file", "password"}
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"hosts[{index}]: unknown keys {', '.join(sorted(unknown))}")

    host = data.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ConfigError(f"hosts[{index}]: 'host' is required and must be a string")
    host = host.strip()

    name = data.get("name")
    if name is None:
        name = host
    elif not isinstance(name, str) or not name.strip():
        raise ConfigError(f"hosts[{index}]: 'name' must be a non-empty string")
    name = name.strip() if isinstance(name, str) else name

    port = data.get("port", defaults.port)
    if port is not None and (
        not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
    ):
        raise ConfigError(f"hosts[{index}]: port must be an integer in 1..65535")

    user = data.get("user", defaults.user)
    if user is not None and (not isinstance(user, str) or not user.strip()):
        raise ConfigError(f"hosts[{index}]: 'user' must be a non-empty string when set")

    identity = data.get("identity_file", defaults.identity_file)
    identity_str: str | None
    if identity is None:
        identity_str = None
    elif isinstance(identity, Path):
        identity_str = str(identity)
    elif isinstance(identity, str) and identity.strip():
        identity_str = str(Path(identity.strip()).expanduser())
    else:
        raise ConfigError(f"hosts[{index}]: 'identity_file' must be a string path")

    password = data.get("password", defaults.password)
    if password is not None and (not isinstance(password, str) or not password):
        raise ConfigError(f"hosts[{index}]: 'password' must be a non-empty string when set")

    return HostSpec(
        name=name,
        host=host,
        user=user,
        port=port,
        identity_file=identity_str,
        password=password,
    )
