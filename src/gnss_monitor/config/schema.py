"""Typed configuration schema for the GNSS monitoring platform.

The configuration has four top-level sections:

    app:       application-wide settings (logging, paths)
    site:      the expected operating baseline shared by all receivers
    channels:  one entry per physical receiver (source + optional
               per-channel baseline overrides)

Design rule: application code never reads raw YAML or dictionaries.
It only ever receives validated objects defined in this module. The
merge of site-level defaults with per-channel overrides happens here,
so downstream code always works with a complete ExpectedBaseline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    """Base model: unknown keys in the YAML are an error (fail fast)."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Expected baseline (site defaults + per-channel overrides)
# ---------------------------------------------------------------------------


class ExpectedBaseline(StrictModel):
    """The complete set of expected operating conditions for a receiver.

    All fields are required at the *site* level. Individual channels may
    override any subset via ExpectedOverride.
    """

    latitude_deg: float = Field(
        description="Expected latitude in decimal degrees (WGS-84).",
        ge=-90.0,
        le=90.0,
    )
    longitude_deg: float = Field(
        description="Expected longitude in decimal degrees (WGS-84).",
        ge=-180.0,
        le=180.0,
    )
    position_tolerance_m: float = Field(
        description="Maximum acceptable horizontal distance from the "
        "expected position, in meters.",
        gt=0.0,
    )
    altitude_m: float = Field(
        description="Expected altitude (MSL) in meters.",
    )
    altitude_tolerance_m: float = Field(
        description="Maximum acceptable deviation from expected altitude, "
        "in meters.",
        gt=0.0,
    )
    max_speed_mps: float = Field(
        description="Maximum plausible reported speed for a stationary "
        "receiver, in meters per second.",
        ge=0.0,
    )
    receiver_timeout_s: float = Field(
        description="Maximum acceptable time without any valid sentence "
        "from the receiver, in seconds.",
        gt=0.0,
    )
    min_satellites: int = Field(
        description="Minimum expected number of satellites used in the fix.",
        ge=0,
    )
    max_hdop: float = Field(
        description="Maximum acceptable HDOP value.",
        gt=0.0,
    )


class ExpectedOverride(StrictModel):
    """Per-channel partial override of the site baseline.

    Every field is optional; unset fields inherit the site value.
    """

    latitude_deg: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude_deg: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    position_tolerance_m: Optional[float] = Field(default=None, gt=0.0)
    altitude_m: Optional[float] = None
    altitude_tolerance_m: Optional[float] = Field(default=None, gt=0.0)
    max_speed_mps: Optional[float] = Field(default=None, ge=0.0)
    receiver_timeout_s: Optional[float] = Field(default=None, gt=0.0)
    min_satellites: Optional[int] = Field(default=None, ge=0)
    max_hdop: Optional[float] = Field(default=None, gt=0.0)

    def merge_into(self, baseline: ExpectedBaseline) -> ExpectedBaseline:
        """Return a new ExpectedBaseline with override values applied."""
        overrides = {
            key: value
            for key, value in self.model_dump().items()
            if value is not None
        }
        merged = baseline.model_dump()
        merged.update(overrides)
        return ExpectedBaseline(**merged)


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------


class FileSourceConfig(StrictModel):
    """Replay a previously recorded NMEA log file (Stage 1)."""

    type: Literal["file"]
    path: Path = Field(description="Path to the NMEA log file.")
    rate: Union[Literal["realtime", "fast"], float] = Field(
        default="realtime",
        description="Replay pacing: 'realtime' (1 sentence/second), "
        "'fast' (no delay), or a numeric rate in sentences per second.",
    )
    loop: bool = Field(
        default=False,
        description="Restart from the beginning when the file ends.",
    )

    @field_validator("rate")
    @classmethod
    def _validate_numeric_rate(
        cls, value: Union[str, float]
    ) -> Union[str, float]:
        if isinstance(value, (int, float)) and value <= 0:
            raise ValueError("numeric rate must be positive")
        return value


