You are joining an existing project that has already gone through multiple development phases. Read this entire prompt carefully before making any changes.

# PROJECT OVERVIEW

This project is called GNSS Monitor.

The final objective is NOT to detect spoofing directly.

The objective is to build a platform that continuously monitors multiple independent GNSS receivers and compares their behavior during a spoofing event so we can determine which GNSS constellation is most affected.

This is an engineering internship project.

The platform will run 24/7 on a Raspberry Pi 5 installed at a site.

The Raspberry Pi has:

- Raspberry Pi 5
- Waveshare USB to 8CH TTL adapter
- Four GNSS receivers connected simultaneously through UART
- No monitor
- No keyboard
- SSH access only

The same Python code must run on both Windows (development) and Linux (deployment).

Do NOT introduce platform-specific code unless absolutely necessary.

--------------------------------------------------

# CURRENT HARDWARE

Receiver 1
u-blox NEO-6M
UART
9600 baud

Receiver 2
Quectel LC29HEA
UART
460800 baud

Receiver 3
Quescan NeoM101612F
UART
38400 baud

Receiver 4
VOLLGO VG7779T156N0MA
UART
115200 baud

Each receiver is connected through the Waveshare USB-to-8CH TTL interface.

Windows example:

COM24
COM21
COM23
COM19

Linux deployment will use /dev/ttyUSB*.

--------------------------------------------------

# PROJECT PHILOSOPHY

We are intentionally developing this in stages.

Each stage must compile.

Each stage must be testable.

No "big bang" implementation.

Current focus is reliability.

GUI and advanced spoofing analysis are postponed.

--------------------------------------------------

# CURRENT IMPLEMENTATION STATUS

Completed:

✓ project structure

✓ parser

✓ framer

✓ evaluator

✓ replay mode

✓ multi-serial live mode

✓ configuration system

✓ unit tests

Current pytest status:

95 tests passing

Replay mode works.

Live Windows monitoring works.

Multiple receivers work simultaneously.

--------------------------------------------------

# SIMPLE MODE (CURRENT MVP)

This project currently runs in Simple Mode.

Simple Mode only determines whether each receiver is healthy.

For each receiver:

Expected coordinates are configured.

Incoming GGA messages are parsed.

If:

- valid fix
- within configured radius

status = HEALTHY

Otherwise:

OUT_OF_RANGE

NO_FIX

etc.

This is only an MVP.

Later phases will introduce proper spoofing analysis.

--------------------------------------------------

# FUTURE ANALYSIS

Do NOT implement yet.

Future versions may use:

C/N

HDOP

PDOP

satellite count

constellation comparison

timing anomalies

velocity anomalies

etc.

Ignore these for now.

--------------------------------------------------

# IMPORTANT DESIGN RULES

The parser is talker-independent.

GPGGA

GNGGA

GLGGA

GAGGA

must all become the same GGAMessage object.

Never branch based on talker ID.

--------------------------------------------------

# CODING STYLE

Prefer:

dataclasses

pathlib

typing

threading

logging

pyserial

Avoid:

global variables

platform-specific hacks

duplicated code

magic numbers

--------------------------------------------------

# CURRENT PRIORITY

We are preparing this software to run 24/7 unattended on the Raspberry Pi.

Robustness is more important than features.

--------------------------------------------------

# CRITICAL BUG TO FIX

There is one major bug that must be fixed before anything else.

Current behaviour:

When a GNSS receiver is unplugged while the application is running, the monitor continues processing the LAST RECEIVED NMEA sentence forever.

The receiver still appears HEALTHY even though no new data is arriving.

This is unacceptable.

--------------------------------------------------

Required behaviour:

Every receiver must behave like a live data stream.

Requirements:

1.

Only NEW bytes received from the serial port may generate NMEA sentences.

Never reuse previously parsed data.

2.

If no bytes are received for a configurable timeout (default 2 seconds):

status becomes

NO_DATA

3.

If the serial port disappears:

USB unplugged

driver reset

device removed

etc.

then:

close the port

mark receiver DISCONNECTED

periodically retry opening it

automatically reconnect

continue without restarting the application

4.

Receiver states should become:

HEALTHY

OUT_OF_RANGE

NO_FIX

NO_DATA

DISCONNECTED

ERROR

5.

The UI (current console output) must NEVER display stale coordinates.

Every displayed position must correspond to a newly received NMEA sentence.

If that sentence becomes older than the timeout,

remove it

display NO_DATA instead.

6.

Do not simply clear variables.

Find the root cause.

Explain exactly why the stale sentence is being reused.

Then implement the proper fix.

--------------------------------------------------

# EXPECTED OUTPUT

Please:

1.

Investigate the repository.

2.

Identify the root cause.

3.

Explain the bug.

4.

Implement the fix directly.

5.

Describe the changes.

6.

Provide a git commit message summarizing the fix.

Do not begin adding new features until this bug is fully resolved.