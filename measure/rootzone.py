"""Fetch the DNS root zone and extract the TLDs that carry a DS record.

The IANA-published root zone (https://www.internic.net/domain/root.zone) is a
plain-text zone file with one resource record per line, which lets us extract DS
records with a tolerant streaming parser instead of loading the whole zone into
a DNS library structure.
"""

from __future__ import annotations

from dataclasses import dataclass

ROOT_ZONE_URL = "https://www.internic.net/domain/root.zone"

_CLASSES = {"IN", "CH", "HS", "CS"}


@dataclass(frozen=True)
class TLD:
    """A top-level domain that has at least one DS record in the root zone."""

    name: str  # bare label, lowercase, no trailing dot (e.g. "se", "xn--p1ai")
    ds_count: int


def fetch_root_zone(url: str = ROOT_ZONE_URL, timeout: float = 60.0) -> str:
    """Download the root zone over HTTPS and return it as text."""
    import httpx  # lazy: parsing does not require the HTTP client

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _record_type(fields: list[str]) -> str | None:
    """Return the RR type token from the fields following the owner name.

    Fields look like ``[TTL] [CLASS] TYPE rdata...`` where TTL is numeric and
    CLASS is one of IN/CH/HS. The type is the first token that is neither.
    """
    for tok in fields:
        if tok.isdigit():
            continue
        if tok.upper() in _CLASSES:
            continue
        return tok.upper()
    return None


def parse_ds_signed_tlds(zone_text: str) -> list[TLD]:
    """Parse zone text and return the DS-signed TLDs, sorted by name."""
    counts: dict[str, int] = {}
    last_owner: str | None = None

    for raw in zone_text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith(";"):
            continue

        if line[0].isspace():
            owner = last_owner
            fields = line.split()
        else:
            fields = line.split()
            owner = fields[0]
            last_owner = owner
            fields = fields[1:]

        if owner is None or not fields:
            continue

        if _record_type(fields) != "DS":
            continue

        name = owner.rstrip(".").lower()
        # DS records in the root only appear at TLD delegation points (a single
        # label under the root); the dot check is defensive against surprises.
        if name and "." not in name:
            counts[name] = counts.get(name, 0) + 1

    return [TLD(name=n, ds_count=c) for n, c in sorted(counts.items())]