class SerialSourceConfig(StrictModel):
    """Live serial input (Stage 2: COM ports, Stage 3: /dev/ttyUSB*)."""

    type: Literal["serial"]
    port: str = Field(
        description="Serial port name, e.g. 'COM7' or '/dev/ttyUSB0'."
    )
    baud: int = Field(default=9600, gt=0)


SourceConfig = Union[FileSourceConfig, SerialSourceConfig]


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class ChannelConfig(StrictModel):
    """One physical GNSS receiver and its data source."""

    id: str = Field(
        description="Unique channel identifier, e.g. 'neo6m_1'.",
        pattern=r"^[a-zA-Z][a-zA-Z0-9_\-]*$",
        min_length=1,
        max_length=64,
    )
    module: str = Field(
        description="Human-readable receiver module name, "
        "e.g. 'u-blox NEO-6M'."
    )
    name: Optional[str] = Field(
        default=None,
        description="Short display label for the dashboard, e.g. 'GPS'. "
        "Falls back to the module, then the id, when unset.",
    )
    constellation: Optional[str] = Field(
        default=None,
        description="GNSS constellation this receiver is configured to "
        "track, e.g. 'GPS', 'GLONASS', 'Galileo', 'BeiDou'. Display/label "
        "only; the pipeline is constellation-agnostic.",
    )
    role: Literal["constellation", "reference"] = Field(
        default="constellation",
        description="'constellation' (default): a receiver dedicated to "
        "one GNSS constellation, expected to agree with its peers. "
        "'reference': a multi-constellation receiver (e.g. the LC29HEA) "
        "used as a sanity-check rather than a dedicated constellation "
        "monitor - excluded from cross-receiver disagreement scoring "
        "since it isn't a peer of the single-constellation receivers.",
    )
    antenna: Optional[str] = Field(
        default=None,
        description="Human-readable antenna description, e.g. 'BT-104'.",
    )
    source: SourceConfig = Field(discriminator="type")
    expected: Optional[ExpectedOverride] = Field(
        default=None,
        description="Optional per-channel overrides of the site baseline.",
    )

    @property
    def display_name(self) -> str:
        """The label to show in the dashboard for this receiver."""
        return self.name or self.module or self.id

    @property
    def display_constellation(self) -> str:
        """The GNSS constellation label to show, or '-' if unset."""
        return self.constellation or "-"

    @property
    def is_reference(self) -> bool:
        """True for a multi-constellation sanity-check receiver."""
        return self.role == "reference"


# ---------------------------------------------------------------------------
# Analysis (Version 2 scoring engine)
#
# Every threshold and weight the scoring engine (gnss_monitor.analysis)
# uses lives here - no magic numbers in Python. All fields are required
# (no Python-side defaults) so a threshold is either explicit in the YAML
# or the config fails to load; this mirrors ExpectedBaseline above. The
# whole `analysis:` section is optional at the RootConfig level so
# existing configs without it keep working unchanged (Simple Mode only).
# ---------------------------------------------------------------------------


class PositionScoringConfig(StrictModel):
    """Position-offset indicator: two independent severity tiers.

    Distances beyond failure_radius_m score weight_failure; beyond
    warning_radius_m (but within failure_radius_m) score weight_warning.
    Unlike speed/hdop below, both tiers have their own explicit weight,
    so there is no interpolation between them - only the worse tier that
    was actually crossed contributes.
    """

    warning_radius_m: float = Field(
        gt=0.0, description="Distance from the expected position (m) "
        "beyond which the warning tier triggers."
    )
    failure_radius_m: float = Field(
        gt=0.0, description="Distance from the expected position (m) "
        "beyond which the failure tier triggers."
    )
    weight_warning: float = Field(
        ge=0.0, description="Points contributed at the warning tier."
    )
    weight_failure: float = Field(
        ge=0.0, description="Points contributed at the failure tier."
    )

    @model_validator(mode="after")
    def _validate_radii_ordering(self) -> "PositionScoringConfig":
        if self.failure_radius_m <= self.warning_radius_m:
            raise ValueError(
                "analysis.position.failure_radius_m must be greater than "
                "warning_radius_m"
            )
        return self


