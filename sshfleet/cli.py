"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sshfleet import __version__
from sshfleet.collector import FleetCollector
from sshfleet.config import Defaults, Settings, load_settings, resolve_config_path
from sshfleet.demo import DemoProber, demo_hosts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sshfleet",
        description="Agentless async TUI dashboard for fleets of Linux servers over SSH.",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="path to fleet.toml (default: ./fleet.toml or ~/.config/sshfleet/fleet.toml)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        metavar="SECONDS",
        help="override the poll interval from the config",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run against a synthetic fleet; no config or network needed",
    )
    parser.add_argument("--seed", type=int, default=None, help="seed for --demo randomness")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    prober: FleetCollector | DemoProber
    if args.demo:
        hosts = demo_hosts()
        settings = Settings(
            defaults=Defaults(interval=args.interval if args.interval else 5.0),
            hosts=hosts,
        )
        prober = DemoProber(hosts, seed=args.seed)
    else:
        path = resolve_config_path(args.config)
        if path is None:
            parser.print_usage(sys.stderr)
            print(
                "error: no fleet.toml found (looked in ./ and ~/.config/sshfleet/); "
                "pass --config or try --demo",
                file=sys.stderr,
            )
            return 2
        settings = load_settings(path)
        if args.interval is not None:
            settings = settings.with_interval(args.interval)
        prober = FleetCollector(settings.defaults)

    from sshfleet.app import FleetApp

    FleetApp(settings, prober).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
