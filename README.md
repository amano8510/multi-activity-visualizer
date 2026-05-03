# Multi Activity Visualizer

Multi Activity Visualizer is a lightweight browser-based tool for comparing multiple FIT or MFA activities. It shows summaries, route maps, lap data, Lap Pace, and metric charts in one local web app.

The app runs as a small Python HTTP server and does not require an external web framework.

## Quick Start

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

Open the app in your browser:

```text
http://127.0.0.1:8765
```

## First-Time Workflow

1. Click `Open / Analyze`.
2. Select one or more FIT files, or select an existing MFA project file.
3. Check the Summary table and confirm that dates, distances, pace, heart rate, power, and temperature look correct.
4. Edit workout names directly in the `Workout` field if needed. These names are used in legends, charts, the map, and MFA Export.
5. Compare activities with the Route Map, Lap Chart, Lap Pace, and metric charts.
6. Move through the workout with the Cursor popup controls, Lap Chart rows, or graph/map cursor movement.
7. Turn on `Time Offset` if activities need to be aligned by time.
8. Use `MFA Export` to save the displayed activities together as one MFA project file so the comparison can be reopened later.

## Main Controls

- `Open / Analyze`: Select one or more FIT or MFA files and analyze them in one action.
- `CSV Export`: Export loaded summary, lap, and record data as CSV.
- `MFA Export`: Save all displayed activities together as a single MFA project file. The project includes workout names, start times, time offsets, GPS data, temperature, laps, records, and summary data.
- `Help`: Open the in-app manual popup.
- `Smoothing`: Adjust chart smoothing.
- `Cursor`: Show or hide the Cursor popup window.
- `Time Offset`: Show or hide per-workout time alignment controls.
- `Dark`: Toggle dark mode.

## Layout

### Left Panel

The left panel contains the app title, file actions, display settings, Time Offset controls, and Lap Chart.

### Main Area

The main area contains the Summary table, Route Map, Lap Pace chart, and metric charts.

## Summary Table

- The vertical color bar at the left of the Date column identifies each workout series.
- Workout names are editable directly in the table.
- Edited workout names are reflected in charts, the route map, legends, Cursor readouts, and MFA Export.
- Power and Temp are shown as record averages.
- Temp is displayed with one decimal place.

## Cursor

- The `Cursor` checkbox only toggles the Cursor popup window.
- Even when the Cursor popup is hidden, chart cursor lines, map markers, and chart-title readouts remain active.
- In the Cursor table, the Workout column is fixed and Distance plus metric columns can scroll horizontally.
- The Cursor popup includes `Start`, `Time`, and `Speed` controls for Route Map animation.

## Route Map

- GPS routes are drawn with the same series colors used throughout the app.
- Click a route legend item to focus and track that workout.
- While tracking, the map continuously follows the selected workout's cursor marker.
- Click or right-click the map to zoom around the selected point.

## Graphs

The app includes these chart types:

- Lap Pace
- Pace
- Heart Rate
- Altitude
- Cadence
- Stride
- Power
- Stance Time
- Vertical Ratio

Charts other than Lap Pace support distance-axis zooming. Values at the cursor position are displayed beside each chart title.

## Lap Chart

- For each lap, every workout is shown in one line with cumulative time, lap duration, pace, heart rate, and cadence.
- Click the workout/time area in a row to jump the animation to that lap's cumulative time.
- The current lap is highlighted with a green rectangle in the Lap Pace chart.

## Time Offset

- Enable `Time Offset` to adjust each workout's time alignment in seconds.
- Use offsets to align Route Map animation and cursor tracking between workouts.
- Time Offset panel visibility is saved in the browser.
- Per-workout offset values are saved in MFA Export.

## MFA Format

MFA is a custom project format for this app and the original Multi Activity Visualizer.

MFA Export stores the displayed activities as one project file, including:

- summary data
- lap data
- record data
- workout names
- start times
- time offsets
- GPS data
- temperature data

When an MFA file is loaded, saved workout names and time offsets are restored.

## Saved Display Settings

The following settings are saved in browser `localStorage` and restored the next time the app is opened:

- Smoothing
- Cursor popup visibility
- Time Offset panel visibility
- Dark mode

## Privacy Notes

FIT and MFA files can contain sensitive personal data, including GPS routes, timestamps, workout history, heart rate, and other health or activity metrics.

Do not commit personal `.fit` or `.mfa` files to a public repository. A typical `.gitignore` should exclude them:

```gitignore
*.fit
*.mfa
__pycache__/
*.pyc
.DS_Store
.env
.venv/
venv/
```

## Requirements

See [requirements.txt](requirements.txt).

The current app requires:

- Python 3
- `fitdecode`

## Running for Local Use

By default, the app runs on:

```text
http://127.0.0.1:8765
```

For local personal use, keep the server bound to `127.0.0.1`.

For deployment to a hosted web service, update the server configuration to bind to `0.0.0.0` and read the port from the hosting environment, for example:

```python
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8765))
```

Use hosted deployment with care because uploaded FIT/MFA files may contain private location and health data.
