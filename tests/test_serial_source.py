"""Unit tests for SerialSource using an injected fake serial device.

No hardware or pyserial backend is exercised: a FakeSerial stands in for a
pyserial Serial object, letting us test line framing, partial reads, idle
behaviour, and disconnect propagation deterministically.
"""

from __future__ import annotations

import pytest

from gnss_monitor.sources import SerialSource


class FakeSerial:
    """Minimal stand-in for serial.Serial.

    Feeds bytes from a script of chunks. read(n) returns up to n bytes from
    the current position; when the script is exhausted it returns b"".
    Optionally raises OSError at a given call index to simulate a unplug.
    """

    def __init__(self, chunks: list[bytes], raise_at: int | None = None):
        self._data = bytearray(b"".join(chunks))
        self._pos = 0
        self._reads = 0
        self._raise_at = raise_at
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._data) - self._pos

    def read(self, n: int) -> bytes:
        self._reads += 1
        if self._raise_at is not None and self._reads >= self._raise_at:
            raise OSError("device disconnected")
        if self._pos >= len(self._data):
            return b""
        chunk = bytes(self._data[self._pos : self._pos + n])
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


def make_source(chunks, raise_at=None) -> SerialSource:
    fake = FakeSerial(chunks, raise_at=raise_at)
    return SerialSource(
        "test", "COM_X", 9600, serial_factory=lambda: fake
    )


def test_reads_complete_lines() -> None:
    src = make_source([b"$GPGGA,1*00\r\n", b"$GPRMC,2*00\r\n"])
    src.open()
    assert src.read_line() == "$GPGGA,1*00"
    assert src.read_line() == "$GPRMC,2*00"
    # No more data: returns None, never exhausted (live port).
    assert src.read_line() is None
    assert src.is_exhausted is False
    src.close()


def test_partial_line_reassembled_across_reads() -> None:
    # A line split across three chunks must be reassembled correctly.
    src = make_source([b"$GPG", b"GA,1", b"23*00\r\n"])
    src.open()
    # First reads return None until the newline arrives.
    line = None
    for _ in range(10):
        line = src.read_line()
        if line is not None:
            break
    assert line == "$GPGGA,123*00"
    src.close()


def test_read_before_open_raises() -> None:
    src = make_source([b""])
    with pytest.raises(RuntimeError):
        src.read_line()


def test_disconnect_propagates_as_oserror() -> None:
    src = make_source([b"$GPGGA*00\r\n"], raise_at=2)
    src.open()
    assert src.read_line() == "$GPGGA*00"
    # Next read hits the simulated unplug.
    with pytest.raises(OSError):
        for _ in range(5):
            src.read_line()
    src.close()


def test_close_is_idempotent_and_closes_device() -> None:
    fake = FakeSerial([b"x\r\n"])
    src = SerialSource("t", "COM1", 9600, serial_factory=lambda: fake)
    src.open()
    src.close()
    assert fake.closed is True
    src.close()  # no error second time