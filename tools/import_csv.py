"""Import legacy CSV measurements into the daily-JSON schema.

STATUS: stub. The mapping from CSV columns to the schema (see README and the
``measure.output`` module) will be filled in once the historical CSV format is
known. The target per-day document is:

    {
      "measurement_date": "YYYY-MM-DD",
      "tool_version": "imported",
      "resolver": "<source>",
      "results": [
        {
          "tld": "se",
          "timestamp": "YYYY-MM-DDTHH:MM:SSZ",
          "status": "secure|insecure|bogus|unreachable|error",
          "ad": true,
          "rcode": "NOERROR",
          "ds_count": 1,
          "ede": [{"code": 6, "text": "..."}],
          "class": {"type": "ccTLD", "idn": false}
        }
      ]
    }

Use ``measure.metadata.classify_tld`` to derive ``class`` from the TLD name and
``measure.output.write_daily`` / ``regenerate_derived`` to emit files, so
imported data is indistinguishable from freshly measured data.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="legacy CSV file to import")
    parser.add_argument(
        "--output", type=Path, default=Path("data"), help="output directory"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    raise SystemExit(
        "import_csv is not implemented yet — define the CSV column mapping first."
    )


if __name__ == "__main__":
    main()
