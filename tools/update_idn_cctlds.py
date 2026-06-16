"""Regenerate the IDN ccTLD mapping from IANA's Root Zone Database.

Fetches https://www.iana.org/domains/root/db and keeps the IDN (``xn--``) TLDs
whose Type is ``country-code``, writing them to a JSON mapping file that
``measure.metadata`` reads.

Design constraints (see README / PLAN):
- **Opportunistic:** run regularly, but on any scraping failure leave the
  existing mapping file untouched so the previously checked-in version is
  reused.
- **Sanity threshold:** if the scraped page yields fewer than
  :data:`MIN_PER_CATEGORY` IDN ccTLDs *or* fewer than that many IDN gTLDs,
  treat it as a scraping failure (the page layout probably changed) and do not
  write anything.
- **No-op on no change:** only rewrite the file when the mapping actually
  changed, so the daily job does not produce empty commits.

Exit codes: ``0`` success (file is current, whether or not it changed);
non-zero indicates a scraping failure and that the file was left as-is.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

IANA_DB_URL = "https://www.iana.org/domains/root/db"
MIN_PER_CATEGORY = 10


class ScrapeError(RuntimeError):
    """Raised when the page could not be fetched or parsed sensibly."""


class _RootDbParser(HTMLParser):
    """Extract ``(a_label, u_label, type)`` triples from the root-db table.

    Each table row looks like::

        <td><span class="domain tld"><a href="/domains/root/db/xn--p1ai.html">
            .&#x440;&#x444;</a></span></td>
        <td>country-code</td>
        <td>... TLD manager ...</td>
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = {"text": "", "label": None}
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href", "")
            prefix = "/domains/root/db/"
            if href.startswith(prefix) and href.endswith(".html"):
                self._cell["label"] = href[len(prefix) : -len(".html")]

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def fetch(url: str = IANA_DB_URL, timeout: float = 30.0) -> str:
    import httpx

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any fetch problem is a scrape failure
        raise ScrapeError(f"could not fetch {url}: {exc}") from exc
    return resp.text


def parse(html: str) -> tuple[dict[str, str], int]:
    """Return ``({a_label: u_label} for IDN ccTLDs, count of IDN gTLDs)``."""
    parser = _RootDbParser()
    parser.feed(html)

    idn_cctlds: dict[str, str] = {}
    idn_gtld_count = 0
    for row in parser.rows:
        if len(row) < 2:
            continue
        label = row[0].get("label")
        if not label or not label.startswith("xn--"):
            continue
        rtype = row[1]["text"].strip().lower()
        u_label = row[0]["text"].strip().lstrip(".")
        if rtype == "country-code":
            idn_cctlds[label] = u_label
        else:
            idn_gtld_count += 1
    return idn_cctlds, idn_gtld_count


def build_mapping(html: str) -> dict[str, str]:
    """Parse + validate; raise :class:`ScrapeError` if the result looks wrong."""
    idn_cctlds, idn_gtld_count = parse(html)
    if len(idn_cctlds) < MIN_PER_CATEGORY or idn_gtld_count < MIN_PER_CATEGORY:
        raise ScrapeError(
            "implausible scrape result "
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
        "source": IANA_DB_URL,
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
        "--source", default=IANA_DB_URL, help="root-db URL to scrape"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        mapping = build_mapping(fetch(args.source))
    except ScrapeError as exc:
        print(f"scraping failure: {exc}", file=sys.stderr)
        print("left existing mapping untouched", file=sys.stderr)
        return 2

    changed = update_file(args.output, mapping)
    state = "updated" if changed else "unchanged"
    print(f"{state}: {len(mapping)} IDN ccTLDs -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
