"""Command-line entry point.

Modes (selected with --mode, default 'auto'):
    auto     infer from source types: all serial -> live, all file ->
             replay. Mixed sources require an explicit --mode.
    replay   single-threaded replay dashboard (Phase 3, unchanged).
    live     concurrent multi-receiver serial monitoring (Phase 4).

Other flags:
    --check  validate configuration, print a summary, and exit.
    --once   replay mode only: process all data, render one frame, exit.
"""

from __future__ import annotations

import argparse
import sys

from gnss_monitor import __version__
from gnss_monitor.app import Application
from gnss_monitor.config import ConfigError, RootConfig, load_config
from gnss_monitor.logging_setup import setup_logging
from gnss_monitor.live import LiveController, TerminalLiveDashboard
from gnss_monitor.monitor import SimpleModeController, TerminalDashboard

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 3


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gnss-monitor",
        description=(
            "GPS Spoofing Measurement Platform for Multi-Constellation "
            "GNSS Evaluation"
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the YAML configuration file.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "replay", "live"),
        default="auto",
        help="Monitoring mode (default: auto-detect from source types).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the configuration and exit without running.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Replay mode: process all data, render one frame, then exit.",
    )
    parser.add_argument(
        "--tick",
        type=float,
        default=0.5,
        help="Seconds between dashboard refreshes (default: 0.5).",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=50,
        help="Replay: lines read per receiver per tick (default: 50).",
    )
    parser.add_argument(
        "--reconnect-interval",
        type=float,
        default=3.0,
        help="Live: seconds between reconnection attempts (default: 3.0).",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=0.1,
        help="Live: serial read timeout in seconds (default: 0.1).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _resolve_mode(mode: str, config: RootConfig) -> str:
    if mode != "auto":
        return mode
    types = {channel.source.type for channel in config.channels}
    if types == {"serial"}:
        return "live"
    if types == {"file"}:
        return "replay"
    raise ConfigError(
        "cannot auto-detect mode from mixed source types "
        f"({sorted(types)}); pass --mode replay or --mode live"
    )


def _print_config_summary(config: RootConfig, mode: str) -> None:
    print()
    print(f"Application : {config.app.name}")
    print(f"Site        : {config.site.name}")
    print(f"Mode        : {mode}")
    print(f"Channels    : {len(config.channels)}")
    print()

    for channel in config.channels:
        baseline = config.effective_baseline(channel.id)
        source = channel.source

        if source.type == "file":
            source_text = f"file  {source.path} (rate={source.rate})"
        else:
            source_text = f"serial  {source.port} @ {source.baud} baud"

        print(f"  [{channel.id}]  {channel.display_name}")
        print(f"    Module            : {channel.module}")
        if channel.antenna is not None:
            print(f"    Antenna           : {channel.antenna}")
        print(f"    Source            : {source_text}")
        print(
            f"    Expected position : "
            f"{baseline.latitude_deg:.6f}, {baseline.longitude_deg:.6f} "
            f"(±{baseline.position_tolerance_m:.0f} m)"
        )
        print(
            f"    Expected altitude : {baseline.altitude_m:.1f} m "
            f"(±{baseline.altitude_tolerance_m:.0f} m)"
        )
        print(f"    Max speed         : {baseline.max_speed_mps:.1f} m/s")
        print(f"    Receiver timeout  : {baseline.receiver_timeout_s:.0f} s")
        print(f"    Min satellites    : {baseline.min_satellites}")
        print(f"    Max HDOP          : {baseline.max_hdop:.1f}")
        print()


def _run_replay(config: RootConfig, args: argparse.Namespace) -> int:
    tick = 0.0 if args.once else args.tick
    dashboard = TerminalDashboard(clear=not args.once)
    controller = SimpleModeController(
        config=config,
        dashboard=dashboard,
        tick_interval_s=tick,
        lines_per_tick=args.batch,
        once=args.once,
    )
    ok = Application(controller).run()
    return EXIT_OK if ok else EXIT_RUNTIME_ERROR


def _run_live(config: RootConfig, args: argparse.Namespace) -> int:
    controller = LiveController(
        config=config,
        dashboard=TerminalLiveDashboard(),
        reconnect_interval_s=args.reconnect_interval,
        read_timeout_s=args.read_timeout,
        refresh_interval_s=args.tick,
    )
    ok = Application(controller).run()
    return EXIT_OK if ok else EXIT_RUNTIME_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        mode = _resolve_mode(args.mode, config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logger = setup_logging(
        config.app.log_dir, config.app.diagnostics_level
    )
    logger.info(
        "Configuration loaded: site '%s', %d channel(s), mode '%s'",
        config.site.name,
        len(config.channels),
        mode,
    )

    if args.check:
        _print_config_summary(config, mode)
        print("Configuration is valid.")
        return EXIT_OK

    try:
        if mode == "live":
            return _run_live(config, args)
        return _run_replay(config, args)
    except KeyboardInterrupt:
        logger.info("Interrupted by user; shutting down.")
        return EXIT_OK
    except (FileNotFoundError, ValueError) as exc:
        print(f"Runtime error: {exc}", file=sys.stderr)
        logger.error("Runtime error: %s", exc)
        return EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    sys.exit(main())