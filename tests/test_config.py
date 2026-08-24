from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest

from sshfleet.config import (
    ConfigError,
    Defaults,
    Settings,
    load_settings,
    resolve_config_path,
    settings_from_mapping,
)

VALID = """
[defaults]
user = "deploy"
port = 2222
interval = 5
timeout = 6.5
max_concurrent = 4
identity_file = "~/.ssh/id_ed25519"
verify_hosts = false

[[hosts]]
name = "edge"
host = "203.0.113.10"

[[hosts]]
host = "db.internal"
user = "admin"
port = 22
identity_file = "/etc/ssh/db_key"
"""


def toml_load(text: str) -> Mapping:
    return tomllib.loads(text)


def test_valid_config_maps_everything() -> None:
    settings = settings_from_mapping(toml_load(VALID))

    assert settings.defaults == Defaults(
        user="deploy",
        port=2222,
        interval=5.0,
        timeout=6.5,
        max_concurrent=4,
        identity_file=Path.home() / ".ssh" / "id_ed25519",
        verify_hosts=False,
    )
    edge, db = settings.hosts
    assert edge.name == "edge" and edge.port == 2222 and edge.user == "deploy"
    assert db.name == "db.internal" and db.user == "admin" and db.port == 22


def test_host_name_defaults_to_address() -> None:
    settings = settings_from_mapping({"hosts": [{"host": "10.0.0.7"}]})
    assert settings.hosts[0].name == "10.0.0.7"


def test_duplicate_names_rejected() -> None:
    raw = {"hosts": [{"host": "a"}, {"name": "a", "host": "b"}]}
    with pytest.raises(ConfigError, match="duplicate host name 'a'"):
        settings_from_mapping(raw)


def test_identity_file_expands_tilde() -> None:
    settings = settings_from_mapping(
        {"defaults": {"identity_file": "~/keys/id_rsa"}, "hosts": [{"host": "x"}]}
    )
    assert "~" not in str(settings.defaults.identity_file)


def test_defaults_are_optional() -> None:
    settings = settings_from_mapping({"hosts": [{"host": "h1"}]})
    assert settings.defaults.port == 22
    assert settings.defaults.verify_hosts is True


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"bogus": 1, "hosts": [{"host": "a"}]}, "unknown top-level keys"),
        ({"hosts": []}, "at least one entry"),
        ({"hosts": [{"host": "a"}], "defaults": {"interval": -3}}, "interval"),
        ({"hosts": [{"host": "a"}], "defaults": {"timeout": 0}}, "timeout"),
        ({"hosts": [{"host": "a"}], "defaults": {"port": "ssh"}}, "port"),
        ({"hosts": [{"host": "a"}], "defaults": {"max_concurrent": 0}}, "max_concurrent"),
        ({"hosts": [{"host": "a"}], "defaults": {"verify_hosts": "yes"}}, "verify_hosts"),
        ({"hosts": [{"host": "a", "wat": 1}]}, "unknown keys"),
        ({"hosts": [{}]}, "'host' is required"),
        ({"hosts": [{"host": "a"}, {"host": "b", "port": 70_000}]}, "port"),
        ({"hosts": [{"host": "a"}, {"name": "", "host": "b"}]}, "'name'"),
    ],
)
def test_invalid_configs_rejected(raw: dict, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        settings_from_mapping(raw)


def test_load_settings_reports_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml")


def test_load_settings_reports_bad_toml(tmp_path) -> None:
    target = tmp_path / "broken.toml"
    target.write_text("[defaults\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(target)


def test_resolve_explicit_path_wins(tmp_path) -> None:
    target = tmp_path / "fleet.toml"
    target.touch()
    assert resolve_config_path(target) == target


def test_resolve_falls_back_to_none_when_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert resolve_config_path(None) is None


def test_with_interval_returns_updated_copy() -> None:
    settings = Settings(defaults=Defaults(), hosts=())
    assert settings.with_interval(2.5).defaults.interval == 2.5
    assert settings.defaults.interval == 10.0
