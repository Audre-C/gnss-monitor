import serial

def nmea_checksum(sentence_body):
    """sentence_body = everything between $ and *"""
    cksum = 0
    for c in sentence_body:
        cksum ^= ord(c)
    return f"{cksum:02X}"

def send_pair066(ser, gps, glonass, galileo, bds, qzss, navic):
    body = f"PAIR066,{gps},{glonass},{galileo},{bds},{qzss},{navic}"
    cmd = f"${body}*{nmea_checksum(body)}\r\n"
    ser.write(cmd.encode())
    return cmd

ser = serial.Serial('COM21', 460800, timeout=1)  
cmd = send_pair066(ser, gps=0, glonass=0, galileo=1, bds=0, qzss=0, navic=0)
print("Sent:", cmd.strip())

# Optional: save config so it persists across power cycles
ser.write(b"$PAIR513*3D\r\n")  # verify checksum for your firmware version