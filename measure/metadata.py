"""Classify each TLD on two axes for the web app's filters.

- ``idn``:  whether the TLD is an Internationalized Domain Name (A-label,
            i.e. the label starts with ``xn--``).
- ``type``: ``ccTLD`` (country-code) vs ``gTLD`` (everything else).

Non-IDN names classify by the usual rule (two ASCII letters => ccTLD). IDN
names cannot be told apart by length, so the country-code ones are read from a
checked-in mapping file (``idn-cctlds.json`` in the data directory), which is
regenerated from IANA by ``tools/update_idn_cctlds.py``. There is no hardcoded
fallback: if the mapping file is absent, every ``xn--`` TLD defaults to
``gTLD`` until the mapping is created.
"""

from __future__ import annotations

import json
from pathlib import Path

CLASS_KEYS = ("g-noidn", "g-idn", "cc-noidn", "cc-idn")
MAPPING_FILENAME = "idn-cctlds.json"


def is_idn(tld: str) -> bool:
    """True if the TLD label is an IDN A-label."""
    return tld.startswith("xn--")


def load_idn_cctlds(path: str | Path) -> frozenset[str]:
    """Load the set of IDN ccTLD A-labels from the mapping file.

    Returns an empty set if the file does not exist, so a first run (before the
    mapping has ever been generated) degrades gracefully.
    """
    p = Path(path)
    if not p.exists():
        return frozenset()
    data = json.loads(p.read_text())
    return frozenset(data.get("idn_cctlds", {}))


def tld_type(tld: str, idn_cctlds: frozenset[str]) -> str:
    """Return ``"ccTLD"`` or ``"gTLD"`` for the given TLD label."""
    if is_idn(tld):
        return "ccTLD" if tld in idn_cctlds else "gTLD"
    if len(tld) == 2 and tld.isalpha():
        return "ccTLD"
    return "gTLD"


def classify_tld(tld: str, idn_cctlds: frozenset[str]) -> dict[str, object]:
    """Return the ``{"type": ..., "idn": ...}`` metadata for a TLD."""
    return {"type": tld_type(tld, idn_cctlds), "idn": is_idn(tld)}


def class_key_from_class(cls: dict[str, object]) -> str:
    """Map a stored ``{"type", "idn"}`` record to a compact timeline key.

    One of: ``g-noidn``, ``g-idn``, ``cc-noidn``, ``cc-idn``. Using the stored
    class (rather than re-deriving from the TLD name) keeps each day's
    aggregation consistent with how it was classified at measurement time.
    """
    prefix = "cc" if cls.get("type") == "ccTLD" else "g"
    suffix = "idn" if cls.get("idn") else "noidn"
    return f"{prefix}-{suffix}"
