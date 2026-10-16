"""Regenerate the IDN ccTLD mapping from IANA's Root Zone Database export.

Fetches https://data.iana.org/TLD/v1/tlds.json and keeps the IDN (``xn--``)
TLDs whose type is ``country-code``, writing them to a JSON mapping file that
``measure.metadata`` reads.

Design constraints (see README / PLAN):
- **Opportunistic:** run regularly, but on any fetch or parse failure leave the
  existing mapping file untouched so the previously checked-in version is
  reused.
- **Sanity threshold:** if the export yields fewer than
  :data:`MIN_PER_CATEGORY` IDN ccTLDs *or* fewer than that many IDN gTLDs,
  treat it as a failure (the export probably changed) and do not write
  anything.
- **No-op on no change:** only rewrite the file when the mapping actually
  changed, so the daily job does not produce empty commits.

Exit codes: ``0`` success (file is current, whether or not it changed);
non-zero indicates a failure and that the file was left as-is.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

IANA_TLDS_URL = "https://data.iana.org/TLD/v1/tlds.json"
# Breaking format changes bump the export's specversion; additions don't.
SPEC_VERSION = 1
MIN_PER_CATEGORY = 10


class MappingUpdateError(RuntimeError):
    """Raised when the export could not be fetched or parsed sensibly."""


def fetch(url: str = IANA_TLDS_URL, timeout: float = 30.0) -> str:
    import httpx

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any fetch problem is a failure
        raise MappingUpdateError(f"could not fetch {url}: {exc}") from exc
    return resp.text


def parse(text: str) -> tuple[dict[str, str], int]:
    """Return ``({a_label: u_label} for IDN ccTLDs, count of IDN gTLDs)``."""
    try:
        data = json.loads(text)
        version = data["meta"]["specversion"]
        if version != SPEC_VERSION:
            raise MappingUpdateError(
                f"unsupported export specversion {version} (expected {SPEC_VERSION})"
            )

        idn_cctlds: dict[str, str] = {}
        idn_gtld_count = 0
        for domain in data["domains"]:
            name = domain["name"]
            if not name.startswith("xn--"):
                continue
            if domain["type"] == "country-code":
                idn_cctlds[name] = domain["ulabel"]
            else:
                idn_gtld_count += 1
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MappingUpdateError(f"unexpected export structure: {exc!r}") from exc
    return idn_cctlds, idn_gtld_count


def build_mapping(text: str) -> dict[str, str]:
    """Parse + validate; raise :class:`MappingUpdateError` if it looks wrong."""
    idn_cctlds, idn_gtld_count = parse(text)
    if len(idn_cctlds) < MIN_PER_CATEGORY or idn_gtld_count < MIN_PER_CATEGORY:
        raise MappingUpdateError(
            "implausible result "
            f"(IDN ccTLDs={len(idn_cctlds)}, IDN gTLDs={idn_gtld_count}; "
            f"need >= {MIN_PER_CATEGORY} of each) - treating as failure"
        )
    return dict(sorted(idn_cctlds.items()))


def _existing(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("idn_cctlds", {})
    except (json.JSONDecodeError, OSError):
        return {}


def update_file(path: Path, mapping: dict[str, str]) -> bool:
    """Write the mapping only if it changed. Returns True if the file changed."""
    if _existing(path) == mapping:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "source": IANA_TLDS_URL,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "idn_cctlds": mapping,
    }
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "idn-cctlds.json",
        help="mapping file to write (default: data/idn-cctlds.json)",
    )
    parser.add_argument(
        "--source", default=IANA_TLDS_URL, help="TLD export URL to fetch"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        mapping = build_mapping(fetch(args.source))
    except MappingUpdateError as exc:
        print(f"mapping update failure: {exc}", file=sys.stderr)
        print("left existing mapping untouched", file=sys.stderr)
        return 2

    changed = update_file(args.output, mapping)
    state = "updated" if changed else "unchanged"
    print(f"{state}: {len(mapping)} IDN ccTLDs -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
