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

# Live monitoring (Pi)
gnss-monitor --config config/live_rpi.yaml