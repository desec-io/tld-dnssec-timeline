"""Write the daily measurement file and regenerate the derived data files.

Layout under the output directory (default ``data/``)::

    measurements/YYYY-MM-DD.json   one file per run (the record)
    index.json                     {"dates": [...]} list of available days
    timeline.json                  date -> class -> status -> count (for the UI)
    tld-history.json               per-TLD status over time (for TLD filtering)

``index.json``, ``timeline.json`` and ``tld-history.json`` are rebuilt from
scratch by scanning ``measurements/`` so they stay consistent after re-runs or
historical imports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .classify import STATUSES
from .metadata import CLASS_KEYS, class_key_from_class

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Single-character status codes for the compact per-TLD history. ``-`` marks a
# day on which a TLD was not measured (e.g. before it gained a DS record, or
# after it lost one), so a filtered timeline shows a genuine gap rather than
# carrying the last status forward.
_STATUS_CODE = {status: status[0] for status in STATUSES}
_ABSENT = "-"


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


def _write_json_compact(path: Path, obj: object) -> None:
    # No indentation: tld-history.json is a machine artifact whose transition
    # arrays would balloon (and diff badly) if pretty-printed.
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n")


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
    """Rebuild ``index.json``, ``timeline.json`` and ``tld-history.json``."""
    dates = _measurement_dates(output_dir)
    _write_json(output_dir / "index.json", {"dates": dates})

    days = []
    # Per-TLD history, transition-encoded against the date index: for each TLD a
    # list of ``[day_index, code]`` entries recording only the days its status
    # changed (including ``-`` when it stops being measured). This stays tiny
    # even over years of daily data, yet lets the web app reconstruct any TLD's
    # status on any day for the timeline TLD filter.
    tld_classes: dict[str, str] = {}
    tld_history: dict[str, list] = {}
    last_code: dict[str, str] = {}
    for index, date in enumerate(dates):
        document = json.loads(measurement_path(output_dir, date).read_text())
        counts = _empty_counts()
        present = set()
        for result in document["results"]:
            key = class_key_from_class(result["class"])
            counts[key][result["status"]] += 1

            tld = result["tld"]
            present.add(tld)
            tld_classes[tld] = key
            code = _STATUS_CODE[result["status"]]
            if last_code.get(tld) != code:
                tld_history.setdefault(tld, []).append([index, code])
                last_code[tld] = code
        days.append({"date": date, "counts": counts})

        # Close out TLDs that were measured before but are absent today.
        for tld, code in last_code.items():
            if tld not in present and code != _ABSENT:
                tld_history[tld].append([index, _ABSENT])
                last_code[tld] = _ABSENT

    _write_json(
        output_dir / "timeline.json",
        {
            "statuses": list(STATUSES),
            "classes": list(CLASS_KEYS),
            "days": days,
        },
    )

    _write_json_compact(
        output_dir / "tld-history.json",
        {"dates": dates, "classes": tld_classes, "tlds": tld_history},
    )