class NoFixScoringConfig(StrictModel):
    """No-GNSS-fix indicator: a flat penalty while the fix is invalid."""

    weight: float = Field(
        ge=0.0, description="Points contributed while there is no valid "
        "GNSS fix."
    )


class SpeedScoringConfig(StrictModel):
    """Sudden-speed indicator for an installation expected to be static.

    Below stationary_speed_limit_kmh scores 0; at or above
    warning_speed_kmh scores the full weight; in between, the score
    ramps up linearly so both configured thresholds affect the result
    rather than only one of them being load-bearing.
    """

    stationary_speed_limit_kmh: float = Field(
        ge=0.0, description="Speed (km/h) below which a stationary "
        "receiver's normal reporting noise is expected."
    )
    warning_speed_kmh: float = Field(
        gt=0.0, description="Speed (km/h) at or above which the full "
        "weight is scored."
    )
    weight: float = Field(
        ge=0.0, description="Points contributed at warning_speed_kmh "
        "or above."
    )

    @model_validator(mode="after")
    def _validate_speed_ordering(self) -> "SpeedScoringConfig":
        if self.warning_speed_kmh <= self.stationary_speed_limit_kmh:
            raise ValueError(
                "analysis.speed.warning_speed_kmh must be greater than "
                "stationary_speed_limit_kmh"
            )
        return self


class SatellitesScoringConfig(StrictModel):
    """Satellite-count indicator: two independent conditions, one weight.

    "minimum" is an absolute floor; "sudden_drop" is a same-weight,
    independent relative check (this poll vs the previous one). Either
    condition on its own scores the full weight; both firing together
    still scores it only once.
    """

    minimum: int = Field(
        ge=0, description="Satellite count below which the indicator "
        "triggers."
    )
    sudden_drop: int = Field(
        ge=1, description="A same-poll-to-poll decrease in satellite "
        "count at or above this triggers the indicator."
    )
    weight: float = Field(
        ge=0.0, description="Points contributed when either condition "
        "is met."
    )


class HdopScoringConfig(StrictModel):
    """HDOP indicator: below `warning` scores 0, at/above `critical`
    scores the full weight, ramping linearly in between (same reasoning
    as SpeedScoringConfig).
    """

    warning: float = Field(
        gt=0.0, description="HDOP above which the score starts to ramp."
    )
    critical: float = Field(
        gt=0.0, description="HDOP at or above which the full weight is "
        "scored."
    )
    weight: float = Field(
        ge=0.0, description="Points contributed at critical or above."
    )

    @model_validator(mode="after")
    def _validate_hdop_ordering(self) -> "HdopScoringConfig":
        if self.critical <= self.warning:
            raise ValueError(
                "analysis.hdop.critical must be greater than warning"
            )
        return self


class TimeScoringConfig(StrictModel):
    """Time-discontinuity indicator: a jump (either direction) in the
    receiver's reported UTC time between consecutive fixes."""

    max_jump_seconds: float = Field(
        gt=0.0, description="Timestamp change between consecutive fixes "
        "(seconds) beyond which the indicator triggers."
    )
    weight: float = Field(
        ge=0.0, description="Points contributed when triggered."
    )


class DisagreementScoringConfig(StrictModel):
    """Cross-receiver disagreement: how far a receiver's fix is from the
    other receivers' consensus position."""

    max_distance_between_receivers_m: float = Field(
        gt=0.0, description="Distance from the other receivers' centroid "
        "(m) beyond which the indicator triggers."
    )
    weight: float = Field(
        ge=0.0, description="Points contributed when triggered."
    )


