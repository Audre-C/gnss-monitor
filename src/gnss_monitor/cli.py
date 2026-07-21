"""Command-line entry point.

Phase 1 scope: load and validate the configuration, initialise
diagnostics logging, and print a summary of the configured channels
including each channel's effective expected baseline. Later phases will
start the acquisition pipeline from here.
"""

from __future__ import annotations

import argparse
import sys

from gnss_monitor import __version__
from gnss_monitor.config import ConfigError, RootConfig, load_config
from gnss_monitor.logging_setup import setup_logging

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


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
        "--check",
        action="store_true",
        help="Validate the configuration and exit without running.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _print_config_summary(config: RootConfig) -> None:
    print()
    print(f"Application : {config.app.name}")
    print(f"Site        : {config.site.name}")
    print(f"Channels    : {len(config.channels)}")
    print()

    for channel in config.channels:
        baseline = config.effective_baseline(channel.id)
        source = channel.source

        if source.type == "file":
            source_text = f"file  {source.path} (rate={source.rate})"
        else:
            source_text = f"serial  {source.port} @ {source.baud} baud"

        print(f"  [{channel.id}]")
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logger = setup_logging(
        config.app.log_dir, config.app.diagnostics_level
    )
    logger.info(
        "Configuration loaded: site '%s', %d channel(s)",
        config.site.name,
        len(config.channels),
    )

    _print_config_summary(config)

    if args.check:
        print("Configuration is valid.")
        return EXIT_OK

    # Phase 1 ends here. Phase 3 will construct and run the
    # acquisition pipeline (channels, bus, consumers) at this point.
    logger.info(
        "Phase 1 build: pipeline not yet implemented, exiting cleanly."
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())