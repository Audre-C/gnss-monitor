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
    antenna: Optional[str] = Field(
        default=None,
        description="Human-readable antenna description, e.g. 'BT-104'.",
    )
    source: SourceConfig = Field(discriminator="type")
    expected: Optional[ExpectedOverride] = Field(
        default=None,
        description="Optional per-channel overrides of the site baseline.",
    )


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