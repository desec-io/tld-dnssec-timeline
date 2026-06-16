"""Map a raw query result to a single DNSSEC status.

Statuses:
- ``secure``      NOERROR with an SOA answer and the AD bit set (validated).
- ``insecure``    NOERROR with an SOA answer but AD clear. Anomalous, since the
                  TLD has a DS record, so this means the chain was not validated.
- ``bogus``       failure carrying a DNSSEC-related EDE code.
- ``unreachable`` failure carrying a connectivity EDE code, or a timeout.
- ``error``       any other failure / unclassifiable outcome.

EDE code groups follow RFC 8914.
"""

from __future__ import annotations

from .resolver import QueryResult

# RFC 8914 codes that indicate a DNSSEC validation failure.
DNSSEC_EDE = frozenset({1, 2, 5, 6, 7, 8, 9, 10, 11, 12})
# RFC 8914 codes that indicate the authority could not be reached.
UNREACHABLE_EDE = frozenset({22, 23})

STATUSES = ("secure", "insecure", "bogus", "unreachable", "error")


def classify(result: QueryResult) -> str:
    """Return one of :data:`STATUSES` for the given query result."""
    if result.rcode == "TIMEOUT":
        # The resolver could not obtain an answer within the deadline; treat as
        # an unreachable authority (the error detail is retained in the record).
        return "unreachable"
    if result.rcode == "NETWORK":
        # We could not talk to our own resolver: a tooling problem, not a TLD
        # property.
        return "error"

    if result.rcode == "NOERROR" and result.answered:
        return "secure" if result.ad else "insecure"

    codes = {code for code, _ in result.ede}
    if codes & DNSSEC_EDE:
        return "bogus"
    if codes & UNREACHABLE_EDE:
        return "unreachable"
    return "error"
