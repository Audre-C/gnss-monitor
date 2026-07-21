"""Live serial NMEA source (pyserial).

SerialSource implements the same NMEASource interface as FileReplaySource,
so nothing downstream (framer, parser, monitor, evaluator, dashboard) can
tell live serial data from replayed file data. Migrating Windows COM ports
to Linux /dev/ttyUSB* is purely a configuration change; this class is
identical either way.

pyserial is imported lazily inside open() so the package still imports and
replay mode still works on machines without pyserial installed.
"""

from __future__ import annotations

from typing import Callable, Optional

from gnss_monitor.sources.base import NMEASource

# If a partial line grows past this without a newline, drop the oldest
# bytes: it is almost certainly line noise or a baud mismatch, and we must
# not let the buffer grow without bound.
_MAX_BUFFER_BYTES = 8192


class SerialSource(NMEASource):
    """Reads NMEA lines from a serial port, one per read_line() call.

    read_line() is non-blocking in spirit: it returns the next complete
    line if one is buffered/available, otherwise None. A live port is
    never "finished", so is_exhausted is always False. I/O errors (e.g. an
    unplugged adapter) are allowed to propagate so the caller can trigger
    reconnection; this class does not retry on its own.

    A serial_factory can be injected for testing without hardware; it must
    return an object exposing .in_waiting, .read(n), and .close().
    """

    def __init__(
        self,
        source_id: str,
        port: str,
        baud: int,
        read_timeout_s: float = 0.1,
        serial_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        self._source_id = source_id
        self._port = port
        self._baud = baud
        self._read_timeout_s = read_timeout_s
        self._serial_factory = serial_factory
        self._serial: Optional[object] = None
        self._buffer = bytearray()

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def port(self) -> str:
        return self._port

    @property
    def baud(self) -> int:
        return self._baud

    def _make_serial(self) -> object:
        if self._serial_factory is not None:
            return self._serial_factory()
        import serial  # lazy: only needed for live mode

        return serial.Serial(
            port=self._port,
            baudrate=self._baud,
            timeout=self._read_timeout_s,
        )

    def open(self) -> None:
        if self._serial is not None:
            return
        self._serial = self._make_serial()
        self._buffer.clear()

    def read_line(self) -> Optional[str]:
        if self._serial is None:
            raise RuntimeError(
                f"serial source '{self._source_id}' read before open()"
            )

        newline = self._buffer.find(b"\n")
        if newline == -1:
            # in_waiting and read() may raise on disconnect; let them
            # propagate so the worker can reconnect.
            waiting = int(getattr(self._serial, "in_waiting", 0) or 0)
            if waiting > 0:
                chunk = self._serial.read(waiting)
            else:
                # Short blocking read: waits up to the timeout for a byte
                # and surfaces disconnects, without spinning the CPU.
                chunk = self._serial.read(1)

            if chunk:
                self._buffer.extend(chunk)
                newline = self._buffer.find(b"\n")

            if newline == -1:
                if len(self._buffer) > _MAX_BUFFER_BYTES:
                    del self._buffer[:-_MAX_BUFFER_BYTES]
                return None

        line = self._buffer[:newline]
        del self._buffer[: newline + 1]
        return line.decode("ascii", errors="replace").rstrip("\r\n")

    @property
    def is_exhausted(self) -> bool:
        return False

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
            self._serial = None
        self._buffer.clear()