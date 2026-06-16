"""Write the daily measurement file and regenerate the derived data files.

Layout under the output directory (default ``data/``)::

    measurements/YYYY-MM-DD.json   one file per run (the record)
    index.json                     {"dates": [...]} list of available days
    timeline.json                  date -> class -> status -> count (for the UI)

``index.json`` and ``timeline.json`` are rebuilt from scratch by scanning
``measurements/`` so they stay consistent after re-runs or historical imports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .classify import STATUSES
from .metadata import CLASS_KEYS, class_key_from_class

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def measurement_path(output_dir: Path, date: str) -> Path:
    return output_dir / "measurements" / f"{date}.json"


def write_daily(output_dir: Path, day: dict) -> Path:
    """Write one day's measurement document; returns the file path."""
    path = measurement_path(output_dir, day["measurement_date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, day)
    return path


def _write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _measurement_dates(output_dir: Path) -> list[str]:
    measurements = output_dir / "measurements"
    if not measurements.is_dir():
        return []
    dates = [
        p.stem for p in measurements.glob("*.json") if _DATE_RE.match(p.stem)
    ]
    return sorted(dates)


def _empty_counts() -> dict[str, dict[str, int]]:
    return {key: {status: 0 for status in STATUSES} for key in CLASS_KEYS}


def regenerate_derived(output_dir: Path) -> None:
    """Rebuild ``index.json`` and ``timeline.json`` from the daily files."""
    dates = _measurement_dates(output_dir)
    _write_json(output_dir / "index.json", {"dates": dates})

    days = []
    for date in dates:
        document = json.loads(measurement_path(output_dir, date).read_text())
        counts = _empty_counts()
        for result in document["results"]:
            key = class_key_from_class(result["class"])
            counts[key][result["status"]] += 1
        days.append({"date": date, "counts": counts})

    _write_json(
        output_dir / "timeline.json",
        {
            "statuses": list(STATUSES),
            "classes": list(CLASS_KEYS),
            "days": days,
        },
    )
