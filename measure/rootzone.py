"""Fetch the DNS root zone and extract the delegated TLDs.

The IANA-published root zone (https://www.internic.net/domain/root.zone) is a
plain-text zone file with one resource record per line, which lets us extract
the delegations (NS) and their DNSSEC entry points (DS) with a tolerant
streaming parser instead of loading the whole zone into a DNS library structure.
"""

from __future__ import annotations

from dataclasses import dataclass

ROOT_ZONE_URL = "https://www.internic.net/domain/root.zone"

_CLASSES = {"IN", "CH", "HS", "CS"}


@dataclass(frozen=True)
class TLD:
    """A delegated top-level domain and its DS (DNSSEC) entry points."""

    name: str  # bare label, lowercase, no trailing dot (e.g. "se", "xn--p1ai")
    ds_count: int  # number of DS records in the root (0 = unsigned delegation)
    ds_algorithms: frozenset[int]  # algorithm numbers across those DS records


def fetch_root_zone(url: str = ROOT_ZONE_URL, timeout: float = 60.0) -> str:
    """Download the root zone over HTTPS and return it as text."""
    import httpx  # lazy: parsing does not require the HTTP client

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _split_type(fields: list[str]) -> tuple[str | None, list[str]]:
    """Split ``[TTL] [CLASS] TYPE rdata...`` into ``(TYPE, [rdata...])``.

    The fields follow the owner name; TTL is numeric and CLASS is one of
    IN/CH/HS/CS, so the type is the first token that is neither, and everything
    after it is rdata.
    """
    for i, tok in enumerate(fields):
        if tok.isdigit():
            continue
        if tok.upper() in _CLASSES:
            continue
        return tok.upper(), fields[i + 1 :]
    return None, []


def parse_tlds(zone_text: str) -> list[TLD]:
    """Parse zone text and return every delegated TLD, sorted by name.

    A TLD is an owner name with a single label (one level under the root) that
    carries an ``NS`` record. Its ``ds_count`` and ``ds_algorithms`` summarise
    the DS records at the same delegation point; both are empty/zero for an
    unsigned TLD.
    """
    ns_tlds: set[str] = set()
    ds_counts: dict[str, int] = {}
    ds_algorithms: dict[str, set[int]] = {}
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

        rtype, rdata = _split_type(fields)
        if rtype not in ("NS", "DS"):
            continue

        name = owner.rstrip(".").lower()
        # Only single-label names directly under the root are TLDs; the dot
        # check skips nameserver glue and other deeper records.
        if not name or "." in name:
            continue

        if rtype == "NS":
            ns_tlds.add(name)
        else:  # DS
            ds_counts[name] = ds_counts.get(name, 0) + 1
            # DS rdata is "KeyTag Algorithm DigestType Digest"; the algorithm is
            # the second field.
            if len(rdata) >= 2 and rdata[1].isdigit():
                ds_algorithms.setdefault(name, set()).add(int(rdata[1]))

    return [
        TLD(
            name=name,
            ds_count=ds_counts.get(name, 0),
            ds_algorithms=frozenset(ds_algorithms.get(name, ())),
        )
        for name in sorted(ns_tlds)
    ]
