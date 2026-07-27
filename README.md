# gnss-monitor

# Pre-Running
pip install -e .   

# Continuously refreshing dashboard (Ctrl-C to stop):
gnss-monitor --config config/replay.yaml

# Single final frame, no screen clearing (good for a quick check / CI):
gnss-monitor --config config/replay.yaml --once

# Just validate config and print the summary:
gnss-monitor --config config/replay.yaml --check

# Watch distances converge in real time (fast tick, small batches):
gnss-monitor --config config/replay.yaml --tick 0.1 --batch 20

# Live monitoring (Windows)
gnss-monitor --config config/live_windows.yaml


# Virtual Environment (Pi)
python3 -m venv venv
source venv/bin/activate

# Live monitoring (Pi)
gnss-monitor --config config/live_rpi.yaml

---

# Live Dashboard (TUI)

Live mode (`--mode live`, or `--mode auto` with an all-serial config)
now renders a fixed, htop/btop-style terminal dashboard that redraws in
place instead of scrolling: Header (time/uptime/version), Overall System
Status, one block per receiver (status, score, fix, position,
satellites, HDOP, distance from expected, last update), Triggered
Analysis (which rules fired and why, when the `analysis:` section is
configured), and a rolling Event Log of the last ~12 events (connects,
disconnects, fix acquired/lost, health/analysis state changes).

This only activates when stdout is an actual terminal (an interactive
SSH session). Anywhere else - `systemd`/`journalctl`, output redirected
to a file, piped through `less` - it automatically falls back to plain,
timestamped, append-only frames, which is what makes
`journalctl -u gnss-monitor -f` (see below) readable instead of full of
raw cursor-control escape codes.

Colors are minimal and only used for the health state itself: green
(OK), yellow (Warning), bold yellow standing in for orange (Potential
Spoofing - most terminals don't have a true ANSI "orange"), bold red
(Spoofing Detected). Field labels are dim gray; everything else is your
terminal's default color.

No new CLI flags were needed for this - the dashboard adapts to whatever
terminal it's attached to automatically.

---

# Deployment (Raspberry Pi)

This turns gnss-monitor into an unattended appliance: it starts on boot,
runs continuously, restarts itself if it crashes, and needs no monitor,
keyboard, or manual terminal session once installed. These steps assume
the four receivers are already wired to the Waveshare USB-to-8CH adapter,
which enumerates via the CH9344 driver as `/dev/ttyCH9344USB0`-
`/dev/ttyCH9344USB3` rather than `/dev/ttyUSB*` (confirm with `ls -l
/dev/ttyCH9344USB*`; adjust `config/live_rpi.yaml` if the mapping
differs on your unit).

## 1. Copy the project and create the virtual environment

```bash
sudo mkdir -p /opt/gnss-monitor
sudo chown "$USER" /opt/gnss-monitor
# copy (or git clone) the repository into /opt/gnss-monitor, then:
cd /opt/gnss-monitor
python3 -m venv .venv
source .venv/bin/activate
```

## 2. Install dependencies and the package

```bash
pip install --upgrade pip
pip install -e .
```

This installs pyserial/pydantic/PyYAML and registers the `gnss-monitor`
console script inside `.venv/bin/`, which is what the systemd unit below
calls directly (no need to activate the venv at run time).

## 3. Grant serial port access

The service user needs permission to open `/dev/ttyCH9344USB*`. On
Raspberry Pi OS these are owned by the `dialout` group:

```bash
sudo usermod -aG dialout "$USER"
# log out and back in (or reboot) for the group change to take effect
```

## 4. Review the configuration

Edit `config/live_rpi.yaml` and confirm the surveyed site position,
tolerances, and `/dev/ttyCH9344USB*` assignments are correct, then
validate it:

```bash
.venv/bin/gnss-monitor --config config/live_rpi.yaml --check
```

## 5. Install the systemd service

```bash
sudo cp deploy/systemd/gnss-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
```

If the project isn't installed at `/opt/gnss-monitor` or runs as a user
other than `pi`, edit `WorkingDirectory=`, `ExecStart=`, and `User=` in
`/etc/systemd/system/gnss-monitor.service` first.

## 6. Enable and start the service

```bash
# Start automatically on every boot:
sudo systemctl enable gnss-monitor

# Start it now:
sudo systemctl start gnss-monitor

# (equivalently, do both in one step: sudo systemctl enable --now gnss-monitor)
```

At this point all four receivers begin monitoring immediately, and the
service will keep running - and restart itself - across crashes and
reboots with no further action.

## 7. Everyday service management

```bash
# Current status (active/inactive, recent log lines, restart count):
sudo systemctl status gnss-monitor

# Stop it (closes every serial port cleanly via SIGTERM):
sudo systemctl stop gnss-monitor

# Restart it (e.g. after editing config/live_rpi.yaml):
sudo systemctl restart gnss-monitor

# Disable auto-start on boot (does not stop a currently running instance):
sudo systemctl disable gnss-monitor
```

## 8. Watching receiver health over SSH

```bash
# Follow live output - the same health table you'd see running the app
# in a terminal, continuously updated:
journalctl -u gnss-monitor -f

# Just the last 100 lines:
journalctl -u gnss-monitor -n 100

# Only warnings/errors (e.g. disconnects, reconnects, crashes):
journalctl -u gnss-monitor -p warning
```

The same information is also written to rotating log files under
`logs/app.log` (5 MB per file, 5 backups kept) inside the working
directory, independent of the systemd journal.

## Notes

- **Graceful shutdown**: `systemctl stop`, Ctrl+C, and `kill` (SIGTERM)
  all trigger the same clean shutdown path - every serial port is
  closed and every worker thread is joined before the process exits.
- **Crash recovery**: `Restart=always` with `RestartSec=5` means an
  unhandled crash is logged and the service restarts within 5 seconds.
  `StartLimitBurst=10` / `StartLimitIntervalSec=120` stop it from
  restart-looping forever if something is persistently broken (e.g. bad
  config) - `systemctl status` will show `start-limit-hit` in that case.
- **Windows/dev unaffected**: nothing above touches application code
  that runs on Windows - `deploy/systemd/` and this section are only
  ever read by the Pi deployment. Replay mode and Windows live mode
  (`config/live_windows.yaml`) continue to run exactly as before.
