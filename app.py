from __future__ import annotations

import json
import math
import pickle
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import fitdecode
except ImportError:  # pragma: no cover - shown as a friendly runtime error
    fitdecode = None


HOST = "127.0.0.1"
PORT = 8765
MAX_UPLOAD_BYTES = 120 * 1024 * 1024


@dataclass
class UploadedFile:
    filename: str
    data: bytes


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours}:{minutes:02}:{secs:02}"


def format_pace(total_seconds: float | int | None, distance_km: float | None) -> str:
    if not total_seconds or not distance_km:
        return "-"
    pace_seconds = total_seconds / distance_km
    return f"{int(pace_seconds // 60)}:{int(pace_seconds % 60):02}/km"


def clean_number(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def fit_value(frame: Any, field_name: str, default: Any = None) -> Any:
    try:
        if hasattr(frame, "has_field") and not frame.has_field(field_name):
            return default
        return frame.get_value(field_name)
    except Exception:
        return default


def semicircles_to_degrees(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * (180 / 2**31)
    except (TypeError, ValueError):
        return None


def parse_fit_file(path: Path, display_name: str) -> dict[str, Any]:
    if fitdecode is None:
        raise RuntimeError("fitdecode is not installed. Run: python3 -m pip install -r requirements.txt")

    summary = {
        "file_name": display_name,
        "date": "-",
        "start_time": "-",
        "workout_name": "-",
        "sport": "-",
        "distance_km": None,
        "duration": "-",
        "avg_pace": "-",
        "avg_hr": None,
        "elevation_gain_m": None,
        "calories": None,
        "avg_temp": None,
        "time_offset_seconds": 0,
    }
    records: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    first_timestamp = None

    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.records.FitDataMessage):
                continue

            if frame.name == "record":
                timestamp = fit_value(frame, "timestamp")
                if timestamp and first_timestamp is None:
                    first_timestamp = timestamp
                relative_time = None
                if timestamp and first_timestamp:
                    relative_time = (timestamp - first_timestamp).total_seconds()
                distance_m = fit_value(frame, "distance")
                speed = fit_value(frame, "enhanced_speed") or fit_value(frame, "speed")
                altitude = fit_value(frame, "enhanced_altitude") or fit_value(frame, "altitude")
                heart_rate = fit_value(frame, "heart_rate")
                cadence = fit_value(frame, "cadence")
                running_pitch = 2 * cadence if cadence is not None else None
                temperature = fit_value(frame, "temperature")
                power = fit_value(frame, "power")
                stance_time = fit_value(frame, "stance_time")
                vertical_oscillation = fit_value(frame, "vertical_oscillation")
                position_lat = fit_value(frame, "position_lat")
                position_long = fit_value(frame, "position_long")

                pace = None
                if speed and speed > 0:
                    pace = 1000 / (speed * 60)
                stride_length = None
                if pace and running_pitch:
                    stride_length = 1000 / (pace * running_pitch)
                vertical_ratio = None
                if vertical_oscillation is not None and stride_length:
                    vertical_ratio = 100 * vertical_oscillation / (stride_length * 1000)
                efficiency = None
                if speed and heart_rate:
                    efficiency = speed / heart_rate

                records.append({
                    "timestamp": clean_number(timestamp),
                    "relative_time": clean_number(relative_time),
                    "distance_km": distance_m / 1000 if distance_m is not None else None,
                    "speed": clean_number(speed),
                    "pace": clean_number(pace),
                    "heart_rate": clean_number(heart_rate),
                    "altitude": clean_number(altitude),
                    "cadence": clean_number(running_pitch),
                    "stride": clean_number(stride_length),
                    "power": clean_number(power),
                    "efficiency": clean_number(efficiency),
                    "temperature": clean_number(temperature),
                    "stance_time": clean_number(stance_time),
                    "vertical_oscillation": clean_number(vertical_oscillation),
                    "vertical_ratio": clean_number(vertical_ratio),
                    "position_lat": clean_number(semicircles_to_degrees(position_lat)),
                    "position_long": clean_number(semicircles_to_degrees(position_long)),
                })

            elif frame.name == "lap":
                total_distance = fit_value(frame, "total_distance")
                total_timer_time = fit_value(frame, "total_timer_time")
                avg_running_cadence = fit_value(frame, "avg_running_cadence")
                avg_cadence = fit_value(frame, "avg_cadence")
                cadence = avg_running_cadence if avg_running_cadence is not None else avg_cadence
                end_lat = semicircles_to_degrees(fit_value(frame, "end_position_lat"))
                end_lon = semicircles_to_degrees(fit_value(frame, "end_position_long"))
                cumulative_timer_seconds = sum((lap["timer_seconds"] or 0) for lap in laps) + (total_timer_time or 0)
                laps.append({
                    "lap_number": len(laps) + 1,
                    "distance_km": total_distance / 1000 if total_distance else None,
                    "cumulative_distance_km": sum((lap["distance_km"] or 0) for lap in laps) + (total_distance / 1000 if total_distance else 0),
                    "timer_seconds": clean_number(total_timer_time),
                    "cumulative_timer_seconds": clean_number(cumulative_timer_seconds),
                    "cumulative_time": format_duration(cumulative_timer_seconds),
                    "duration": format_duration(total_timer_time),
                    "avg_pace": format_pace(total_timer_time, total_distance / 1000 if total_distance else None),
                    "avg_pace_minutes": total_timer_time / (total_distance / 1000) / 60 if total_timer_time and total_distance else None,
                    "avg_hr": clean_number(fit_value(frame, "avg_heart_rate")),
                    "cadence": clean_number(2 * cadence if cadence is not None else None),
                    "end_lat": clean_number(end_lat),
                    "end_lon": clean_number(end_lon),
                })

            elif frame.name == "workout":
                summary["workout_name"] = fit_value(frame, "wkt_name") or "-"

            elif frame.name == "session":
                total_distance = fit_value(frame, "total_distance")
                total_elapsed_time = fit_value(frame, "total_elapsed_time")
                distance_km = total_distance / 1000 if total_distance else None
                start_time = fit_value(frame, "start_time")
                if start_time:
                    local_start = start_time.astimezone()
                    summary["date"] = local_start.date().isoformat()
                    summary["start_time"] = local_start.strftime("%H:%M:%S")

                summary.update({
                    "sport": fit_value(frame, "sport") or "-",
                    "distance_km": clean_number(distance_km),
                    "duration": format_duration(total_elapsed_time),
                    "avg_pace": format_pace(total_elapsed_time, distance_km),
                    "avg_hr": clean_number(fit_value(frame, "avg_heart_rate")),
                    "elevation_gain_m": clean_number(fit_value(frame, "total_ascent")),
                    "calories": clean_number(fit_value(frame, "total_calories")),
                })

    return {"summary": summary, "records": records, "laps": laps}


def list_value(values: list[Any], index: int, default: Any = None) -> Any:
    return values[index] if index < len(values) else default


def date_to_text(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value) if value else "-"


def pace_text_from_minutes(value: Any) -> str:
    if value is None:
        return "-"
    try:
        minutes = int(float(value))
        seconds = int(round((float(value) - minutes) * 60))
        if seconds == 60:
            minutes += 1
            seconds = 0
        return f"{minutes}:{seconds:02}/km"
    except (TypeError, ValueError):
        return "-"


def parse_duration_text(value: Any) -> int | None:
    if not value or value == "-":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value).split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = [int(float(part)) for part in parts]
            return hours * 3600 + minutes * 60 + seconds
        if len(parts) == 2:
            minutes, seconds = [int(float(part)) for part in parts]
            return minutes * 60 + seconds
    except ValueError:
        return None
    return None


def mfa_row_to_record(row: Any) -> dict[str, Any]:
    values = list(row) if isinstance(row, (list, tuple)) else []
    return {
        "timestamp": None,
        "relative_time": clean_number(list_value(values, 8)),
        "distance_km": clean_number(list_value(values, 0)),
        "speed": None,
        "pace": clean_number(list_value(values, 1)),
        "heart_rate": clean_number(list_value(values, 2)),
        "altitude": clean_number(list_value(values, 3)),
        "cadence": clean_number(list_value(values, 4)),
        "stride": clean_number(list_value(values, 5)),
        "power": clean_number(list_value(values, 6)),
        "efficiency": clean_number(list_value(values, 7)),
        "temperature": clean_number(list_value(values, 14)),
        "stance_time": clean_number(list_value(values, 9)),
        "vertical_oscillation": clean_number(list_value(values, 10)),
        "vertical_ratio": clean_number(list_value(values, 11)),
        "position_lat": clean_number(list_value(values, 12)),
        "position_long": clean_number(list_value(values, 13)),
    }


def mfa_lap_to_web_lap(lap: Any, cumulative_distance: float, cumulative_timer: float) -> dict[str, Any]:
    lap = lap if isinstance(lap, dict) else {}
    timer_seconds = clean_number(lap.get("total_timer_time"))
    distance_km = clean_number(lap.get("lap_distance"))
    cumulative_distance += distance_km or 0
    cumulative_timer += timer_seconds or 0
    pace_minutes = clean_number(lap.get("timer_lap_pace"))
    if pace_minutes is None and timer_seconds and distance_km:
        pace_minutes = timer_seconds / distance_km / 60
    return {
        "lap_number": lap.get("lap_number"),
        "distance_km": distance_km,
        "cumulative_distance_km": clean_number(lap.get("cumulative_distance", cumulative_distance)),
        "timer_seconds": timer_seconds,
        "cumulative_timer_seconds": clean_number(lap.get("cumulative_timer_time", cumulative_timer)),
        "cumulative_time": format_duration(lap.get("cumulative_timer_time", cumulative_timer)),
        "duration": format_duration(timer_seconds),
        "avg_pace": pace_text_from_minutes(pace_minutes),
        "avg_pace_minutes": pace_minutes,
        "avg_hr": clean_number(lap.get("avg_heart_rate")),
        "cadence": clean_number(lap.get("cadence")),
        "end_lat": clean_number(lap.get("end_lat")),
        "end_lon": clean_number(lap.get("end_lon")),
    }


def mfa_laps_to_web_laps(raw_laps: Any) -> list[dict[str, Any]]:
    cumulative_distance = 0.0
    cumulative_timer = 0.0
    laps = []
    for raw_lap in raw_laps if isinstance(raw_laps, list) else []:
        lap = mfa_lap_to_web_lap(raw_lap, cumulative_distance, cumulative_timer)
        cumulative_distance = lap["cumulative_distance_km"] or cumulative_distance
        cumulative_timer = lap["cumulative_timer_seconds"] or cumulative_timer
        laps.append(lap)
    return laps


def parse_fit_bytes(data: bytes, display_name: str) -> dict[str, Any] | None:
    try:
        with tempfile.NamedTemporaryFile(suffix=".fit") as tmp:
            tmp.write(data)
            tmp.flush()
            return parse_fit_file(Path(tmp.name), display_name)
    except Exception:
        return None


def merge_gps_data(activity: dict[str, Any], gps_activity: dict[str, Any] | None) -> dict[str, Any]:
    if not gps_activity:
        return activity

    gps_records = [
        record for record in gps_activity.get("records", [])
        if record.get("position_lat") is not None and record.get("position_long") is not None
    ]
    if gps_records:
        records = activity.get("records", [])
        for index, record in enumerate(records):
            gps_record = None
            relative_time = record.get("relative_time")
            if relative_time is not None:
                gps_record = min(
                    gps_records,
                    key=lambda item: abs((item.get("relative_time") or 0) - relative_time),
                )
            elif index < len(gps_records):
                gps_record = gps_records[index]
            if gps_record:
                record["position_lat"] = gps_record.get("position_lat")
                record["position_long"] = gps_record.get("position_long")

    gps_laps = gps_activity.get("laps", [])
    for lap, gps_lap in zip(activity.get("laps", []), gps_laps):
        lap["end_lat"] = gps_lap.get("end_lat")
        lap["end_lon"] = gps_lap.get("end_lon")

    if activity["summary"].get("start_time") == "-":
        activity["summary"]["start_time"] = gps_activity["summary"].get("start_time", "-")
    return activity


