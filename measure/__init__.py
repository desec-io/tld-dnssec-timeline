"""TLD DNSSEC measurement tool.

Fetches the DNS root zone, enumerates every delegated TLD, and queries each
one's SOA through a validating resolver to record its DNSSEC
validation status (with Extended DNS Error detail, RFC 8914).
"""

__version__ = "1.0.0"
