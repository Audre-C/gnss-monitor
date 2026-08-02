"""Per-receiver monitoring: source -> framer -> parser -> latest state.

A ReceiverMonitor owns one NMEASource and reuses the existing Framer and
NmeaParser. It maintains the receiver's latest observed state (most
recent valid position and fix quality). It performs no evaluation and no
display; those are separate, independent modules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from gnss_monitor.framing import Framer
from gnss_monitor.model import (
    FixQuality,
    GGAMessage,
    GSVMessage,
    RMCMessage,
    SatelliteInfo,
)
from gnss_monitor.model.sentence import NmeaSentence
from gnss_monitor.parsing import NmeaParser
from gnss_monitor.sources.base import NMEASource


class DataSink(Protocol):
    """Narrow interface for an optional passive observer of every sentence.

    Defined here (the acquisition layer) rather than in the data-logging
    package itself, so this module never has to import anything from
    data_logging: any object with matching methods satisfies this
    Protocol structurally, and gnss_monitor.data_logging.DataLogger is
    simply one such object, wired in by LiveController. A sink must
    never raise (ReceiverMonitor does not guard these calls) and must
    return immediately - it observes, it does not influence acquisition.
    """

    def on_raw(self, receiver_id: str, t_wall: Optional[float], raw: str) -> None: ...

    def on_parsed(
        self,
        receiver_id: str,
        t_wall: Optional[float],
        sentence: NmeaSentence,
        message: object,
    ) -> None: ...


class _NullDataSink:
    """Default sink: both calls are no-ops, so an unconfigured
    ReceiverMonitor pays only the cost of two trivial method calls per
    sentence and writes nothing anywhere."""

    def on_raw(self, receiver_id: str, t_wall: Optional[float], raw: str) -> None:
        return None

    def on_parsed(
        self,
        receiver_id: str,
        t_wall: Optional[float],
        sentence: NmeaSentence,
        message: object,
    ) -> None:
        return None


_NULL_DATA_SINK = _NullDataSink()

_CN0_TRACK_TTL_S = 10.0
"""How long a tracked satellite's C/N0 reading stays "current" after its
last GSV update, in monotonic seconds. GSV cycles roughly once a second
on the receivers this project targets, so 10s comfortably survives a
couple of missed or garbled cycles without holding onto a reading from a
satellite that has since dropped out of track.
"""


@dataclass
class ReceiverState:
    """Mutable snapshot of what a receiver has reported so far."""

    sentences_seen: int = 0
    valid_checksums: int = 0
    has_fix: bool = False
    fix_quality: Optional[FixQuality] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    num_satellites: Optional[int] = None
    hdop: Optional[float] = None
    speed_mps: Optional[float] = None
    last_fix_utc: Optional[str] = None
    last_seen_monotonic: Optional[float] = None
    """Monotonic time a sentence was last actually framed from the wire.

    None until the first sentence arrives. This is what lets the
    evaluator tell "receiver went silent" apart from "receiver is
    still reporting its last known fix" - see PositionEvaluator.evaluate.
    """

    _tracked_cn0: dict[tuple[str, int], tuple[int, float]] = field(
        default_factory=dict
    )
    """Private: (talker, prn) -> (snr_dbhz, t_rx_mono) for satellites most
    recently reported as tracked (SNR present) in a GSV sentence. Keyed
    by (talker, prn) rather than prn alone so multi-constellation talkers
    (GP, GL, GA, GB, ...) never collide on overlapping PRN numbering.
    Populated by record_tracked_satellites(); read via
    tracked_cn0_dbhz(). No statistics live here - that is an analysis
    concern (see gnss_monitor.analysis.rules.signal_anomaly), not an
    acquisition one.
    """

    def record_tracked_satellites(
        self,
        talker: str,
        satellites: tuple[SatelliteInfo, ...],
        t_rx_mono: float,
    ) -> None:
        """Update the rolling tracked-satellite C/N0 view from one GSV sentence.

        Only satellites with both a PRN and a populated SNR are recorded:
        snr_dbhz is None for a satellite that is merely in view but not
        tracked (see SatelliteInfo), which this view must exclude. Stale
        entries (older than _CN0_TRACK_TTL_S) are dropped in the same
        pass, so the map stays bounded over a 24/7 run instead of
        accumulating every satellite ever seen.

        The dict is replaced wholesale rather than mutated in place: a
        snapshot taken via dataclasses.replace(state) (see
        ReceiverWorker.snapshot) shares this dict object by reference, so
        replacing rather than mutating is what keeps that snapshot's view
        stable even though this state keeps changing afterwards on the
        worker thread.

        Independent of has_fix: GSV enumerates satellites in view
        regardless of whether a fix exists, so this keeps working during
        a no-fix cold start.
        """
        fresh = {
            key: value
            for key, value in self._tracked_cn0.items()
            if t_rx_mono - value[1] <= _CN0_TRACK_TTL_S
        }
        for satellite in satellites:
            if satellite.prn is None or satellite.snr_dbhz is None:
                continue
            fresh[(talker, satellite.prn)] = (satellite.snr_dbhz, t_rx_mono)
        self._tracked_cn0 = fresh

    def tracked_cn0_dbhz(self, now_mono: float) -> tuple[int, ...]:
        """C/N0 values (dB-Hz) for satellites tracked within the last TTL."""
        return tuple(
            snr_dbhz
            for snr_dbhz, t_rx_mono in self._tracked_cn0.values()
            if now_mono - t_rx_mono <= _CN0_TRACK_TTL_S
        )


class ReceiverMonitor:
    """Drives one receiver's data stream and tracks its latest state."""

    def __init__(
        self,
        receiver_id: str,
        display_name: str,
        source: NMEASource,
        framer: Optional[Framer] = None,
        parser: Optional[NmeaParser] = None,
        sink: Optional[DataSink] = None,
    ) -> None:
        self.receiver_id = receiver_id
        self.display_name = display_name
        self._source = source
        self._framer = framer or Framer()
        self._parser = parser or NmeaParser()
        self._sink = sink or _NULL_DATA_SINK
        self.state = ReceiverState()

    @property
    def source(self) -> NMEASource:
        return self._source

    def poll(self, max_lines: int) -> int:
        """Read up to max_lines from the source, updating state.

        Returns the number of lines actually processed this call. Stops
        early if the source has no more data available right now.
        """
        processed = 0
        for _ in range(max_lines):
            line = self._source.read_line()
            if line is None:
                break
            self._process_line(line)
            processed += 1
        return processed

    def _process_line(self, line: str) -> None:
        sentence = self._framer.frame(
            line,
            channel_id=self.receiver_id,
            t_rx_wall=time.time(),
            t_rx_mono=time.monotonic(),
        )
        if sentence is None:
            return
        self.state.sentences_seen += 1
        self.state.last_seen_monotonic = sentence.t_rx_mono
        if sentence.checksum_ok:
            self.state.valid_checksums += 1
        self._sink.on_raw(self.receiver_id, sentence.t_rx_wall, sentence.raw)
        message = self._parser.parse(sentence)
        self._sink.on_parsed(self.receiver_id, sentence.t_rx_wall, sentence, message)
        self._apply(message)

    def _apply(self, message: object) -> None:
        """Update state from a parsed message (latest valid wins).

        A GGA/RMC that reports no valid fix is itself a valid, current
        observation - "the receiver has no fix right now" - and must
        overwrite has_fix/position, not leave them latched at a stale
        True from whenever a fix was last held. Before this fix, has_fix
        could only ever be set to True: a receiver that genuinely lost
        its fix kept reporting a stale position as OK, which silently
        defeated the no_fix scoring rule (analysis.rules.no_fix) and any
        position-based rule that gates on has_fix.

        num_satellites/hdop are updated from every GGA regardless of fix
        validity (a real receiver reports both even at fix_quality 0),
        so hdop_anomaly/satellite_anomaly - which do NOT gate on has_fix
        - see the actual reception-quality reading that explains a fix
        loss, not a frozen pre-loss value. last_fix_utc deliberately
        keeps updating only on a valid fix: it feeds
        analysis.rules.time_discontinuity, which is specifically about
        jumps in the receiver's own fix timeline, not about the wall
        clock or acquisition timing - see gnss_monitor.data_logging for
        that (records the raw per-sentence UTC field independently).
        """
        if isinstance(message, GGAMessage):
            self.state.fix_quality = message.fix_quality
            if message.num_satellites is not None:
                self.state.num_satellites = message.num_satellites
            if message.hdop is not None:
                self.state.hdop = message.hdop
            if (
                message.has_fix
                and message.latitude_deg is not None
                and message.longitude_deg is not None
            ):
                self.state.has_fix = True
                self.state.latitude_deg = message.latitude_deg
                self.state.longitude_deg = message.longitude_deg
                self.state.altitude_m = message.altitude_m
                if message.utc_time is not None:
                    self.state.last_fix_utc = message.utc_time
            else:
                self.state.has_fix = False
                self.state.latitude_deg = None
                self.state.longitude_deg = None
                self.state.altitude_m = None
        elif isinstance(message, RMCMessage):
            if (
                message.is_valid
                and message.latitude_deg is not None
                and message.longitude_deg is not None
            ):
                self.state.has_fix = True
                self.state.latitude_deg = message.latitude_deg
                self.state.longitude_deg = message.longitude_deg
                if message.speed_mps is not None:
                    self.state.speed_mps = message.speed_mps
                if message.utc_time is not None:
                    self.state.last_fix_utc = message.utc_time
            else:
                self.state.has_fix = False
                self.state.latitude_deg = None
                self.state.longitude_deg = None
                self.state.speed_mps = None
        elif isinstance(message, GSVMessage):
            # Independent of GGA/RMC and of fix state entirely: satellites
            # in view are reported whether or not the receiver currently
            # has a fix, and C/N0 capture must keep working through a
            # no-fix cold start. talker/t_rx_mono are only absent for a
            # sentence framed in isolation (e.g. a unit test); skip rather
            # than guess.
            talker = message.talker
            t_rx_mono = message.sentence.t_rx_mono
            if talker is not None and t_rx_mono is not None:
                self.state.record_tracked_satellites(
                    talker, message.satellites, t_rx_mono
                )