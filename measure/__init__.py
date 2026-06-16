"""TLD DNSSEC measurement tool.

Fetches the DNS root zone, finds TLDs that carry at least one DS record, and
queries each one's SOA through a validating resolver to record its DNSSEC
validation status (with Extended DNS Error detail, RFC 8914).
"""

__version__ = "1.0.0"
