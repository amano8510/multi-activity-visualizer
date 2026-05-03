# Multi Activity Visualizer Web

A lightweight browser version of the PyQt5 multi-activity visualization tool.

## Run

```bash
cd /Users/amano/Documents/Codex/2026-04-28/files-mentioned-by-the-user-multi/web_fit_visualizer
python3 -m pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:8765` in a browser and load one or more `.fit` or `.mfa` files.

## Features

- Load multiple FIT / MFA files
- Session summary table
- Multi-series charts by distance
  - Lap Pace
  - Pace
  - Heart Rate
  - Altitude
  - Cadence
  - Stride
  - Power
  - Stance Time
  - Vertical Ratio
- Route comparison for files with GPS data
- Route map with OpenStreetMap tiles
- Lap list and lap jump
- Route playback
- Cursor readouts for each metric
- Dark mode
- Smoothing adjustment
- CSV export
- MFA export
- Runs without an external web framework

## Possible Next Features

- PNG / HTML export
- Load FIT files from a server-side directory