class SignalScoringConfig(StrictModel):
    """C/N0 (signal-strength) indicator: two independent conditions, one
    weight - mirrors SatellitesScoringConfig above. A spoofed or
    simulated signal tends to arrive at one near-constant power rather
    than the natural spread real sky geometry produces, so either an
    implausibly strong mean on its own, or a uniformly elevated set,
    triggers; either firing scores the full weight once, not additively.

    Elevation-correlated C/N0 checks and previous-sample deltas are
    deliberately out of scope for now - see the module docstring on
    gnss_monitor.analysis.rules.
    """

    min_tracked_satellites: int = Field(
        ge=1, description="Minimum number of tracked-satellite C/N0 "
        "readings required to evaluate this indicator at all."
    )
    uniform_std_dbhz: float = Field(
        gt=0.0, description="Population standard deviation (dB-Hz) at "
        "or below which the tracked set counts as 'uniform'."
    )
    uniform_min_mean_dbhz: float = Field(
        description="Only a uniform set at or above this mean C/N0 "
        "(dB-Hz) is treated as suspicious, so a genuinely weak, "
        "low-variance set (e.g. a poor antenna) does not trigger."
    )
    max_mean_cn0_dbhz: float = Field(
        description="Mean C/N0 (dB-Hz) at or above which the signal is "
        "implausibly strong on its own, regardless of spread."
    )
    weight: float = Field(
        ge=0.0, description="Points contributed when either condition "
        "is met."
    )


class OverallThresholdsConfig(StrictModel):
    """Score cut-offs mapping a total score to a HealthState."""

    warning: float = Field(
        ge=0.0, description="Score at/above which the state is Warning."
    )
    potential_spoofing: float = Field(
        ge=0.0, description="Score at/above which the state is "
        "Potential Spoofing."
    )
    spoofing: float = Field(
        ge=0.0, description="Score at/above which the state is "
        "Spoofing Detected."
    )

    @model_validator(mode="after")
    def _validate_ordering(self) -> "OverallThresholdsConfig":
        if not (self.warning < self.potential_spoofing < self.spoofing):
            raise ValueError(
                "analysis.overall thresholds must satisfy "
                "warning < potential_spoofing < spoofing"
            )
        return self


class AnalysisConfig(StrictModel):
    """Version 2 scoring-engine configuration.

    Every threshold used by gnss_monitor.analysis lives here. When this
    section is absent from the YAML entirely, the scoring engine is not
    used and the platform behaves exactly as in Simple Mode.
    """

    position: PositionScoringConfig
    no_fix: NoFixScoringConfig
    speed: SpeedScoringConfig
    satellites: SatellitesScoringConfig
    hdop: HdopScoringConfig
    time: TimeScoringConfig
    disagreement: DisagreementScoringConfig
    signal: Optional[SignalScoringConfig] = Field(
        default=None,
        description="C/N0 (signal-strength) indicator. Optional so "
        "existing analysis: blocks written before this indicator "
        "existed keep loading unchanged.",
    )
    overall: OverallThresholdsConfig


# ---------------------------------------------------------------------------
# Top-level sections
# ---------------------------------------------------------------------------


class AppSection(StrictModel):
    """Application-wide settings."""

    name: str = Field(default="gnss-monitor")
    log_dir: Path = Field(
        default=Path("logs"),
        description="Directory for diagnostic log files.",
    )
    diagnostics_level: Literal[
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    ] = Field(default="INFO")


class SiteSection(StrictModel):
    """The fixed installation site and its expected baseline."""

    name: str = Field(description="Site name, e.g. 'Teleport Rooftop'.")
    expected: ExpectedBaseline


class RootConfig(StrictModel):
    """The complete, validated application configuration."""

    app: AppSection = Field(default_factory=AppSection)
    site: SiteSection
    channels: list[ChannelConfig] = Field(min_length=1)
    analysis: Optional[AnalysisConfig] = Field(
        default=None,
        description="Version 2 scoring-engine thresholds. Omit this "
        "section entirely to run without the scoring engine (Simple "
        "Mode behaviour only) - existing configs are unaffected.",
    )

    @model_validator(mode="after")
    def _validate_unique_channel_ids(self) -> "RootConfig":
        seen: set[str] = set()
        for channel in self.channels:
            if channel.id in seen:
                raise ValueError(f"duplicate channel id: '{channel.id}'")
            seen.add(channel.id)
        return self

    def effective_baseline(self, channel_id: str) -> ExpectedBaseline:
        """Return the fully merged expected baseline for a channel.

        Raises KeyError if the channel id is unknown.
        """
        for channel in self.channels:
            if channel.id == channel_id:
                if channel.expected is None:
                    return self.site.expected
                return channel.expected.merge_into(self.site.expected)
        raise KeyError(f"unknown channel id: '{channel_id}'")