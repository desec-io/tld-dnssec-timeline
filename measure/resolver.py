"""Query a TLD's SOA through a validating resolver and read AD + EDE.

We send a normal recursive query with the DO bit set and CD clear, so the
resolver performs DNSSEC validation and reports its verdict via the AD flag, and
attaches Extended DNS Error (EDE, RFC 8914) options on failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import dns.edns
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype


@dataclass
class QueryResult:
    """The raw, classifier-agnostic outcome of one SOA query."""

    rcode: str  # textual RCODE, or "TIMEOUT" / "NETWORK" for transport failures
    ad: bool  # AD (Authenticated Data) flag in the response
    answered: bool  # an SOA RR was present in the answer section
    ede: list[tuple[int, str]] = field(default_factory=list)  # (code, text)
    error: str | None = None  # transport-level error detail, if any


def _extract_ede(response: dns.message.Message) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for opt in response.options:
        if isinstance(opt, dns.edns.EDEOption):
            out.append((opt.code, opt.text or ""))
    return out


def _has_soa_answer(response: dns.message.Message) -> bool:
    return any(
        rrset.rdtype == dns.rdatatype.SOA for rrset in response.answer
    )


def _is_transient(result: QueryResult) -> bool:
    """Whether ``result`` looks like a transient failure worth retrying.

    Timeouts and transport errors are transient by nature. A SERVFAIL with no
    Extended DNS Error is the signature of a resolver shedding load (e.g. Unbound
    jostling queries out of a saturated request list under high concurrency),
    not an authoritative verdict -- a genuinely bogus or unreachable TLD carries
    an EDE, so retrying those is harmless and does not change their status.
    """
    if result.rcode in ("TIMEOUT", "NETWORK"):
        return True
    return result.rcode == "SERVFAIL" and not result.ede


def _query_soa_once(
    qname: dns.name.Name,
    query: dns.message.Message,
    resolver: str,
    port: int,
    timeout: float,
    tcp_fallback: bool,
) -> QueryResult:
    try:
        response = dns.query.udp(query, resolver, port=port, timeout=timeout)
        if response.flags & dns.flags.TC and tcp_fallback:
            response = dns.query.tcp(query, resolver, port=port, timeout=timeout)
    except dns.exception.Timeout:
        return QueryResult(
            rcode="TIMEOUT", ad=False, answered=False, error="timeout"
        )
    except (OSError, dns.exception.DNSException) as exc:
        return QueryResult(
            rcode="NETWORK", ad=False, answered=False, error=str(exc)
        )

    return QueryResult(
        rcode=dns.rcode.to_text(response.rcode()),
        ad=bool(response.flags & dns.flags.AD),
        answered=_has_soa_answer(response),
        ede=_extract_ede(response),
    )


def query_soa(
    tld: str,
    resolver: str,
    port: int = 53,
    timeout: float = 5.0,
    tcp_fallback: bool = True,
    retries: int = 0,
    retry_backoff: float = 0.5,
) -> QueryResult:
    """Query ``SOA <tld>.`` against ``resolver`` and summarise the response.

    On a transient failure (see :func:`_is_transient`) the query is retried up to
    ``retries`` additional times with a small linear backoff, so a resolver
    momentarily shedding load does not get recorded as the TLD's DNSSEC status.
    """
    qname = dns.name.from_text(tld + ".")
    query = dns.message.make_query(qname, dns.rdatatype.SOA, want_dnssec=True)

    result = _query_soa_once(
        qname, query, resolver, port, timeout, tcp_fallback
    )
    for attempt in range(1, retries + 1):
        if not _is_transient(result):
            break
        time.sleep(retry_backoff * attempt)
        result = _query_soa_once(
            qname, query, resolver, port, timeout, tcp_fallback
        )
    return result
