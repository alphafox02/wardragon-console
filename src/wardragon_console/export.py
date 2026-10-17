"""CSV and KML export of Map history."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from .history import ExportRow, iter_export_flights

CSV_COLUMNS = (
    ("time_utc", "t"), ("flight", "flight_id"), ("type", "type"), ("id", "track_id"), ("label", "label"),
    ("make_model", "make_model"), ("ua_type", "ua_type"), ("transport", "transport"),
    ("operator_id", "operator_id"), ("lat", "lat"), ("lon", "lon"), ("alt_m", "alt"),
    ("speed_mps", "speed"), ("heading_deg", "heading"), ("pilot_lat", "pilot_lat"),
    ("pilot_lon", "pilot_lon"), ("home_lat", "home_lat"), ("home_lon", "home_lon"), ("rssi", "rssi"),
)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell(value: Any) -> Any:
    # Text fields come from broadcast RF data; stop spreadsheets treating them as formulas.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value


def to_csv(rows: list[ExportRow]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([name for name, _ in CSV_COLUMNS])
    for row in rows:
        writer.writerow([_iso(row.t) if key == "t" else _cell(getattr(row, key)) for _, key in CSV_COLUMNS])
    return out.getvalue().encode("utf-8")


def _text(value: Any) -> str:
    return escape("" if value is None else str(value))


def _coord(lon: float, lat: float, alt: float | None) -> str:
    return f"{lon:.7f} {lat:.7f} {alt if alt is not None else 0:.1f}"


def to_kml(rows: list[ExportRow], title: str) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">',
        "<Document>",
        f"<name>{_text(title)}</name>",
        '<Style id="drone"><LineStyle><color>ff3232b8</color><width>3</width></LineStyle></Style>',
        '<Style id="aircraft"><LineStyle><color>ff8f6f2b</color><width>2</width></LineStyle></Style>',
        '<Style id="pilot"><IconStyle><color>ff1f79b7</color></IconStyle></Style>',
        '<Style id="home"><IconStyle><color>ff528a24</color></IconStyle></Style>',
    ]
    for first, points in iter_export_flights(rows):
        name = first.label or first.track_id or f"Flight {first.flight_id}"
        start, end = _iso(points[0].t), _iso(points[-1].t)
        description = "<br/>".join(
            _text(f"{label}: {value}") for label, value in (
                ("ID", first.track_id), ("Make/model", first.make_model),
                ("Operator ID", first.operator_id), ("Transport", first.transport),
                ("First seen", start), ("Last seen", end),
            ) if value
        )
        parts.append(f"<Folder><name>{_text(name)}</name>")
        parts.append(
            f"<Placemark><name>{_text(name)}</name><description>{description}</description>"
            f"<styleUrl>#{'aircraft' if first.type == 'aircraft' else 'drone'}</styleUrl>"
            f"<gx:Track><altitudeMode>{'absolute' if all(p.alt is not None for p in points) else 'clampToGround'}</altitudeMode>"
        )
        parts.extend(f"<when>{_iso(p.t)}</when>" for p in points)
        parts.extend(f"<gx:coord>{_coord(p.lon, p.lat, p.alt)}</gx:coord>" for p in points)
        parts.append("</gx:Track></Placemark>")
        for kind, lat_key, lon_key in (("pilot", "pilot_lat", "pilot_lon"), ("home", "home_lat", "home_lon")):
            located = [p for p in points if getattr(p, lat_key) is not None]
            if located:
                lat, lon = getattr(located[-1], lat_key), getattr(located[-1], lon_key)
                parts.append(
                    f"<Placemark><name>{_text(kind.title())} of {_text(name)}</name><styleUrl>#{kind}</styleUrl>"
                    f"<Point><coordinates>{lon:.7f},{lat:.7f},0</coordinates></Point></Placemark>"
                )
        parts.append("</Folder>")
    parts.append("</Document></kml>")
    return "\n".join(parts).encode("utf-8")
