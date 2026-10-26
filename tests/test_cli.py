from __future__ import annotations

import pytest

from sshfleet.cli import build_parser, main


def test_broken_toml_reports_error_and_exits_2(tmp_path, capsys) -> None:
    bad = tmp_path / "fleet.toml"
    bad.write_text("[defaults\n", encoding="utf-8")

    assert main(["--config", str(bad)]) == 2

    err = capsys.readouterr().err
    assert "error:" in err
    assert "invalid TOML" in err


def test_missing_explicit_config_reports_error(tmp_path, capsys) -> None:
    assert main(["--config", str(tmp_path / "nope.toml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_invalid_host_entry_reports_error(tmp_path, capsys) -> None:
    bad = tmp_path / "fleet.toml"
    bad.write_text('[[hosts]]\nhost = "a"\nwat = 1\n', encoding="utf-8")

    assert main(["--config", str(bad)]) == 2
    assert "unknown keys" in capsys.readouterr().err


@pytest.mark.parametrize("interval", ["0", "-3", "nan", "inf", "-inf"])
def test_nonpositive_interval_rejected(interval: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([f"--interval={interval}"])
    assert excinfo.value.code == 2


def test_directory_config_reports_error(tmp_path, capsys) -> None:
    assert main(["--config", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err


def test_config_and_demo_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--demo", "--config", "fleet.toml"])
    assert excinfo.value.code == 2


def test_demo_seed_parsed() -> None:
    args = build_parser().parse_args(["--demo", "--seed", "7"])
    assert args.demo is True
    assert args.seed == 7
