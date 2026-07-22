import serial
import struct
import time

PORT = 'COM23'   # adjust to match COM23 mapping on the Pi
BAUD = 38400

# Confirmed u-blox M10 CFG-SIGNAL key IDs (from u-blox configuration item tables)
KEYS = {
    'GPS_ENA':    0x1031001f,
    'SBAS_ENA':   0x10310020,
    'GAL_ENA':    0x10310021,
    'BDS_ENA':    0x10310022,
    'QZSS_ENA':   0x10310024,
    'GLO_ENA':    0x10310025,
    'GLO_L1_ENA': 0x10310018,
    'GLO_L2_ENA': 0x1031001a,
}

LAYER_RAM = 0x01
LAYER_BBR = 0x02
LAYER_FLASH = 0x04


def ubx_checksum(msg_class, msg_id, payload):
    ck_a = ck_b = 0
    data = bytes([msg_class, msg_id]) + struct.pack('<H', len(payload)) + payload
    for b in data:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a, ck_b


def build_ubx(msg_class, msg_id, payload=b''):
    ck_a, ck_b = ubx_checksum(msg_class, msg_id, payload)
    return bytes([0xB5, 0x62, msg_class, msg_id]) + struct.pack('<H', len(payload)) + payload + bytes([ck_a, ck_b])


def build_valset(settings, layers):
    """settings: dict of key_name -> 0/1. Builds a single-message (non-transaction) CFG-VALSET."""
    version = 0x00       # 0 = simple set, not part of a multi-message transaction
    reserved0 = 0x0000
    payload = struct.pack('<BBH', version, layers, reserved0)
    for name, val in settings.items():
        payload += struct.pack('<IB', KEYS[name], val)
    return build_ubx(0x06, 0x8A, payload)


def read_ack(ser, timeout=2.0):
    """Scan incoming bytes for a UBX-ACK-ACK/NAK frame, ignoring interleaved NMEA chatter."""
    end_time = time.time() + timeout
    buf = b''
    while time.time() < end_time:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
        idx = buf.find(b'\xb5\x62\x05')
        if idx != -1 and len(buf) >= idx + 4:
            if buf[idx + 3] == 0x01:
                return "ACK"
            elif buf[idx + 3] == 0x00:
                return "NACK"
            buf = buf[idx + 4:]  # keep scanning past this partial match
    return "TIMEOUT (no ACK/NACK seen)"


def send_and_check(ser, settings, layers, label):
    cmd = build_valset(settings, layers)
    print(f"[{label}] sending: {cmd.hex()}")
    ser.reset_input_buffer()
    ser.write(cmd)
    result = read_ack(ser)
    print(f"[{label}] result: {result}")
    return result


if __name__ == "__main__":
    glonass_only = {
        'GPS_ENA':    0,
        'SBAS_ENA':   0,
        'GAL_ENA':    0,
        'BDS_ENA':    0,
        'QZSS_ENA':   0,
        'GLO_ENA':    1,
        'GLO_L1_ENA': 1,
        'GLO_L2_ENA': 1,
    }

    ser = serial.Serial(PORT, BAUD, timeout=0.5)

    # Try writing to RAM + BBR + Flash first
    result = send_and_check(ser, glonass_only, LAYER_RAM | LAYER_BBR | LAYER_FLASH, "RAM+BBR+Flash")

    if result == "NACK":
        # Some M10 ROM-based modules reject the Flash layer — fall back to RAM+BBR
        print("Flash layer rejected — retrying with RAM+BBR only (BBR needs a working backup battery/cap to persist)")
        send_and_check(ser, glonass_only, LAYER_RAM | LAYER_BBR, "RAM+BBR")

    ser.close()