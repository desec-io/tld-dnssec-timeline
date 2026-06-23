"""Map a raw query result to a single DNSSEC status.

Statuses:
- ``secure``      NOERROR with an SOA answer and the AD bit set (validated).
- ``insecure``    NOERROR with an SOA answer, AD clear, and the TLD is not
                  signed with an algorithm a validator must support — so an
                  unauthenticated answer is the expected outcome (the TLD has no
                  DS, or is signed only with algorithms a validator may decline,
                  which RFC 4035 treats as insecure).
- ``bogus``       failure carrying a DNSSEC-related EDE code.
- ``unreachable`` failure carrying a connectivity EDE code, or a timeout.
- ``error``       any other failure, including a TLD signed with a
                  mandatory-to-support algorithm that answered without the AD
                  bit — a validator must either validate it or SERVFAIL, so an
                  unauthenticated NOERROR answer is anomalous.

EDE code groups follow RFC 8914.
"""

from __future__ import annotations

from .resolver import QueryResult

# RFC 8914 codes that indicate a DNSSEC validation failure.
DNSSEC_EDE = frozenset({1, 2, 5, 6, 7, 8, 9, 10, 11, 12})
# RFC 8914 codes that indicate the authority could not be reached.
UNREACHABLE_EDE = frozenset({22, 23})

# DNSKEY algorithm numbers a validating resolver MUST implement (RFC 8624):
# RSASHA256 (8) and ECDSAP256SHA256 (13). A TLD whose DS uses one of these can
# always be validated, so an unauthenticated answer for it is anomalous; a TLD
# signed only with optional algorithms (e.g. 7, 10, 14, 15) may legitimately go
# unvalidated, which is indistinguishable from being unsigned.
MANDATORY_TO_SUPPORT_ALGORITHMS = frozenset({8, 13})

STATUSES = ("secure", "insecure", "bogus", "unreachable", "error")


def classify(
    result: QueryResult, ds_algorithms: frozenset[int] = frozenset()
) -> str:
    """Return one of :data:`STATUSES` for the given query result.

    ``ds_algorithms`` is the set of DS algorithm numbers the TLD publishes in
    the root (empty for an unsigned TLD); it distinguishes an expected
    unauthenticated answer (``insecure``) from an anomalous one (``error``).
    """
    if result.rcode == "TIMEOUT":
        # The resolver could not obtain an answer within the deadline; treat as
        # an unreachable authority (the error detail is retained in the record).
        return "unreachable"
    if result.rcode == "NETWORK":
        # We could not talk to our own resolver: a tooling problem, not a TLD
        # property.
        return "error"

    if result.rcode == "NOERROR" and result.answered:
        if result.ad:
            return "secure"
        # An unauthenticated answer. If the TLD is signed with an algorithm the
        # resolver must support, it should have validated (or returned SERVFAIL);
        # an unauthenticated NOERROR answer is then anomalous. Otherwise the
        # answer is the expected outcome for an insecure (unsigned, or
        # optional-algorithm-only) TLD.
        if ds_algorithms & MANDATORY_TO_SUPPORT_ALGORITHMS:
            return "error"
        return "insecure"

    codes = {code for code, _ in result.ede}
    if codes & DNSSEC_EDE:
        return "bogus"
    if codes & UNREACHABLE_EDE:
        return "unreachable"
    return "error"