def mfa_stats_to_summary(stats: Any, activity_date: Any, source_name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    stats = list(stats) if isinstance(stats, (list, tuple)) else []
    workout_name = list_value(stats, 0, Path(source_name).stem) or Path(source_name).stem
    total_distance_m = list_value(stats, 1)
    distance_km = total_distance_m / 1000 if total_distance_m is not None else None
    duration_seconds = list_value(stats, 12) or list_value(stats, 11)
    if distance_km is None and records:
        distances = [record["distance_km"] for record in records if record["distance_km"] is not None]
        distance_km = max(distances) if distances else None
    return {
        "file_name": source_name,
        "date": date_to_text(activity_date),
        "start_time": "-",
        "workout_name": workout_name,
        "sport": list_value(stats, 25, "-") or "-",
        "distance_km": clean_number(distance_km),
        "duration": format_duration(duration_seconds),
        "avg_pace": format_pace(duration_seconds, distance_km),
        "avg_hr": clean_number(list_value(stats, 8)),
        "elevation_gain_m": clean_number(list_value(stats, 13)),
        "calories": clean_number(list_value(stats, 4)),
        "avg_temp": clean_number(list_value(stats, 19)) if clean_number(list_value(stats, 19)) is not None else clean_number(list_value(stats, 20)),
        "time_offset_seconds": 0,
    }


def parse_mfa_file(data: bytes, display_name: str) -> list[dict[str, Any]]:
    try:
        payload = pickle.loads(data)
    except Exception as exc:
        raise ValueError(f"{display_name}: could not parse MFA pickle data.") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{display_name}: MFAのルートがdictではありません。")

    all_data = payload.get("all_data")
    activity_dates = payload.get("activity_dates", [])
    all_lap_data = payload.get("all_lap_data", [])
    all_stats = payload.get("all_stats", [])
    input_files = payload.get("input_files", [])
    fit_binaries = payload.get("all_fitparse_binary", [])
    time_offsets = payload.get("time_offsets", [])
    start_times = payload.get("start_times", [])
    if not isinstance(all_data, list):
        raise ValueError(f"{display_name}: all_dataが見つかりません。")

    activities = []
    for index, rows in enumerate(all_data):
        source_name = Path(list_value(input_files, index, display_name) or display_name).name
        records = [mfa_row_to_record(row) for row in rows if isinstance(row, (list, tuple))]
        laps = mfa_laps_to_web_laps(list_value(all_lap_data, index, []))
        summary = mfa_stats_to_summary(
            list_value(all_stats, index, []),
            list_value(activity_dates, index),
            source_name,
            records,
        )
        start_time = list_value(start_times, index)
        if start_time:
            summary["start_time"] = str(start_time)
        summary["time_offset_seconds"] = clean_number(list_value(time_offsets, index, 0)) or 0
        activity = {"summary": summary, "records": records, "laps": laps}
        gps_activity = None
        fit_binary = list_value(fit_binaries, index)
        if fit_binary:
            gps_activity = parse_fit_bytes(fit_binary, source_name)
        elif list_value(input_files, index):
            fit_path = Path(list_value(input_files, index))
            if fit_path.exists():
                gps_activity = parse_fit_file(fit_path, fit_path.name)
        activities.append(merge_gps_data(activity, gps_activity))
    return activities


def activity_to_mfa_row(record: dict[str, Any]) -> list[Any]:
    return [
        record.get("distance_km"),
        record.get("pace"),
        record.get("heart_rate"),
        record.get("altitude"),
        record.get("cadence"),
        record.get("stride"),
        record.get("power"),
        record.get("efficiency"),
        record.get("relative_time"),
        record.get("stance_time"),
        record.get("vertical_oscillation"),
        record.get("vertical_ratio"),
        record.get("position_lat"),
        record.get("position_long"),
        record.get("temperature"),
    ]


def numeric_record_values(records: list[dict[str, Any]], key: str) -> list[float]:
    values = []
    for record in records:
        value = clean_number(record.get(key))
        if value is not None:
            values.append(value)
    return values


def activity_to_mfa_lap(lap: dict[str, Any]) -> dict[str, Any]:
    timer_seconds = lap.get("timer_seconds")
    distance_km = lap.get("distance_km")
    pace = timer_seconds / distance_km / 60 if timer_seconds and distance_km else None
    return {
        "lap_number": lap.get("lap_number"),
        "total_elapsed_time": timer_seconds,
        "total_timer_time": timer_seconds,
        "avg_heart_rate": lap.get("avg_hr"),
        "timer_lap_pace": pace,
        "elapsed_lap_pace": pace,
        "cadence": lap.get("cadence"),
        "lap_distance": distance_km,
        "cumulative_distance": lap.get("cumulative_distance_km"),
        "cumulative_timer_time": lap.get("cumulative_timer_seconds"),
        "cumulative_elapsed_time": lap.get("cumulative_timer_seconds"),
        "avg_stride": None,
        "avg_power": None,
        "avg_stance_time": None,
        "avg_vertical_ratio": None,
        "end_lat": lap.get("end_lat"),
        "end_lon": lap.get("end_lon"),
    }


def activities_to_mfa_bytes(activities: list[dict[str, Any]]) -> bytes:
    payload = {
        "all_data": [],
        "activity_dates": [],
        "all_lap_data": [],
        "sub_comment": [],
        "all_stats": [],
        "input_files": [],
        "all_fitparse_binary": [],
        "time_series_data": [],
        "time_offsets": [],
        "distance_offsets": [],
        "start_times": [],
    }
    for activity in activities:
        summary = activity.get("summary", {})
        records = activity.get("records", [])
        laps = activity.get("laps", [])
        temperatures = numeric_record_values(records, "temperature")
        avg_temperature = sum(temperatures) / len(temperatures) if temperatures else summary.get("avg_temp")
        min_temperature = min(temperatures) if temperatures else None
        max_temperature = max(temperatures) if temperatures else None
        payload["all_data"].append([activity_to_mfa_row(record) for record in records])
        payload["activity_dates"].append(summary.get("date") if summary.get("date") != "-" else "")
        payload["all_lap_data"].append([activity_to_mfa_lap(lap) for lap in laps])
        payload["sub_comment"].append("")
        payload["all_stats"].append([
            summary.get("workout_name") or Path(summary.get("file_name", "activity")).stem,
            (summary.get("distance_km") or 0) * 1000 if summary.get("distance_km") is not None else None,
            None,
            None,
            summary.get("calories"),
            None,
            None,
            None,
            summary.get("avg_hr"),
            None,
            None,
            parse_duration_text(summary.get("duration")),
            parse_duration_text(summary.get("duration")),
            summary.get("elevation_gain_m"),
            None,
            None,
            None,
            None,
            None,
            None,
            avg_temperature,
            min_temperature,
            max_temperature,
            None,
            summary.get("workout_name"),
            summary.get("sport"),
        ])
        payload["input_files"].append(summary.get("file_name", "activity.fit"))
        payload["all_fitparse_binary"].append(None)
        payload["time_offsets"].append(summary.get("time_offset_seconds", 0) or 0)
        payload["distance_offsets"].append(0)
        payload["start_times"].append(summary.get("start_time") if summary.get("start_time") != "-" else "")
    return pickle.dumps(payload)


def parse_multipart(body: bytes, content_type: str) -> list[UploadedFile]:
    if "multipart/form-data" not in content_type:
        raise ValueError("multipart/form-dataとして送信されていません。")

    message_bytes = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=policy.default).parsebytes(message_bytes)
    if not message.is_multipart():
        raise ValueError("Could not parse upload body as multipart data.")

    uploads: list[UploadedFile] = []

    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        if not filename.lower().endswith((".fit", ".mfa")):
            continue

        data = part.get_payload(decode=True)
        if not data:
            continue
        uploads.append(UploadedFile(Path(filename).name, data))

    return uploads


INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Multi Activity Visualizer</title>
  <style>
    :root { color-scheme: light; --line: #d6dde8; --accent: #0f766e; --bg: #f6f8fb; --panel: #ffffff; --text: #17202a; --muted: #64748b; --cursor-space: 560px; }
    :root[data-theme="dark"] { color-scheme: dark; --line: #334155; --accent: #2dd4bf; --bg: #10151f; --panel: #17202a; --text: #e8edf5; --muted: #94a3b8; }
    @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --line: #334155; --accent: #2dd4bf; --bg: #10151f; --panel: #17202a; --text: #e8edf5; --muted: #94a3b8; } }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    main { max-width: 1360px; margin: 0 auto; padding: 18px; }
    h1 { margin: 0; font-size: 24px; }
    h2 { margin: 0 0 10px; font-size: 15px; }
    input[type=file].file-input { display: none; }
    button { background: var(--accent); border: 0; color: #fff; padding: 9px 14px; border-radius: 6px; font-weight: 700; cursor: pointer; }
    button.secondary { background: transparent; border: 1px solid var(--line); color: var(--text); }
    button:disabled { opacity: .55; cursor: wait; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; margin-bottom: 16px; }
    #message { background: transparent; border: 0; border-radius: 0; margin: 0 0 8px; padding: 0; }
    #message:empty { display: none; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { border-bottom: 1px solid var(--line); padding: 8px 7px; text-align: left; white-space: nowrap; }
    th { font-size: 12px; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); }
    .summary-date { align-items: center; display: inline-flex; gap: 8px; }
    .summary-bar { border-radius: 3px; display: inline-block; flex: 0 0 auto; height: 22px; width: 4px; }
    .workout-name-input { background: transparent; border: 1px solid transparent; border-radius: 5px; color: var(--text); font: inherit; min-width: 80px; padding: 4px 6px; width: 80px; }
    .workout-name-input:focus { background: var(--bg); border-color: var(--accent); outline: none; }
    .analysis-layout { display: block; }
    .side-panel { padding: 0 18px 18px; }
    .app-title { margin: 0 0 14px; padding: 0 2px; }
    .app-title h1 { font-size: 22px; }
    .lap-panel { max-height: 48vh; overflow: auto; }
    .side-panel.cursor-off .lap-panel { max-height: calc(100vh - 170px); }
    .lap-list { display: grid; gap: 10px; font-size: 15px; }
    .lap-card { border-bottom: 1px solid var(--line); padding-bottom: 10px; }
    .lap-card:last-child { border-bottom: 0; }
    .lap-row { align-items: baseline; border-left: 3px solid var(--line); display: flex; gap: 12px; padding-left: 8px; margin-top: 7px; min-width: 0; }
    .lap-main { font-weight: 700; }
    .lap-jump { align-items: center; appearance: none; background: transparent; border: 0; border-radius: 3px; color: var(--text); cursor: pointer; display: inline-flex; flex: 0 0 auto; font: inherit; font-weight: 700; gap: 8px; padding: 0; text-align: left; }
    .lap-jump:hover { color: var(--accent); text-decoration: underline; }
    .lap-jump-time { border-left: 1px solid var(--line); color: var(--muted); font-variant-numeric: tabular-nums; padding-left: 8px; }
    .lap-metrics { color: var(--muted); display: flex; flex: 1 1 auto; flex-wrap: nowrap; gap: 10px; font-size: 16px; margin-top: 0; min-width: 0; overflow-x: auto; }
    .lap-metrics span { white-space: nowrap; }
    canvas { width: 100%; display: block; }
    input[type=range] { accent-color: var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .chart-wide { grid-column: 1 / -1; }
    .chart-heading { align-items: center; display: flex; gap: 12px; margin-bottom: 8px; min-height: 24px; }
    .chart-heading h2 { flex: 0 0 auto; margin: 0; }
    .chart-readout { color: var(--muted); display: flex; flex: 1 1 auto; gap: 8px; min-width: 0; overflow: hidden; white-space: nowrap; }
    .chart-readout .readout-position { border-left: 0; color: var(--muted); flex: 0 0 auto; font-weight: 700; padding-left: 0; }
    .chart-readout span { border-left: 3px solid var(--line); color: var(--text); flex: 0 1 auto; font-size: 12px; min-width: 0; overflow: hidden; padding-left: 6px; text-overflow: ellipsis; }
    .chart canvas { height: 260px; }
    .chart-wide canvas { height: 300px; }
    .map canvas { height: 520px; }
    .map-controls { display: grid; grid-template-columns: auto minmax(180px, 1fr) auto auto minmax(110px, 180px) auto; gap: 10px; align-items: center; margin: 0 0 10px; }
    .map-controls button { padding: 7px 12px; }
    .toolbar { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; display: grid; gap: 10px; margin-bottom: 16px; padding: 12px; }
    .toolbar-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .settings-row { align-items: center; }
    .toolbar label { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; }
    .smoothing-control input { width: 110px; }
    .cursor-popup { box-shadow: 0 18px 42px rgba(15, 23, 42, .24); left: 18px; margin: 0; max-height: 42vh; overflow: auto; position: fixed; right: 18px; top: 18px; z-index: 40; }
    .cursor-toggle { display: flex; align-items: center; gap: 8px; font-size: 14px; }
    .cursor-grid { font-size: 12px; overflow-x: auto; }
    .cursor-table { border-collapse: separate; border-spacing: 0; min-width: 760px; width: 100%; }
    .cursor-table th, .cursor-table td { padding: 6px 7px; }
    .cursor-table td { color: var(--text); }
    .cursor-table .compact { max-width: 72px; min-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; width: 72px; }
    .cursor-table th:first-child, .cursor-workout { background: var(--panel); left: 0; position: sticky; width: 150px; z-index: 2; }
    .cursor-table th:first-child { z-index: 3; }
    .cursor-workout { border-left: 4px solid var(--line); box-shadow: 1px 0 0 var(--line); font-weight: 700; max-width: 150px; overflow: hidden; text-overflow: ellipsis; }
    .cursor-controls { margin: 12px 0 0; }
    .offset-list { display: grid; gap: 8px; }
    .offset-row { align-items: center; border-left: 3px solid var(--line); display: grid; gap: 8px; grid-template-columns: minmax(0, 1fr) 88px; padding-left: 8px; }
    .offset-row label { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .offset-row input { background: var(--bg); border: 1px solid var(--line); border-radius: 6px; color: var(--text); padding: 6px 7px; width: 100%; }
    .muted { color: var(--muted); }
    .error { color: #dc2626; font-weight: 700; }
    .hidden { display: none; }
    .modal-backdrop { align-items: center; background: rgba(15, 23, 42, .42); display: flex; inset: 0; justify-content: center; padding: 24px; position: fixed; z-index: 80; }
    .modal-backdrop.hidden { display: none; }
    .help-modal { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 24px 64px rgba(15, 23, 42, .32); display: flex; flex-direction: column; height: min(86vh, 900px); max-width: 980px; overflow: hidden; width: min(980px, 100%); }
    .help-modal-header { align-items: center; border-bottom: 1px solid var(--line); display: flex; gap: 12px; justify-content: space-between; padding: 12px 14px; }
    .help-modal-header h2 { margin: 0; }
    .help-modal iframe { border: 0; flex: 1 1 auto; width: 100%; }
    @media (min-width: 1280px) {
      body { padding-left: var(--cursor-space); }
      main { max-width: 1360px; margin-left: 0; margin-right: 0; }
      .side-panel { position: fixed; top: 18px; left: 0; bottom: 0; z-index: 20; width: var(--cursor-space); overflow: auto; }
      .cursor-popup { left: calc(var(--cursor-space) + 18px); max-width: 1360px; right: 18px; }
      .lap-panel { width: 520px; max-width: calc(var(--cursor-space) - 36px); }
    }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } .lap-panel { max-height: none; } .cursor-popup { max-height: 45vh; } .map-controls { grid-template-columns: 1fr 1fr; } .map-controls input[type=range] { grid-column: 1 / -1; } .toolbar-row { align-items: stretch; flex-direction: column; } }
  </style>
</head>
<body>
  <main>
    <section id="message" class="muted"></section>
    <section id="summarySection" class="hidden">
      <table>
        <thead><tr><th>Date</th><th>Start</th><th>Workout</th><th>Type</th><th>Distance</th><th>Time</th><th>Pace</th><th>Avg HR</th><th>Power</th><th>Temp</th><th>Elevation</th><th>Calories</th></tr></thead>
        <tbody id="summaryBody"></tbody>
      </table>
    </section>
    <section id="mapSection" class="map hidden">
      <h2>Route Map</h2>
      <canvas id="routeMap" width="1320" height="520"></canvas>
      <div id="mapNote" class="muted"></div>
    </section>
    <div id="analysisLayout" class="analysis-layout hidden">
      <div id="charts" class="grid"></div>
    </div>
    <section id="cursorPopup" class="cursor-popup hidden">
      <h2>Cursor</h2>
      <div id="cursorReadout" class="cursor-grid"></div>
      <div class="map-controls cursor-controls">
        <button id="playButton" type="button">Start</button>
        <input id="timeSlider" type="range" min="0" max="0" step="0.1" value="0">
        <span id="timeLabel">Time: 0:00:00</span>
        <span>Speed</span>
        <input id="speedSlider" type="range" min="0.1" max="20" step="0.1" value="1">
        <span id="speedLabel">1.0x</span>
      </div>
    </section>
  </main>
  <aside class="side-panel">
    <div class="app-title">
      <h1>Multi Activity Visualizer</h1>
      <div class="muted">Compare multiple activities from FIT or MFA files</div>
    </div>
    <div class="toolbar">
      <div class="toolbar-row">
        <input id="files" class="file-input" name="files" type="file" accept=".fit,.mfa" multiple>
        <button id="analyzeButton" type="button">Open / Analyze</button>
        <button id="exportButton" class="secondary" type="button" disabled>CSV Export</button>
        <button id="mfaExportButton" class="secondary" type="button" disabled>MFA Export</button>
        <button id="helpButton" class="secondary" type="button">Help</button>
      </div>
      <div class="toolbar-row settings-row">
        <label class="smoothing-control">
          Smoothing
          <input id="smoothingSlider" type="range" min="1" max="21" step="2" value="9">
          <span id="smoothingLabel">9</span>
        </label>
        <label class="cursor-toggle">
          <input id="cursorToggle" type="checkbox" checked>
          Cursor
        </label>
        <label class="cursor-toggle">
          <input id="timeOffsetToggle" type="checkbox">
          Time Offset
        </label>
        <label class="cursor-toggle">
          <input id="themeToggle" type="checkbox">
          Dark
        </label>
      </div>
    </div>
    <section id="timeOffsetSection" class="lap-panel hidden">
      <h2>Time Offset</h2>
      <div id="timeOffsetControls" class="offset-list"></div>
    </section>
    <section id="lapSection" class="lap-panel hidden">
      <h2>Lap Chart</h2>
      <div id="lapChart"></div>
    </section>
  </aside>
  <div id="helpModal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="helpTitle">
    <div class="help-modal">
      <div class="help-modal-header">
        <h2 id="helpTitle">Manual</h2>
        <button id="helpCloseButton" class="secondary" type="button">Close</button>
      </div>
      <iframe id="helpFrame" title="Multi Activity Visualizer Manual"></iframe>
    </div>
  </div>
  <script>
    const filesInput = document.querySelector("#files");
    const button = document.querySelector("#analyzeButton");
    const message = document.querySelector("#message");
    const sidePanel = document.querySelector(".side-panel");
    const cursorToggle = document.querySelector("#cursorToggle");
    const timeOffsetToggle = document.querySelector("#timeOffsetToggle");
    const cursorPopup = document.querySelector("#cursorPopup");
    const cursorReadout = document.querySelector("#cursorReadout");
    const summarySection = document.querySelector("#summarySection");
    const analysisLayout = document.querySelector("#analysisLayout");
    const lapSection = document.querySelector("#lapSection");
    const lapChart = document.querySelector("#lapChart");
    const timeOffsetSection = document.querySelector("#timeOffsetSection");
    const timeOffsetControls = document.querySelector("#timeOffsetControls");
    const mapSection = document.querySelector("#mapSection");
    const summaryBody = document.querySelector("#summaryBody");
    const charts = document.querySelector("#charts");
    const routeMap = document.querySelector("#routeMap");
    const mapNote = document.querySelector("#mapNote");
    const themeToggle = document.querySelector("#themeToggle");
    const exportButton = document.querySelector("#exportButton");
    const mfaExportButton = document.querySelector("#mfaExportButton");
    const helpButton = document.querySelector("#helpButton");
    const helpModal = document.querySelector("#helpModal");
    const helpCloseButton = document.querySelector("#helpCloseButton");
    const helpFrame = document.querySelector("#helpFrame");
    const smoothingSlider = document.querySelector("#smoothingSlider");
    const smoothingLabel = document.querySelector("#smoothingLabel");
    const playButton = document.querySelector("#playButton");
    const timeSlider = document.querySelector("#timeSlider");
    const timeLabel = document.querySelector("#timeLabel");
    const speedSlider = document.querySelector("#speedSlider");
    const speedLabel = document.querySelector("#speedLabel");
    const colors = ["#0f766e", "#2563eb", "#c2410c", "#7c3aed", "#be123c", "#15803d", "#a16207", "#0891b2"];
    const metrics = [
      { key: "lap_pace", label: "Lap Pace", unit: "min/km", source: "laps" },
      { key: "pace", label: "Pace", unit: "min/km", clamp: [2, 12] },
      { key: "heart_rate", label: "Heart Rate", unit: "bpm" },
      { key: "altitude", label: "Altitude", unit: "m" },
      { key: "cadence", label: "Cadence", unit: "spm" },
      { key: "stride", label: "Stride", unit: "m" },
      { key: "power", label: "Power", unit: "W" },
      { key: "stance_time", label: "Stance Time", unit: "ms" },
      { key: "vertical_ratio", label: "Vertical Ratio", unit: "%" }
    ];
    const smoothing = {
      pace: 10,
      heart_rate: 5,
      altitude: 5,
      cadence: 9,
      stride: 5,
      power: 9,
      stance_time: 3,
      vertical_ratio: 3
    };
    const settingsKey = "multiActivityVisualizerSettings";
    const savedSettings = loadSettings();
    applySavedSettings(savedSettings);
    let smoothingScale = Number(smoothingSlider.value) / 9;
    let activities = [];
    let cursorDistance = null;
    let cursorEnabled = true;
    let chartModels = new Map();
    let chartZooms = new Map();
    let chartDrag = null;
    let mapModel = null;
    let mapView = null;
    let focusedRouteActivityIndex = null;
    let animationTime = 0;
    let animationMaxTime = 0;
    let animationTimer = null;
    let lastAnimationStamp = null;
    const tileSize = 256;
    const tileCache = new Map();
    const tileUrlTemplate = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

    function loadSettings() {
      try {
        return JSON.parse(localStorage.getItem(settingsKey) || "{}");
      } catch {
        return {};
      }
    }

    function saveSettings() {
      const settings = {
        smoothing: Number(smoothingSlider.value),
        cursor: cursorToggle.checked,
        timeOffset: timeOffsetToggle.checked,
        theme: document.documentElement.dataset.theme || ""
      };
      try {
        localStorage.setItem(settingsKey, JSON.stringify(settings));
      } catch {
        // Ignore storage failures; controls should remain usable.
      }
    }

    function applySavedSettings(settings) {
      const smoothingValue = Number(settings.smoothing);
      if (Number.isFinite(smoothingValue) && smoothingValue >= 1 && smoothingValue <= 21 && smoothingValue % 2 === 1) {
        smoothingSlider.value = String(smoothingValue);
        smoothingLabel.textContent = String(smoothingValue);
      }
      if (typeof settings.cursor === "boolean") cursorToggle.checked = settings.cursor;
      if (typeof settings.timeOffset === "boolean") timeOffsetToggle.checked = settings.timeOffset;
      if (settings.theme === "dark" || settings.theme === "light") {
        document.documentElement.dataset.theme = settings.theme;
      }
    }

    function valueText(value, suffix = "") {
      return value === null || value === undefined || value === "-" ? "-" : `${value}${suffix}`;
    }

    function fixedValueText(value, digits, suffix = "") {
      const number = Number(value);
      return Number.isFinite(number) ? `${number.toFixed(digits)}${suffix}` : "-";
    }

    function averageRecordValue(activity, key, digits = 0) {
      const values = activity.records
        .map(record => Number(record[key]))
        .filter(value => Number.isFinite(value));
      if (!values.length) return null;
      const average = values.reduce((sum, value) => sum + value, 0) / values.length;
      return Number(average.toFixed(digits));
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function csvCell(value) {
      if (value === null || value === undefined) return "";
      const text = String(value);
      return /[",\\n\\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
    }

    function downloadText(filename, content, type = "text/csv;charset=utf-8") {
      const blob = new Blob([content], { type });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function rowsToCsv(headers, rows) {
      return [
        headers.map(csvCell).join(","),
        ...rows.map(row => headers.map(header => csvCell(row[header])).join(","))
      ].join("\\n");
    }

    function exportCsv() {
      if (!activities.length) return;
      const headers = [
        "row_type", "file_name", "date", "start_time", "workout_name", "sport",
        "lap_number", "timestamp", "relative_time", "distance_km", "cumulative_distance_km",
        "duration", "cumulative_time", "avg_pace", "pace", "avg_hr", "heart_rate",
        "elevation_gain_m", "altitude", "calories", "cadence", "stride", "power",
        "stance_time", "vertical_ratio", "position_lat", "position_long"
      ];
      const rows = activities.flatMap(activity => [
        { row_type: "summary", ...activity.summary },
        ...activity.laps.map(lap => ({ row_type: "lap", file_name: activity.summary.file_name, ...lap })),
        ...activity.records.map(record => ({ row_type: "record", file_name: activity.summary.file_name, ...record }))
      ]);
      downloadText("fit_activity_export.csv", rowsToCsv(headers, rows));
    }

    async function saveMfaBlob(blob) {
      const defaultName = activities.length === 1
        ? `${(activities[0].summary.workout_name || "activity").replace(/[\\\\/:*?"<>|]+/g, "_")}.mfa`
        : "multi_activity_export.mfa";
      if (window.showSaveFilePicker) {
        try {
          const handle = await window.showSaveFilePicker({
            suggestedName: defaultName,
            types: [{
              description: "MFA Project",
              accept: { "application/octet-stream": [".mfa"] }
            }]
          });
          const writable = await handle.createWritable();
          await writable.write(blob);
          await writable.close();
          return;
        } catch (error) {
          if (error.name === "AbortError") return;
          throw error;
        }
      }
      const typedName = window.prompt("Save MFA as", defaultName);
      if (typedName === null) return;
      const fileName = (typedName.trim() || defaultName).toLowerCase().endsWith(".mfa")
        ? (typedName.trim() || defaultName)
        : `${typedName.trim() || defaultName}.mfa`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function exportMfa() {
      if (!activities.length) return;
      mfaExportButton.disabled = true;
      fetch("/api/export_mfa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activities })
      }).then(response => {
        if (!response.ok) throw new Error("MFA export failed.");
        return response.blob();
      }).then(saveMfaBlob).catch(error => {
        message.className = "error";
        message.textContent = error.message;
      }).finally(() => {
        mfaExportButton.disabled = activities.length === 0;
      });
    }

    function fillSummary() {
      summaryBody.innerHTML = activities.map((activity, index) => {
        const { summary } = activity;
        const avgPower = averageRecordValue(activity, "power");
        const avgTemp = summary.avg_temp ?? averageRecordValue(activity, "temperature", 1);
        return `
          <tr>
            <td><span class="summary-date"><span class="summary-bar" style="background:${colors[index % colors.length]}"></span>${summary.date}</span></td><td>${summary.start_time}</td>
            <td><input class="workout-name-input" data-index="${index}" value="${escapeHtml(summary.workout_name)}"></td><td>${summary.sport}</td>
            <td>${summary.distance_km ? summary.distance_km.toFixed(2) + " km" : "-"}</td><td>${summary.duration}</td><td>${summary.avg_pace}</td>
            <td>${valueText(summary.avg_hr, " bpm")}</td><td>${valueText(avgPower, " W")}</td><td>${fixedValueText(avgTemp, 1, " C")}</td><td>${valueText(summary.elevation_gain_m, " m")}</td><td>${valueText(summary.calories, " Cal")}</td>
          </tr>`;
      }).join("");
    }

    function activityTimeOffset(activity) {
      return Number(activity.summary.time_offset_seconds) || 0;
    }

    function hasTimeOffsets() {
      return activities.some(activity => activityTimeOffset(activity) !== 0);
    }

    function drawTimeOffsetControls() {
      if (!activities.length) {
        timeOffsetControls.innerHTML = "";
        return;
      }
      timeOffsetControls.innerHTML = activities.map((activity, index) => `
        <div class="offset-row" style="border-left-color:${colors[index % colors.length]}">
          <label for="timeOffset-${index}" title="${activity.summary.date} ${activity.summary.workout_name}">${activity.summary.date} ${activity.summary.workout_name}</label>
          <input id="timeOffset-${index}" type="number" step="1" value="${activityTimeOffset(activity)}" data-index="${index}">
        </div>
      `).join("");
    }

    function drawLapChart() {
      const maxLaps = Math.max(0, ...activities.map(activity => activity.laps.length));
      if (!maxLaps) {
        lapChart.innerHTML = '<div class="muted">No lap data.</div>';
        return;
      }
      const laps = Array.from({ length: maxLaps }, (_, lapIndex) => {
        const rows = activities.map((activity, activityIndex) => {
          const lap = activity.laps[lapIndex];
          if (!lap) return "";
          const pace = lap.avg_pace_minutes;
          return `<div class="lap-row" style="border-left-color:${colors[activityIndex % colors.length]}">
              <button class="lap-jump" type="button" data-time="${lap.cumulative_timer_seconds || 0}">
                <span>${activity.summary.date}</span>
                <span class="lap-jump-time">${lap.cumulative_time}</span>
              </button>
              <div class="lap-metrics">
                <span>${lap.duration}</span>
                <span>${lap.avg_pace}</span>
                <span>${valueText(lap.avg_hr, " bpm")}</span>
                <span>${valueText(lap.cadence, " spm")}</span>
              </div>
            </div>`;
        }).join("");
        return `<div class="lap-card"><div class="lap-main">Lap ${lapIndex + 1}</div>${rows}</div>`;
      }).join("");
      lapChart.innerHTML = `<div class="lap-list">${laps}</div>`;
    }

    function parsePace(text) {
      if (!text || text === "-") return null;
      const [minutes, seconds] = text.split("/")[0].split(":").map(Number);
      return minutes + seconds / 60;
    }

    function lapPoints(activity) {
      let distance = 0;
      return activity.laps.map((lap, index) => {
        const pace = parsePace(lap.avg_pace);
        const start = distance;
        distance += lap.distance_km || 0;
        return { x: index + 1, start, end: distance, lapNumber: index + 1, y: pace };
      }).filter(p => p.x !== null && p.y !== null);
    }

    function median(values) {
      const sorted = values.slice().sort((a, b) => a - b);
      const mid = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    }

    function medianFilterPoints(points, windowSize) {
      if (windowSize <= 1 || points.length < 2) return points;
      const half = Math.floor(windowSize / 2);
      return points.map((point, index) => {
        const values = [];
        for (let offset = -half; offset <= half; offset += 1) {
          const clampedIndex = Math.max(0, Math.min(points.length - 1, index + offset));
          values.push(points[clampedIndex].y);
        }
        return { ...point, y: median(values) };
      });
    }

    function movingAveragePoints(points, windowSize) {
      if (windowSize <= 1 || points.length < windowSize) return points;
      const smoothed = [];
      for (let index = windowSize - 1; index < points.length; index += 1) {
        let sum = 0;
        for (let offset = 0; offset < windowSize; offset += 1) {
          sum += points[index - offset].y;
        }
        smoothed.push({ ...points[index], y: sum / windowSize });
      }
      return smoothed;
    }

    function filteredMetricPoints(points, metric) {
      if (metric.source === "laps") return points;
      const baseWindow = smoothing[metric.key] || 5;
      const windowSize = Math.max(1, Math.round(baseWindow * smoothingScale));
      const spikeFiltered = medianFilterPoints(points, windowSize % 2 ? windowSize : windowSize + 1);
      if (metric.key === "power") return spikeFiltered;
      return movingAveragePoints(spikeFiltered, windowSize);
    }

    function metricSeries(metric) {
      return activities.map((activity, index) => {
        const rawPoints = metric.source === "laps"
          ? lapPoints(activity)
          : activity.records.filter(r => r.distance_km !== null && r[metric.key] !== null).map(r => ({ x: r.distance_km, y: r[metric.key] }));
        const points = filteredMetricPoints(rawPoints, metric);
        return {
          name: `${activity.summary.date} ${activity.summary.workout_name}`,
          color: colors[index % colors.length],
          activityIndex: index,
          points
        };
      }).filter(series => series.points.length > 1);
    }

    function nearestByDistance(points, distance) {
      if (!points.length || distance === null) return null;
      const containing = points.find(point => point.start !== undefined && point.end !== undefined && distance >= point.start && distance <= point.end);
      if (containing) return containing;
      let best = points[0];
      let bestDiff = Math.abs(points[0].x - distance);
      for (const point of points) {
        const diff = Math.abs(point.x - distance);
        if (diff < bestDiff) {
          best = point;
          bestDiff = diff;
        }
      }
      return best;
    }

    function nearestByTime(points, time) {
      const usable = points.filter(point => point.t !== null && point.t !== undefined);
      if (!usable.length) return null;
      let best = usable[0];
      let bestDiff = Math.abs(best.t - time);
      for (const point of usable) {
        const diff = Math.abs(point.t - time);
        if (diff < bestDiff) {
          best = point;
          bestDiff = diff;
        }
      }
      return best;
    }

    function formatMetricValue(value, unit) {
      if (value === null || value === undefined) return "-";
      if (unit === "min/km") {
        let minutes = Math.floor(value);
        let seconds = Math.round((value - minutes) * 60);
        if (seconds === 60) {
          minutes += 1;
          seconds = 0;
        }
        return `${minutes}:${seconds.toString().padStart(2, "0")}/km`;
      }
      if (unit === "m" || unit === "%") return `${value.toFixed(2)} ${unit}`;
      if (unit === "bpm" || unit === "spm" || unit === "W" || unit === "ms") return `${Math.round(value)} ${unit}`;
      return `${value.toFixed(2)} ${unit}`;
    }

    function formatDurationLabel(seconds) {
      const safeSeconds = Math.max(0, Math.floor(seconds || 0));
      const hours = Math.floor(safeSeconds / 3600);
      const minutes = Math.floor((safeSeconds % 3600) / 60);
      const secs = safeSeconds % 60;
      return `${hours}:${minutes.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
    }

    function formatAxisValue(value, metric) {
      if (metric.unit === "min/km") {
        let minutes = Math.floor(value);
        let seconds = Math.round((value - minutes) * 60);
        if (seconds === 60) {
          minutes += 1;
          seconds = 0;
        }
        return `${minutes}:${seconds.toString().padStart(2, "0")}`;
      }
      return value.toFixed(metric.unit === "m" || metric.unit === "%" ? 1 : 0);
    }

    function percentile(sortedValues, percent) {
      if (!sortedValues.length) return null;
      const index = (sortedValues.length - 1) * percent;
      const lower = Math.floor(index);
      const upper = Math.ceil(index);
      if (lower === upper) return sortedValues[lower];
      return sortedValues[lower] + (sortedValues[upper] - sortedValues[lower]) * (index - lower);
    }

    function excludeOutliersAndFindMinMax(values) {
      const sortedValues = values
        .filter(value => Number.isFinite(value))
        .slice()
        .sort((a, b) => a - b);
      if (!sortedValues.length) return null;
      const q1 = percentile(sortedValues, 0.25);
      const q3 = percentile(sortedValues, 0.75);
      const iqr = q3 - q1;
      const lowerBound = q1 - 1.5 * iqr;
      const upperBound = q3 + 1.5 * iqr;
      const filtered = sortedValues.filter(value => value >= lowerBound && value <= upperBound);
      const usable = filtered.length ? filtered : sortedValues;
      return { min: usable[0], max: usable[usable.length - 1] };
    }

    function paceRangeFromValues(values) {
      const range = excludeOutliersAndFindMinMax(values);
      if (!range) return { minY: 2.5, maxY: 6.0 };
      const tick = 20 / 60;
      const rawSpread = Math.max(tick, range.max - range.min);
      const margin = rawSpread * 0.2;
      const minY = Math.max(1, Math.floor((range.min - margin) / tick) * tick);
      const maxY = Math.ceil((range.max + margin) / tick) * tick;
      return { minY, maxY };
    }

    function numericRangeFromValues(values, tick = 5, marginRatio = 0.2) {
      const range = excludeOutliersAndFindMinMax(values);
      if (!range) return null;
      const rawSpread = Math.max(tick, range.max - range.min);
      const margin = rawSpread * marginRatio;
      const minY = Math.floor((range.min - margin) / tick) * tick;
      const maxY = Math.ceil((range.max + margin) / tick) * tick;
      return { minY, maxY };
    }

    function distanceTickStep(spanKm) {
      if (spanKm <= 1) return 0.1;
      if (spanKm <= 2) return 0.2;
      if (spanKm <= 5) return 0.5;
      if (spanKm <= 14) return 1;
      if (spanKm <= 28) return 2;
      if (spanKm <= 55) return 5;
      return 10;
    }

    function distanceTicks(minX, maxX) {
      const step = distanceTickStep(Math.max(0.01, maxX - minX));
      const ticks = [];
      const decimals = step < 1 ? 1 : 0;
      let value = Math.ceil(minX / step) * step;
      const end = Math.floor(maxX / step) * step;
      while (value <= end + step / 10) {
        ticks.push(Number(value.toFixed(decimals)));
        value += step;
      }
      return ticks;
    }

    function drawMetric(canvas, metric) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = "12px system-ui";
      const pad = { left: 56, right: 16, top: 18, bottom: 36 };
      const series = metricSeries(metric);
      if (!series.length) {
        ctx.fillStyle = getComputedStyle(document.body).color;
        ctx.fillText("No data", pad.left, 34);
        return;
      }
      const all = series.flatMap(s => s.points);
      const rawMaxX = metric.source === "laps"
        ? Math.max(...all.map(p => p.lapNumber ?? p.x)) || 1
        : Math.max(...all.map(p => p.end ?? p.x)) || 1;
      const zoom = metric.source === "laps" ? null : chartZooms.get(metric.key);
      const minX = zoom ? zoom.min : 0;
      const maxX = zoom ? zoom.max : rawMaxX;
      const visibleSeries = metric.source === "laps"
        ? series
        : series.map(seriesItem => ({
            ...seriesItem,
            points: seriesItem.points.filter(point => point.x >= minX && point.x <= maxX)
          })).filter(seriesItem => seriesItem.points.length > 1);
      const visibleAll = visibleSeries.flatMap(s => s.points);
      const yValues = visibleAll.length ? visibleAll.map(p => p.y) : all.map(p => p.y);
      let minY = Math.min(...yValues);
      let maxY = Math.max(...yValues);
      if (metric.unit === "min/km") {
        const paceRange = paceRangeFromValues(yValues);
        minY = paceRange.minY;
        maxY = paceRange.maxY;
      } else if (metric.key === "cadence") {
        const cadenceRange = numericRangeFromValues(yValues, 5, 0.2);
        if (cadenceRange) {
          minY = cadenceRange.minY;
          maxY = cadenceRange.maxY;
        }
      } else if (metric.clamp) {
        minY = Math.max(minY, metric.clamp[0]);
        maxY = Math.min(maxY, metric.clamp[1]);
      }
      if (minY === maxY) { minY -= 1; maxY += 1; }
      const plotW = canvas.width - pad.left - pad.right;
      const plotH = canvas.height - pad.top - pad.bottom;
      const xMap = x => pad.left + ((x - minX) / (maxX - minX || 1)) * plotW;
      const yMap = y => metric.unit === "min/km"
        ? pad.top + ((y - minY) / (maxY - minY)) * plotH
        : pad.top + (1 - (y - minY) / (maxY - minY)) * plotH;
        chartModels.set(canvas.id, { canvas, metric, pad, plotW, plotH, minX, maxX, rawMaxX, minY, maxY, xMap, yMap, series: visibleSeries });

      const styles = getComputedStyle(document.body);
      const bodyColor = styles.color;
      const panelColor = styles.getPropertyValue("--panel").trim() || "#ffffff";
      const axisColor = styles.getPropertyValue("--muted").trim() || "#94a3b8";
      ctx.strokeStyle = axisColor;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, pad.top + plotH);
      ctx.lineTo(pad.left + plotW, pad.top + plotH);
      ctx.stroke();
      ctx.fillStyle = bodyColor;
      if (metric.source === "laps") {
        for (let lap = Math.max(1, Math.ceil(minX)); lap <= Math.floor(maxX); lap += 1) {
          ctx.fillText(String(lap), xMap(lap) - 4, pad.top + plotH + 22);
        }
      } else {
        distanceTicks(minX, maxX).forEach(x => {
          const label = Number.isInteger(x) ? x.toFixed(0) : x.toFixed(1);
          ctx.fillText(label, xMap(x) - 9, pad.top + plotH + 22);
        });
      }
      for (let i = 0; i <= 4; i++) {
        const y = minY + ((maxY - minY) / 4) * i;
        ctx.fillText(formatAxisValue(y, metric), 6, yMap(y) + 4);
      }

      if (metric.source === "laps") {
        const baselineY = yMap(maxY);
        const slotWidth = plotW / Math.max(1, maxX);
        series.forEach((seriesItem, seriesIndex) => {
          ctx.fillStyle = seriesItem.color;
          ctx.globalAlpha = 0.76;
          seriesItem.points.forEach(point => {
            const groupLeft = xMap((point.lapNumber ?? point.x) - 1);
            const seriesSlotWidth = slotWidth / Math.max(1, series.length);
            const barWidth = Math.max(2, seriesSlotWidth * 0.82);
            const left = groupLeft + seriesIndex * seriesSlotWidth + (seriesSlotWidth - barWidth) / 2;
            const y = yMap(Math.max(minY, Math.min(maxY, point.y)));
            ctx.fillRect(left, y, barWidth, Math.max(1, baselineY - y));
          });
          ctx.globalAlpha = 1;
        });
      } else {
        visibleSeries.forEach(seriesItem => {
          ctx.strokeStyle = seriesItem.color;
          ctx.lineWidth = 2;
          ctx.beginPath();
          seriesItem.points.forEach((p, i) => {
            const x = xMap(p.x);
            const y = yMap(Math.max(minY, Math.min(maxY, p.y)));
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          });
          ctx.stroke();
        });
      }

      const cursorPoints = [];
      if (cursorEnabled && cursorDistance !== null) {
        visibleSeries.forEach(seriesItem => {
          const point = nearestByDistance(seriesItem.points, cursorDistance);
          if (point) cursorPoints.push({ seriesItem, point });
        });
      }
      if (cursorEnabled && cursorPoints.length && (metric.source === "laps" || (cursorDistance >= minX && cursorDistance <= maxX))) {
        const cursorXValue = metric.source === "laps"
          ? cursorPoints[0].point.lapNumber ?? cursorPoints[0].point.x
          : cursorDistance;
        const cursorX = xMap(cursorXValue);
        ctx.save();
        if (metric.source === "laps") {
          const slotWidth = plotW / Math.max(1, maxX);
          const left = Math.max(pad.left, xMap(cursorXValue - 1));
          const right = Math.min(pad.left + plotW, left + slotWidth);
          ctx.fillStyle = "rgba(15, 118, 110, 0.16)";
          ctx.fillRect(left, pad.top, right - left, plotH);
          ctx.strokeStyle = "#0f766e";
          ctx.strokeRect(left, pad.top, right - left, plotH);
        } else {
          ctx.strokeStyle = "#111827";
          ctx.setLineDash([5, 4]);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(cursorX, pad.top);
          ctx.lineTo(cursorX, pad.top + plotH);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        const cursorValues = [];
        cursorPoints.forEach(({ seriesItem, point }) => {
          const clampedY = Math.max(minY, Math.min(maxY, point.y));
          ctx.fillStyle = seriesItem.color;
          ctx.beginPath();
          ctx.arc(xMap(point.x), yMap(clampedY), 4, 0, Math.PI * 2);
          ctx.fill();
          cursorValues.push({ color: seriesItem.color, value: point.y, label: seriesItem.name });
        });
        ctx.restore();
      }

      if (zoom && metric.source !== "laps") {
        ctx.save();
        ctx.fillStyle = bodyColor;
        ctx.globalAlpha = 0.72;
        ctx.fillText("Zoomed - double-click to reset", pad.left + plotW - 158, pad.top + 12);
        ctx.restore();
      }

      if (chartDrag && chartDrag.canvasId === canvas.id && metric.source !== "laps") {
        const left = Math.max(pad.left, Math.min(chartDrag.startX, chartDrag.currentX));
        const right = Math.min(pad.left + plotW, Math.max(chartDrag.startX, chartDrag.currentX));
        if (right - left > 2) {
          ctx.save();
          ctx.fillStyle = "rgba(15, 118, 110, 0.16)";
          ctx.fillRect(left, pad.top, right - left, plotH);
          ctx.strokeStyle = "#0f766e";
          ctx.strokeRect(left, pad.top, right - left, plotH);
          ctx.restore();
        }
      }
    }

    function drawCharts() {
      charts.innerHTML = metrics.map((metric, index) => `
        <section class="chart ${metric.source === "laps" ? "chart-wide" : ""}">
          <div class="chart-heading">
            <h2>${metric.label}</h2>
            <div id="readout-${index}" class="chart-readout"></div>
          </div>
          <canvas id="chart-${index}" width="${metric.source === "laps" ? 1320 : 650}" height="${metric.source === "laps" ? 300 : 260}"></canvas>
        </section>`).join("");
      metrics.forEach((metric, index) => {
        const canvas = document.querySelector(`#chart-${index}`);
        canvas.addEventListener("mousemove", event => setCursorFromChart(event, canvas));
        canvas.addEventListener("mousedown", event => startChartZoom(event, canvas));
        canvas.addEventListener("mousemove", event => updateChartZoomDrag(event, canvas));
        canvas.addEventListener("mouseup", event => finishChartZoom(event, canvas));
        canvas.addEventListener("dblclick", () => resetChartZoom(canvas));
        canvas.addEventListener("mouseleave", () => setCursorDistance(null));
      });
      renderCharts();
    }

    function updateChartReadouts() {
      metrics.forEach((metric, index) => {
        const readout = document.querySelector(`#readout-${index}`);
        if (!readout) return;
        if (cursorDistance === null || !activities.length) {
          readout.innerHTML = "";
          return;
        }
        const series = metricSeries(metric);
        const points = [];
        const items = series.map(seriesItem => {
          const point = nearestByDistance(seriesItem.points, cursorDistance);
          if (!point) return "";
          points.push(point);
          return `<span style="border-left-color:${seriesItem.color}" title="${seriesItem.name}">${formatMetricValue(point.y, metric.unit)}</span>`;
        }).filter(Boolean);
        if (!items.length) {
          readout.innerHTML = "";
          return;
        }
        const positionLabel = metric.source === "laps"
          ? `Lap ${points[0].lapNumber ?? points[0].x}`
          : `${cursorDistance.toFixed(2)} km`;
        readout.innerHTML = `<span class="readout-position">${positionLabel}</span>${items.join("")}`;
      });
    }

    function renderCharts() {
      chartModels = new Map();
      metrics.forEach((metric, index) => {
        const canvas = document.querySelector(`#chart-${index}`);
        if (canvas) drawMetric(canvas, metric);
      });
      updateChartReadouts();
    }

    function lonLatToWorld(lon, lat, zoom) {
      const sinLat = Math.sin(lat * Math.PI / 180);
      const scale = tileSize * 2 ** zoom;
      return {
        x: ((lon + 180) / 360) * scale,
        y: (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale
      };
    }

    function chooseMapZoom(bounds, width, height, pad) {
      for (let zoom = 18; zoom >= 2; zoom -= 1) {
        const nw = lonLatToWorld(bounds.minLon, bounds.maxLat, zoom);
        const se = lonLatToWorld(bounds.maxLon, bounds.minLat, zoom);
        const bboxW = Math.max(1, Math.abs(se.x - nw.x));
        const bboxH = Math.max(1, Math.abs(se.y - nw.y));
        if (bboxW <= width - pad * 2 && bboxH <= height - pad * 2) return zoom;
      }
      return 2;
    }

    function getTileImage(z, x, y) {
      const maxTile = 2 ** z;
      const wrappedX = ((x % maxTile) + maxTile) % maxTile;
      if (y < 0 || y >= maxTile) return null;
      const key = `${z}/${wrappedX}/${y}`;
      if (tileCache.has(key)) return tileCache.get(key);
      const image = new Image();
      image.referrerPolicy = "no-referrer";
      image.onload = () => drawMap();
      image.onerror = () => {
        image.failed = true;
        drawMap();
      };
      image.src = tileUrlTemplate
        .replace("{z}", z)
        .replace("{x}", wrappedX)
        .replace("{y}", y);
      tileCache.set(key, image);
      return image;
    }

    function drawMapTiles(ctx, zoom, topLeftWorldX, topLeftWorldY) {
      const startTileX = Math.floor(topLeftWorldX / tileSize);
      const endTileX = Math.floor((topLeftWorldX + routeMap.width) / tileSize);
      const startTileY = Math.floor(topLeftWorldY / tileSize);
      const endTileY = Math.floor((topLeftWorldY + routeMap.height) / tileSize);
      for (let tileY = startTileY; tileY <= endTileY; tileY += 1) {
        for (let tileX = startTileX; tileX <= endTileX; tileX += 1) {
          const image = getTileImage(zoom, tileX, tileY);
          const x = Math.round(tileX * tileSize - topLeftWorldX);
          const y = Math.round(tileY * tileSize - topLeftWorldY);
          if (image && image.complete && !image.failed) {
            ctx.drawImage(image, x, y, tileSize, tileSize);
          } else {
            ctx.fillStyle = "#e2e8f0";
            ctx.fillRect(x, y, tileSize, tileSize);
            ctx.strokeStyle = "#cbd5e1";
            ctx.strokeRect(x, y, tileSize, tileSize);
          }
        }
      }
    }

    function drawMap() {
      const ctx = routeMap.getContext("2d");
      ctx.clearRect(0, 0, routeMap.width, routeMap.height);
      ctx.font = "13px system-ui";
      const routes = activities.map((activity, index) => ({
        name: `${activity.summary.date} ${activity.summary.workout_name}`,
        color: colors[index % colors.length],
        activityIndex: index,
        timeOffset: activityTimeOffset(activity),
        lapMarkers: activity.laps
          .filter(lap => lap.end_lat !== null && lap.end_lon !== null)
          .map(lap => ({ lat: lap.end_lat, lon: lap.end_lon, number: lap.lap_number })),
        points: activity.records
          .filter(r => r.position_lat !== null && r.position_long !== null && r.distance_km !== null)
          .map(r => ({ lat: r.position_lat, lon: r.position_long, x: r.distance_km, t: r.relative_time }))
      })).filter(route => route.points.length > 1);
      if (!routes.length) {
        mapModel = null;
        mapNote.textContent = "No GPS data found. Some older MFA files do not include GPS data.";
        ctx.fillStyle = getComputedStyle(document.body).color;
        ctx.fillText("No GPS route", 28, 36);
        return;
      }
      mapNote.textContent = "";
      const all = routes.flatMap(route => route.points);
      const minLat = Math.min(...all.map(p => p.lat));
      const maxLat = Math.max(...all.map(p => p.lat));
      const minLon = Math.min(...all.map(p => p.lon));
      const maxLon = Math.max(...all.map(p => p.lon));
      const bounds = { minLat, maxLat, minLon, maxLon };
      const pad = 54;
      if (!mapView) {
        const fitZoom = chooseMapZoom(bounds, routeMap.width, routeMap.height, pad);
        const nw = lonLatToWorld(minLon, maxLat, fitZoom);
        const se = lonLatToWorld(maxLon, minLat, fitZoom);
        mapView = {
          zoom: fitZoom,
          centerX: (nw.x + se.x) / 2,
          centerY: (nw.y + se.y) / 2
        };
      }
      const zoom = mapView.zoom;
      const topLeftWorldX = mapView.centerX - routeMap.width / 2;
      const topLeftWorldY = mapView.centerY - routeMap.height / 2;
      const xMap = lon => lonLatToWorld(lon, 0, zoom).x - topLeftWorldX;
      const yMap = lat => lonLatToWorld(0, lat, zoom).y - topLeftWorldY;
      mapModel = { routes, xMap, yMap, zoom, topLeftWorldX, topLeftWorldY, legendItems: [] };
      drawMapTiles(ctx, zoom, topLeftWorldX, topLeftWorldY);
      ctx.fillStyle = "rgba(255, 255, 255, 0.82)";
      ctx.fillRect(routeMap.width - 224, 8, 216, 18);
      ctx.fillStyle = "#334155";
      ctx.fillText(`Click: zoom in / Alt-click: out  z${zoom}`, routeMap.width - 218, 22);
      const legendLineHeight = 22;
      const legendNames = routes.map(route => {
        const focusedPrefix = focusedRouteActivityIndex === route.activityIndex ? "● " : "";
        const rawName = `${focusedPrefix}${route.name}`;
        return rawName.length > 58 ? `${rawName.slice(0, 55)}...` : rawName;
      });
      const legendTextWidth = Math.max(...legendNames.map(name => ctx.measureText(name).width), 0);
      const legendWidth = Math.min(routeMap.width - 250, Math.ceil(42 + legendTextWidth + 16));
      const legendHeight = Math.max(28, 10 + routes.length * legendLineHeight);
      ctx.fillStyle = "rgba(255, 255, 255, 0.82)";
      ctx.fillRect(8, 8, legendWidth, legendHeight);
      ctx.strokeStyle = "rgba(15, 23, 42, 0.16)";
      ctx.strokeRect(8, 8, legendWidth, legendHeight);
      routes.forEach((route, index) => {
        const rowTop = 13 + index * legendLineHeight;
        const rowCenter = rowTop + legendLineHeight / 2;
        const textBaseline = rowCenter + 4;
        mapModel.legendItems.push({
          route,
          x: 8,
          y: rowTop,
          width: legendWidth,
          height: legendLineHeight
        });
        const isFocused = focusedRouteActivityIndex === route.activityIndex;
        if (isFocused) {
          ctx.fillStyle = "rgba(15, 118, 110, 0.14)";
          ctx.fillRect(10, rowTop, legendWidth - 4, legendLineHeight);
        }
        ctx.fillStyle = route.color;
        ctx.fillRect(16, rowCenter - 2, 12, 4);
        ctx.fillStyle = "#334155";
        ctx.fillText(legendNames[index], 36, textBaseline);
      });
      ctx.fillStyle = "rgba(255, 255, 255, 0.82)";
      ctx.fillRect(8, routeMap.height - 26, 244, 18);
      ctx.fillStyle = "#334155";
      ctx.fillText("© OpenStreetMap contributors", 14, routeMap.height - 12);
      routes.forEach((route, index) => {
        ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
        ctx.lineWidth = 6;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.beginPath();
        route.points.forEach((p, i) => {
          const x = xMap(p.lon);
          const y = yMap(p.lat);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.strokeStyle = route.color;
        ctx.lineWidth = 3;
        ctx.beginPath();
        route.points.forEach((p, i) => {
          const x = xMap(p.lon);
          const y = yMap(p.lat);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
        route.lapMarkers.forEach(marker => {
          const markerX = xMap(marker.lon);
          const markerY = yMap(marker.lat);
          ctx.fillStyle = "#ffffff";
          ctx.strokeStyle = route.color;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(markerX, markerY, 9, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
          ctx.fillStyle = route.color;
          ctx.fillText(String(marker.number), markerX - 4, markerY + 4);
        });
      });
      if (cursorEnabled && cursorDistance !== null) {
        routes.forEach(route => {
          const point = (animationTimer !== null || animationTime > 0 || hasTimeOffsets())
            ? nearestByTime(route.points, animationTime - route.timeOffset)
            : nearestByDistance(route.points, cursorDistance);
          if (!point) return;
          const x = xMap(point.lon);
          const y = yMap(point.lat);
          ctx.fillStyle = route.color;
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(x, y, 7, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        });
      }
    }

    function setCursorFromChart(event, canvas) {
      const model = chartModels.get(canvas.id);
      if (!model || chartDrag) return;
      const rect = canvas.getBoundingClientRect();
      const canvasX = (event.clientX - rect.left) * (canvas.width / rect.width);
      if (canvasX < model.pad.left || canvasX > model.pad.left + model.plotW) return;
      const distance = model.minX + ((canvasX - model.pad.left) / model.plotW) * (model.maxX - model.minX);
      setCursorDistance(distance);
    }

    function chartCanvasX(event, canvas) {
      const rect = canvas.getBoundingClientRect();
      return (event.clientX - rect.left) * (canvas.width / rect.width);
    }

    function startChartZoom(event, canvas) {
      const model = chartModels.get(canvas.id);
      if (!model || model.metric.source === "laps" || event.button !== 0) return;
      const canvasX = chartCanvasX(event, canvas);
      if (canvasX < model.pad.left || canvasX > model.pad.left + model.plotW) return;
      chartDrag = { canvasId: canvas.id, startX: canvasX, currentX: canvasX };
      canvas.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    }

    function updateChartZoomDrag(event, canvas) {
      if (!chartDrag || chartDrag.canvasId !== canvas.id) return;
      const model = chartModels.get(canvas.id);
      if (!model) return;
      const canvasX = chartCanvasX(event, canvas);
      chartDrag.currentX = Math.max(model.pad.left, Math.min(model.pad.left + model.plotW, canvasX));
      drawMetric(canvas, model.metric);
    }

    function finishChartZoom(event, canvas) {
      if (!chartDrag || chartDrag.canvasId !== canvas.id) return;
      const model = chartModels.get(canvas.id);
      const drag = chartDrag;
      chartDrag = null;
      if (!model) return;
      const start = Math.max(model.pad.left, Math.min(model.pad.left + model.plotW, drag.startX));
      const end = Math.max(model.pad.left, Math.min(model.pad.left + model.plotW, drag.currentX));
      const width = Math.abs(end - start);
      if (width < 8) {
        renderCharts();
        return;
      }
      const leftRatio = (Math.min(start, end) - model.pad.left) / model.plotW;
      const rightRatio = (Math.max(start, end) - model.pad.left) / model.plotW;
      const min = model.minX + leftRatio * (model.maxX - model.minX);
      const max = model.minX + rightRatio * (model.maxX - model.minX);
      if (max - min > 0.02) {
        chartZooms.set(model.metric.key, { min, max });
      }
      renderCharts();
    }

    function resetChartZoom(canvas) {
      const model = chartModels.get(canvas.id);
      if (!model || model.metric.source === "laps") return;
      chartZooms.delete(model.metric.key);
      renderCharts();
    }

    function setCursorFromMap(event) {
      if (!mapModel) return;
      const rect = routeMap.getBoundingClientRect();
      const mouseX = (event.clientX - rect.left) * (routeMap.width / rect.width);
      const mouseY = (event.clientY - rect.top) * (routeMap.height / rect.height);
      if (legendItemAt(mouseX, mouseY)) return;
      let best = null;
      let bestDistance = Infinity;
      for (const route of mapModel.routes) {
        for (const point of route.points) {
          const dx = mapModel.xMap(point.lon) - mouseX;
          const dy = mapModel.yMap(point.lat) - mouseY;
          const distance = dx * dx + dy * dy;
          if (distance < bestDistance) {
            bestDistance = distance;
            best = point;
          }
        }
      }
      if (best) setCursorDistance(best.x);
    }

    function legendItemAt(canvasX, canvasY) {
      if (!mapModel || !mapModel.legendItems) return null;
      return mapModel.legendItems.find(item =>
        canvasX >= item.x &&
        canvasX <= item.x + item.width &&
        canvasY >= item.y &&
        canvasY <= item.y + item.height
      ) || null;
    }

    function setMapCursor(event) {
      if (!mapModel) {
        routeMap.style.cursor = "";
        return;
      }
      const rect = routeMap.getBoundingClientRect();
      const canvasX = (event.clientX - rect.left) * (routeMap.width / rect.width);
      const canvasY = (event.clientY - rect.top) * (routeMap.height / rect.height);
      routeMap.style.cursor = legendItemAt(canvasX, canvasY) ? "pointer" : "";
    }

    function centerMapOnRoute(route, redraw = true) {
      if (!route || !route.points.length || !mapModel) return false;
      let point = null;
      if (cursorDistance !== null) {
        point = nearestByDistance(route.points, cursorDistance);
      }
      if (!point) {
        point = route.points[Math.floor(route.points.length / 2)];
      }
      if (!point) return;
      const world = lonLatToWorld(point.lon, point.lat, mapModel.zoom);
      mapView = {
        zoom: mapModel.zoom,
        centerX: world.x,
        centerY: world.y
      };
      if (redraw) drawMap();
      return true;
    }

    function focusedRoute() {
      if (!mapModel || focusedRouteActivityIndex === null) return null;
      return mapModel.routes.find(route => route.activityIndex === focusedRouteActivityIndex) || null;
    }

    function updateTrackedRoute(redraw = false) {
      const route = focusedRoute();
      if (!route) return false;
      return centerMapOnRoute(route, redraw);
    }

    function focusRoute(route) {
      if (!route || !route.points.length || !mapModel) return;
      if (focusedRouteActivityIndex === route.activityIndex) {
        focusedRouteActivityIndex = null;
        drawMap();
        return;
      }
      focusedRouteActivityIndex = route.activityIndex;
      centerMapOnRoute(route, true);
    }

    function zoomMapAt(event) {
      if (!mapModel) return;
      event.preventDefault();
      const rect = routeMap.getBoundingClientRect();
      const canvasX = (event.clientX - rect.left) * (routeMap.width / rect.width);
      const canvasY = (event.clientY - rect.top) * (routeMap.height / rect.height);
      const legendItem = legendItemAt(canvasX, canvasY);
      if (legendItem) {
        focusRoute(legendItem.route);
        return;
      }
      const direction = event.button === 2 || event.altKey || event.metaKey ? -1 : 1;
      const newZoom = Math.max(2, Math.min(19, mapModel.zoom + direction));
      if (newZoom === mapModel.zoom) return;
      focusedRouteActivityIndex = null;
      const clickedWorldX = mapModel.topLeftWorldX + canvasX;
      const clickedWorldY = mapModel.topLeftWorldY + canvasY;
      const scale = 2 ** (newZoom - mapModel.zoom);
      mapView = {
        zoom: newZoom,
        centerX: clickedWorldX * scale,
        centerY: clickedWorldY * scale
      };
      drawMap();
    }

    function setAnimationTime(time, syncCursor = true) {
      animationTime = Math.max(0, Math.min(animationMaxTime, Number(time) || 0));
      timeSlider.value = animationTime.toFixed(1);
      timeLabel.textContent = `Time: ${formatDurationLabel(animationTime)}`;
      if (syncCursor && mapModel) {
        const firstRoute = mapModel.routes.find(route => route.points.some(point => point.t !== null && point.t !== undefined));
        const point = firstRoute ? nearestByTime(firstRoute.points, animationTime - firstRoute.timeOffset) : null;
        if (point && point.x !== null) {
          cursorDistance = point.x;
          renderCharts();
          updateCursorReadout();
        }
      }
      updateTrackedRoute(false);
      if (!mapSection.classList.contains("hidden")) drawMap();
    }

    function configureAnimationControls(reset = true) {
      const times = activities.flatMap(activity => activity.records
        .map(record => record.relative_time === null || record.relative_time === undefined ? null : record.relative_time + activityTimeOffset(activity))
        .filter(value => value !== null && value !== undefined));
      animationMaxTime = times.length ? Math.max(...times) : 0;
      if (reset) {
        animationTime = 0;
        focusedRouteActivityIndex = null;
      } else {
        animationTime = Math.max(0, Math.min(animationTime, animationMaxTime));
      }
      timeSlider.max = animationMaxTime.toFixed(1);
      timeSlider.value = animationTime.toFixed(1);
      timeLabel.textContent = `Time: ${formatDurationLabel(animationTime)}`;
      playButton.disabled = animationMaxTime <= 0;
      playButton.textContent = "Start";
      if (reset) stopAnimation();
    }

    function stopAnimation() {
      if (animationTimer !== null) {
        cancelAnimationFrame(animationTimer);
        animationTimer = null;
      }
      lastAnimationStamp = null;
      playButton.textContent = "Start";
    }

    function animationStep(timestamp) {
      if (lastAnimationStamp === null) lastAnimationStamp = timestamp;
      const elapsed = (timestamp - lastAnimationStamp) / 1000;
      lastAnimationStamp = timestamp;
      const speed = Number(speedSlider.value) || 1;
      const nextTime = animationTime + elapsed * speed;
      if (nextTime >= animationMaxTime) {
        setAnimationTime(animationMaxTime);
        stopAnimation();
        return;
      }
      setAnimationTime(nextTime);
      animationTimer = requestAnimationFrame(animationStep);
    }

    function toggleAnimation() {
      if (animationTimer !== null) {
        stopAnimation();
        return;
      }
      if (animationTime >= animationMaxTime) setAnimationTime(0);
      playButton.textContent = "Stop";
      lastAnimationStamp = null;
      animationTimer = requestAnimationFrame(animationStep);
    }

    function setCursorDistance(distance) {
      cursorDistance = distance;
      renderCharts();
      updateTrackedRoute(false);
      if (!mapSection.classList.contains("hidden")) drawMap();
      updateCursorReadout();
    }

    function updateCursorReadout() {
      if (!cursorToggle.checked || !activities.length) {
        cursorPopup.classList.add("hidden");
        cursorReadout.innerHTML = "";
        return;
      }
      cursorPopup.classList.remove("hidden");
      if (cursorDistance === null) {
        cursorDistance = 0;
      }
      const cursorMetrics = metrics.filter(metric => metric.source !== "laps");
      const header = `<tr><th>Workout</th><th>Distance</th>${cursorMetrics.map(metric => `<th class="${metric.key === "stance_time" || metric.key === "vertical_ratio" ? "compact" : ""}">${metric.label}</th>`).join("")}</tr>`;
      const rows = activities.map((activity, index) => {
        const records = activity.records.filter(r => r.distance_km !== null);
        const record = nearestByDistance(records.map(r => ({ ...r, x: r.distance_km, y: 0 })), cursorDistance);
        const values = cursorMetrics
          .map(metric => {
            const value = record ? record[metric.key] : null;
            return `<td class="${metric.key === "stance_time" || metric.key === "vertical_ratio" ? "compact" : ""}">${formatMetricValue(value, metric.unit)}</td>`;
          })
          .join("");
        return `<tr>
          <td class="cursor-workout" style="border-left-color:${colors[index % colors.length]}" title="${escapeHtml(activity.summary.date)} ${escapeHtml(activity.summary.workout_name)}">${escapeHtml(activity.summary.date)} ${escapeHtml(activity.summary.workout_name)}</td>
          <td>${cursorDistance.toFixed(2)} km</td>
          ${values}
        </tr>`;
      }).join("");
      cursorReadout.innerHTML = `<table class="cursor-table"><thead>${header}</thead><tbody>${rows}</tbody></table>`;
    }

    function updateSidePanelLayout() {
      sidePanel.classList.toggle("cursor-off", !cursorToggle.checked);
      if (window.innerWidth >= 1280 && !lapSection.classList.contains("hidden")) {
        const top = lapSection.getBoundingClientRect().top;
        lapSection.style.maxHeight = `calc(100vh - ${Math.ceil(top + 18)}px)`;
      } else {
        lapSection.style.maxHeight = "";
      }
    }

    function updateTimeOffsetVisibility() {
      if (timeOffsetToggle.checked && activities.length) {
        timeOffsetSection.classList.remove("hidden");
      } else {
        timeOffsetSection.classList.add("hidden");
      }
      updateSidePanelLayout();
    }

    function applyTimeOffset(index, seconds) {
      if (!activities[index]) return;
      activities[index].summary.time_offset_seconds = Number(seconds) || 0;
      configureAnimationControls(false);
      setAnimationTime(animationTime);
      drawMap();
    }

    function applyWorkoutName(index, name) {
      if (!activities[index]) return;
      activities[index].summary.workout_name = name.trim() || "-";
      drawTimeOffsetControls();
      drawLapChart();
      renderCharts();
      updateCursorReadout();
      if (!mapSection.classList.contains("hidden")) drawMap();
    }

    async function analyzeSelectedFiles() {
      if (!filesInput.files.length) return;
      button.disabled = true;
      message.className = "muted";
      message.textContent = "Analyzing...";
      setCursorDistance(null);
      exportButton.disabled = true;
      mfaExportButton.disabled = true;
      summarySection.classList.add("hidden");
      cursorPopup.classList.add("hidden");
      timeOffsetSection.classList.add("hidden");
      lapSection.classList.add("hidden");
      mapSection.classList.add("hidden");
      analysisLayout.classList.add("hidden");
      try {
        const formData = new FormData();
        for (const file of filesInput.files) formData.append("files", file);
        const response = await fetch("/api/analyze", { method: "POST", body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Analysis failed.");
        activities = data.activities;
        chartZooms = new Map();
        chartDrag = null;
        mapView = null;
        configureAnimationControls();
        fillSummary();
        drawTimeOffsetControls();
        drawLapChart();
        drawMap();
        drawCharts();
        summarySection.classList.remove("hidden");
        updateTimeOffsetVisibility();
        lapSection.classList.remove("hidden");
        mapSection.classList.remove("hidden");
        analysisLayout.classList.remove("hidden");
        exportButton.disabled = false;
        mfaExportButton.disabled = false;
        updateSidePanelLayout();
        message.textContent = "";
      } catch (error) {
        message.className = "error";
        message.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    }

    button.addEventListener("click", () => {
      filesInput.value = "";
      filesInput.click();
    });
    timeOffsetControls.addEventListener("input", event => {
      const input = event.target.closest("input[data-index]");
      if (!input) return;
      applyTimeOffset(Number(input.dataset.index), input.value);
    });
    summaryBody.addEventListener("change", event => {
      const input = event.target.closest(".workout-name-input");
      if (!input) return;
      applyWorkoutName(Number(input.dataset.index), input.value);
    });
    summaryBody.addEventListener("keydown", event => {
      const input = event.target.closest(".workout-name-input");
      if (!input) return;
      if (event.key === "Enter") input.blur();
      if (event.key === "Escape") {
        input.value = activities[Number(input.dataset.index)]?.summary.workout_name || "";
        input.blur();
      }
    });
    filesInput.addEventListener("change", analyzeSelectedFiles);
    routeMap.addEventListener("mousemove", event => {
      setMapCursor(event);
      setCursorFromMap(event);
    });
    routeMap.addEventListener("mouseleave", () => {
      routeMap.style.cursor = "";
      setCursorDistance(null);
    });
    routeMap.addEventListener("click", zoomMapAt);
    routeMap.addEventListener("contextmenu", event => {
      event.preventDefault();
      zoomMapAt(event);
    });
    lapChart.addEventListener("click", event => {
      const button = event.target.closest(".lap-jump");
      if (!button) return;
      stopAnimation();
      setAnimationTime(Number(button.dataset.time || 0));
    });
    cursorToggle.addEventListener("change", event => {
      saveSettings();
      if (!event.target.checked) {
        cursorPopup.classList.add("hidden");
      }
      updateCursorReadout();
      updateSidePanelLayout();
    });
    timeOffsetToggle.addEventListener("change", () => {
      saveSettings();
      updateTimeOffsetVisibility();
    });
    playButton.addEventListener("click", toggleAnimation);
    timeSlider.addEventListener("input", event => {
      stopAnimation();
      setAnimationTime(event.target.value);
    });
    speedSlider.addEventListener("input", event => {
      speedLabel.textContent = `${Number(event.target.value).toFixed(1)}x`;
    });
    function currentTheme() {
      return document.documentElement.dataset.theme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    }

    function syncThemeToggle() {
      themeToggle.checked = currentTheme() === "dark";
    }

    themeToggle.addEventListener("change", event => {
      document.documentElement.dataset.theme = event.target.checked ? "dark" : "light";
      saveSettings();
      renderCharts();
      if (!mapSection.classList.contains("hidden")) drawMap();
    });
    exportButton.addEventListener("click", exportCsv);
    mfaExportButton.addEventListener("click", exportMfa);
    helpButton.addEventListener("click", () => {
      if (!helpFrame.src) helpFrame.src = "/help";
      helpModal.classList.remove("hidden");
    });
    helpCloseButton.addEventListener("click", () => {
      helpModal.classList.add("hidden");
    });
    helpModal.addEventListener("click", event => {
      if (event.target === helpModal) helpModal.classList.add("hidden");
    });
    window.addEventListener("keydown", event => {
      if (event.key === "Escape") helpModal.classList.add("hidden");
    });
    smoothingSlider.addEventListener("input", event => {
      smoothingScale = Number(event.target.value) / 9;
      smoothingLabel.textContent = event.target.value;
      saveSettings();
      renderCharts();
      updateCursorReadout();
    });
    window.addEventListener("resize", updateSidePanelLayout);
    syncThemeToggle();
    updateSidePanelLayout();
  </script>
</body>
</html>
"""

HELP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Multi Activity Visualizer Help</title>
  <style>
    :root { color-scheme: light; --line: #d6dde8; --accent: #0f766e; --bg: #f6f8fb; --panel: #ffffff; --text: #17202a; --muted: #64748b; }
    @media (prefers-color-scheme: dark) { :root { color-scheme: dark; --line: #334155; --accent: #2dd4bf; --bg: #10151f; --panel: #17202a; --text: #e8edf5; --muted: #94a3b8; } }
    * { box-sizing: border-box; }
    body { background: var(--bg); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; margin: 0; }
    main { margin: 0 auto; max-width: 960px; padding: 28px 18px 48px; }
    h1 { font-size: 28px; margin: 0 0 6px; }
    h2 { border-bottom: 1px solid var(--line); font-size: 20px; margin: 30px 0 10px; padding-bottom: 6px; }
    h3 { font-size: 16px; margin: 18px 0 6px; }
    p { margin: 8px 0; }
    ul { margin: 8px 0 12px 22px; padding: 0; }
    li { margin: 4px 0; }
    code { background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 1px 5px; }
    .lead { color: var(--muted); margin-bottom: 22px; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin: 16px 0; padding: 14px; }
    .grid { display: grid; gap: 14px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <h1>Multi Activity Visualizer Manual</h1>
    <p class="lead">Use this app to load FIT or MFA files and compare multiple activities with summaries, maps, lap data, and metric charts.</p>

    <div class="panel">
      <h2>First-Time Workflow</h2>
      <ol>
        <li><strong>Start with Open / Analyze.</strong> Click the button, select one or more FIT files, or select an existing MFA project file.</li>
        <li><strong>Check the Summary table.</strong> Confirm that the activities, dates, distances, pace, heart rate, power, and temperature look correct.</li>
        <li><strong>Edit workout names if needed.</strong> Click directly in the Workout field. These names are used in legends, charts, the map, and MFA Export.</li>
        <li><strong>Compare the activities.</strong> Use the Route Map, Lap Chart, Lap Pace, and metric charts to inspect differences between workouts.</li>
        <li><strong>Move through the workout.</strong> Use the Cursor popup controls, Lap Chart rows, or graph/map cursor movement to inspect the same point across all activities.</li>
        <li><strong>Align activities if needed.</strong> Turn on Time Offset and adjust each workout in seconds when starts or comparable moments do not line up.</li>
        <li><strong>Save your comparison.</strong> Use MFA Export to save the displayed activities as one project file so you can reopen the same comparison later.</li>
      </ol>
    </div>

    <div class="panel">
      <h2>Main Controls</h2>
      <ul>
        <li><strong>Open / Analyze</strong>: Select one or more FIT or MFA files and analyze them in one action.</li>
        <li><strong>CSV Export</strong>: Export the loaded summary, lap, and record data as CSV.</li>
        <li><strong>MFA Export</strong>: Save all displayed activities together as a single MFA project file. The project includes workout names, start times, time offsets, GPS data, temperature, laps, records, and summary data.</li>
        <li><strong>Help</strong>: Open this manual in an in-app popup.</li>
      </ul>
    </div>

    <h2>Layout</h2>
    <div class="grid">
      <div class="panel">
        <h3>Left Panel</h3>
        <p>Shows the app title, file actions, display settings, Time Offset controls, and Lap Chart.</p>
      </div>
      <div class="panel">
        <h3>Main Area</h3>
        <p>Shows the summary table, Route Map, Lap Pace, and metric charts.</p>
      </div>
    </div>

    <h2>Summary Table</h2>
    <ul>
      <li>The vertical color bar at the left of the Date column identifies each workout series.</li>
      <li>Workout names are editable directly in the table. Edits are reflected in charts, the map, and MFA Export.</li>
      <li>Power and Temp are shown as record averages. Temp is displayed with one decimal place.</li>
    </ul>

    <h2>Cursor</h2>
    <ul>
      <li>The <strong>Cursor</strong> checkbox only toggles the Cursor popup window.</li>
      <li>Even when the popup is hidden, chart cursor lines, map markers, and chart-title readouts remain active.</li>
      <li>In the Cursor table, the Workout column is fixed and Distance plus metric columns can scroll horizontally.</li>
      <li>Use Start, Time, and Speed at the bottom of the Cursor popup to control Route Map animation.</li>
    </ul>

    <h2>Route Map</h2>
    <ul>
      <li>GPS routes are drawn with the same colors used throughout the app.</li>
      <li>Click a route legend item to focus and track that workout.</li>
      <li>While tracking, the map continuously follows the selected workout's cursor marker.</li>
      <li>Click or right-click the map to zoom around the selected point.</li>
    </ul>

    <h2>Graphs</h2>
    <ul>
      <li>Charts other than Lap Pace support distance-axis zooming.</li>
      <li>Values at the cursor position are displayed beside each chart title.</li>
      <li>Use <strong>Smoothing</strong> to adjust chart smoothing. This setting is saved in the browser.</li>
    </ul>

    <h2>Lap Chart</h2>
    <ul>
      <li>For each lap, every workout is shown in one line with cumulative time, lap duration, pace, heart rate, and cadence.</li>
      <li>Click the workout/time area in a row to jump the animation to that lap's cumulative time.</li>
      <li>The current lap is highlighted with a green rectangle in the Lap Pace chart.</li>
    </ul>

    <h2>Time Offset</h2>
    <ul>
      <li>Enable <strong>Time Offset</strong> to adjust each workout's time alignment in seconds.</li>
      <li>Use offsets to align Route Map animation and cursor tracking between workouts.</li>
      <li>The Time Offset panel visibility is saved in the browser. Per-workout offset values are saved in MFA Export.</li>
    </ul>

    <h2>MFA Format</h2>
    <ul>
      <li>MFA is a custom format for this app and the original Multi Activity Visualizer.</li>
      <li>MFA Export stores the displayed activities as one project file, including summary, lap, record, workout name, start time, time offset, GPS, and temperature data.</li>
      <li>When an MFA file is loaded, saved workout names and time offsets are restored.</li>
    </ul>

    <h2>Saved Display Settings</h2>
    <p>The following settings are saved in browser localStorage and restored the next time the app is opened.</p>
    <ul>
      <li>Smoothing</li>
      <li>Cursor popup visibility</li>
      <li>Time Offset panel visibility</li>
      <li>Dark mode</li>
    </ul>
  </main>
</body>
</html>
"""


class FitVisualizerHandler(BaseHTTPRequestHandler):
    server_version = "FitVisualizerHTTP/0.1"

    def normalized_path(self) -> str:
        return self.path.split("?", 1)[0]

    def do_HEAD(self) -> None:
        if self.normalized_path() in {"/", "/index.html", "/help"}:
            encoded = (HELP_HTML if self.normalized_path() == "/help" else INDEX_HTML).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        if self.normalized_path() in {"/", "/index.html"}:
            self.send_html(INDEX_HTML)
            return
        if self.normalized_path() == "/help":
            self.send_html(HELP_HTML)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.normalized_path() == "/api/export_mfa":
            self.export_mfa()
            return
        if self.normalized_path() != "/api/analyze":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "Upload is too large."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        try:
            body = self.rfile.read(content_length)
            uploads = parse_multipart(body, self.headers.get("Content-Type", ""))
            if not uploads:
                raise ValueError("No FIT or MFA files were selected.")

            activities = []
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                for upload in uploads:
                    if upload.filename.lower().endswith(".mfa"):
                        activities.extend(parse_mfa_file(upload.data, upload.filename))
                    else:
                        fit_path = tmp / upload.filename
                        fit_path.write_bytes(upload.data)
                        activities.append(parse_fit_file(fit_path, upload.filename))

            activities.sort(key=lambda item: item["summary"]["date"], reverse=True)
            self.send_json({"activities": activities})
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def export_mfa(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "Export payload is too large."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            activities = payload.get("activities")
            if not isinstance(activities, list):
                raise ValueError("activitiesが配列ではありません。")
            encoded = activities_to_mfa_bytes(activities)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="fit_activity_export.mfa"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_html(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    port = PORT
    while True:
        try:
            server = ThreadingHTTPServer((HOST, port), FitVisualizerHandler)
            break
        except OSError:
            port += 1
            if port > PORT + 20:
                raise
    print(f"Multi Activity Visualizer is running at http://{HOST}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
