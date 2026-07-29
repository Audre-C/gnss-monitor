"""DataLogger: the public facade for the optional data-logging subsystem.

DataLogger is a passive observer, never a participant: it is handed
already-computed facts (a raw line, a parsed message, a receiver's
current snapshot) by ReceiverMonitor and LiveController, and its own
public methods never raise, never block the caller on disk I/O, and
never return a value anything else depends on. Concretely:

    * on_raw()/on_parsed() satisfy monitor.receiver_monitor.DataSink by
      structural typing (no shared base class, no import from `monitor`
      needed) and are called from a receiver's own worker thread inside
      ReceiverMonitor._process_line - the same thread that owns the
      serial read loop. Both build a small immutable record and enqueue
      it; that is the entire cost paid on the acquisition hot path.
    * update_receiver_context()/record_snapshot() are called from
      LiveController on the main render thread once per refresh tick.
    * A single background thread drains the queue and performs the
      actual (buffered, flushed) file writes in writers.py. If disk I/O
      stalls, the queue simply grows - up to a bound, past which new
      records are dropped rather than blocking either producer thread.

Disabled (no config, or enabled: false) is the default and is fully
inert: no thread is started, no directory is created, every public
method is a cheap early-return. This is what makes "if the section is
absent, behavior must be identical to the current implementation" true
by construction rather than by convention.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

from gnss_monitor.config.schema import DataLoggingConfig
from gnss_monitor.data_logging.records import (
    ParsedMessageRecord,
    RawSentenceRecord,
    SnapshotRecord,
)
from gnss_monitor.data_logging.writers import (
    ParsedCsvWriter,
    RawNmeaWriter,
    SnapshotCsvWriter,
)
from gnss_monitor.model import GGAMessage, RMCMessage
from gnss_monitor.model.sentence import NmeaSentence

_logger = logging.getLogger("gnss_monitor.data_logging")

_MAX_QUEUE_SIZE = 10_000
"""Records buffered before new ones are dropped. At a handful of
sentences/second/receiver across five receivers, this comfortably
absorbs a multi-second disk stall without blocking acquisition; beyond
that, dropping (with a single rate-limited warning) is preferable to
letting an unbounded queue consume memory during a longer stall."""

_STOP = object()


@dataclass(frozen=True, slots=True)
class _ReceiverContext:
    """Most-recently-known analysis-side facts for one receiver.

    Updated by LiveController once per refresh tick; read by on_parsed()
    to enrich a parsed-message row with values that are only computed at
    the controller/analysis layer, never inside ReceiverMonitor itself.
    """

    avg_cn0_dbhz: Optional[float] = None
    analysis_score: Optional[float] = None
    analysis_state: Optional[str] = None


_EMPTY_CONTEXT = _ReceiverContext()


def _extract_parsed_fields(message: object) -> dict:
    """Position/fix/satellite/HDOP/speed fields available at parse time.

    Mirrors the isinstance dispatch already used by
    ReceiverMonitor._apply - the same knowledge of "which message type
    carries which field", just read out instead of applied to state.
    Any other message type (GSV, GSA, VTG, GLL, TXT, Unknown) leaves
    every field blank; the row is still logged (sentence + talker are
    always known) so no sentence goes unaccounted for in the archive.
    """
    if isinstance(message, GGAMessage):
        return dict(
            latitude_deg=message.latitude_deg,
            longitude_deg=message.longitude_deg,
            has_fix=message.has_fix,
            num_satellites=message.num_satellites,
            hdop=message.hdop,
            speed_mps=None,
        )
    if isinstance(message, RMCMessage):
        return dict(
            latitude_deg=message.latitude_deg,
            longitude_deg=message.longitude_deg,
            has_fix=message.is_valid,
            num_satellites=None,
            hdop=None,
            speed_mps=message.speed_mps,
        )
    return dict(
        latitude_deg=None,
        longitude_deg=None,
        has_fix=None,
        num_satellites=None,
        hdop=None,
        speed_mps=None,
    )


class DataLogger:
    """Owns the queue/thread and the three optional file writers.

    Constructed once per LiveController from its (optional)
    DataLoggingConfig. Pass the same instance as the `sink` argument to
    every channel's ReceiverMonitor and call update_receiver_context()/
    record_snapshot() from the controller's own refresh loop.
    """

    def __init__(self, config: Optional[DataLoggingConfig]) -> None:
        self._config = config
        self._context_lock = threading.Lock()
        self._context: dict[str, _ReceiverContext] = {}
        self._last_snapshot_mono: dict[str, float] = {}
        self._overflow_warned = False

        self._queue: "queue.Queue[object]" = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._thread: Optional[threading.Thread] = None

        self._raw_writer: Optional[RawNmeaWriter] = None
        self._parsed_writer: Optional[ParsedCsvWriter] = None
        self._snapshot_writer: Optional[SnapshotCsvWriter] = None

        if config is not None and config.enabled:
            if config.raw_nmea.enabled:
                self._raw_writer = RawNmeaWriter(config.directory, config.rotate_daily)
            if config.parsed.enabled:
                self._parsed_writer = ParsedCsvWriter(
                    config.directory, config.rotate_daily
                )
            if config.snapshot.enabled:
                self._snapshot_writer = SnapshotCsvWriter(
                    config.directory, config.rotate_daily
                )
            if self._raw_writer or self._parsed_writer or self._snapshot_writer:
                self._thread = threading.Thread(
                    target=self._run, name="data-logger", daemon=True
                )
                self._thread.start()

    @property
    def enabled(self) -> bool:
        """True only if at least one sub-logger is actually running."""
        return self._thread is not None

    # -- DataSink protocol: called from a receiver's worker thread ----------

    def on_raw(self, receiver_id: str, t_wall: Optional[float], raw: str) -> None:
        if self._raw_writer is None:
            return
        self._enqueue(RawSentenceRecord(receiver_id, t_wall or time.time(), raw))

    def on_parsed(
        self,
        receiver_id: str,
        t_wall: Optional[float],
        sentence: NmeaSentence,
        message: object,
    ) -> None:
        if self._parsed_writer is None:
            return
        fields = _extract_parsed_fields(message)
        ctx = self._get_context(receiver_id)
        self._enqueue(
            ParsedMessageRecord(
                receiver_id=receiver_id,
                t_wall=t_wall or time.time(),
                sentence_type=sentence.sentence_type,
                talker=sentence.talker,
                avg_cn0_dbhz=ctx.avg_cn0_dbhz,
                analysis_score=ctx.analysis_score,
                analysis_state=ctx.analysis_state,
                **fields,
            )
        )

    # -- called from LiveController's own thread -----------------------------

    def update_receiver_context(
        self,
        receiver_id: str,
        avg_cn0_dbhz: Optional[float] = None,
        analysis_score: Optional[float] = None,
        analysis_state: Optional[str] = None,
    ) -> None:
        if self._parsed_writer is None:
            return
        with self._context_lock:
            self._context[receiver_id] = _ReceiverContext(
                avg_cn0_dbhz, analysis_score, analysis_state
            )

    def _get_context(self, receiver_id: str) -> _ReceiverContext:
        with self._context_lock:
            return self._context.get(receiver_id, _EMPTY_CONTEXT)

    def record_snapshot(
        self,
        receiver_id: str,
        *,
        name: str,
        constellation: str,
        health: str,
        analysis_state: Optional[str],
        score: Optional[float],
        latitude_deg: Optional[float],
        longitude_deg: Optional[float],
        distance_from_expected_m: Optional[float],
        speed_mps: Optional[float],
        has_fix: Optional[bool],
        hdop: Optional[float],
        num_satellites: Optional[int],
        avg_cn0_dbhz: Optional[float],
        triggered_rules: str,
    ) -> None:
        """Write one snapshot row, throttled to the configured interval.

        A no-op unless snapshot logging is enabled and at least
        interval_s has elapsed since the last row actually written for
        this receiver - regardless of how often the caller's own
        refresh loop runs.
        """
        if self._snapshot_writer is None:
            return
        assert self._config is not None
        interval_s = self._config.snapshot.interval_s
        now = time.monotonic()
        last = self._last_snapshot_mono.get(receiver_id)
        if last is not None and now - last < interval_s:
            return
        self._last_snapshot_mono[receiver_id] = now
        self._enqueue(
            SnapshotRecord(
                receiver_id=receiver_id,
                t_wall=time.time(),
                name=name,
                constellation=constellation,
                health=health,
                analysis_state=analysis_state,
                score=score,
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
                distance_from_expected_m=distance_from_expected_m,
                speed_mps=speed_mps,
                has_fix=has_fix,
                hdop=hdop,
                num_satellites=num_satellites,
                avg_cn0_dbhz=avg_cn0_dbhz,
                triggered_rules=triggered_rules,
            )
        )

    # -- queue/thread plumbing -----------------------------------------------

    def _enqueue(self, record: object) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            if not self._overflow_warned:
                _logger.warning(
                    "data logger queue is full; dropping records until it "
                    "drains (disk I/O may be stalled)"
                )
                self._overflow_warned = True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            try:
                self._dispatch(item)
            except Exception:  # noqa: BLE001 - never kill the writer thread
                _logger.exception("data logger failed to write a record; continuing")
            if self._queue.empty():
                self._overflow_warned = False

    def _dispatch(self, record: object) -> None:
        if isinstance(record, RawSentenceRecord):
            if self._raw_writer is not None:
                self._raw_writer.write(record)
        elif isinstance(record, ParsedMessageRecord):
            if self._parsed_writer is not None:
                self._parsed_writer.write(record)
        elif isinstance(record, SnapshotRecord):
            if self._snapshot_writer is not None:
                self._snapshot_writer.write(record)

    def close(self, timeout_s: float = 2.0) -> None:
        """Flush and stop the background thread. Safe to call if disabled."""
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout=timeout_s)
        self._thread = None
        for writer in (self._raw_writer, self._parsed_writer, self._snapshot_writer):
            if writer is not None:
                writer.close()
