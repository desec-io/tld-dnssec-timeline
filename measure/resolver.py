"""Query a TLD's SOA through a validating resolver and read AD + EDE.

We send a normal recursive query with the DO bit set and CD clear, so the
resolver performs DNSSEC validation and reports its verdict via the AD flag, and
attaches Extended DNS Error (EDE, RFC 8914) options on failure.
"""

from __future__ import annotations

import random
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


# Control probes deliberately bypass our own recursive resolver and speak to
# authoritative servers directly. Going through the resolver would prove
# nothing: a cached answer never leaves the machine, and a nonce name is no
# help either, because with RFC 8198 aggressive NSEC use (on by default in
# Unbound) a resolver that has walked the root can synthesize the NXDOMAIN for
# a random TLD straight from cache. A direct query to an authoritative server
# is the only form of control that must put a packet on the wire.
#
# IPv4 only, to match the measurement resolver's `do-ip6: no`: probing a path
# the resolver never uses would answer the wrong question.
ROOT_SERVER_ADDRESSES = (
    "198.41.0.4",  # a.root-servers.net
    "170.247.170.2",  # b
    "192.33.4.12",  # c
    "199.7.91.13",  # d
    "192.203.230.10",  # e
    "192.5.5.241",  # f
    "192.112.36.4",  # g
    "198.97.190.53",  # h
    "192.36.148.17",  # i
    "192.58.128.30",  # j
    "193.0.14.129",  # k
    "199.7.83.42",  # l
    "202.12.27.33",  # m
)

# How many servers a control probe contacts. Every one of them must answer for
# the probe to pass: the bias is deliberate, because a failed control only
# costs us a data point, whereas a control that passes when it should not lets
# us record our own outage as a property of somebody's TLD.
CONTROL_PROBES = 3


def _probe_addresses(
    addresses: list[str],
    qname: dns.name.Name,
    rdtype: int,
    timeout: float,
    require_all: bool,
) -> bool | None:
    """Ask each address a direct, non-recursive question; did they answer?

    ``require_all`` picks the burden of proof. Both callers set it so that the
    doubt falls on us rather than on a TLD: the vantage check must see *every*
    server it asked (so a single lost packet makes us distrust our own sight),
    while the authority check needs only *one* reply (so one flaky anycast node
    cannot stop a reachable TLD from being cleared).

    Returns ``None`` when there is nothing to probe, which the caller must treat
    as "no evidence" rather than as either verdict.
    """
    if not addresses:
        return None
    query = dns.message.make_query(qname, rdtype, want_dnssec=True)
    # These are authoritative servers; asking them to recurse is meaningless and
    # some will refuse outright.
    query.flags &= ~dns.flags.RD
    answered = 0
    for address in addresses:
        result = _query_soa_once(
            qname, query, address, 53, timeout, tcp_fallback=False
        )
        if result.rcode in ("TIMEOUT", "NETWORK"):
            if require_all:
                return False
        else:
            answered += 1
            if not require_all:
                return True
    return answered == len(addresses)


def probe_vantage(timeout: float = 5.0, probes: int = CONTROL_PROBES) -> bool:
    """Whether our own path out to the DNS is working *right now*.

    A TLD that does not answer tells us nothing on its own: it may be genuinely
    unreachable, or we may be the ones who went blind. Root servers are the
    control because they are anycast, globally reachable, and built for exactly
    this kind of trivial query; if a random few of them cannot be reached, the
    problem is at our end.

    A single attempt per server, no retries: a dropped control packet should
    make us doubt our own vantage point, because the alternative is blaming a
    TLD for our packet loss.
    """
    addresses = random.sample(
        ROOT_SERVER_ADDRESSES, min(probes, len(ROOT_SERVER_ADDRESSES))
    )
    return bool(
        _probe_addresses(
            addresses, dns.name.root, dns.rdatatype.SOA, timeout, require_all=True
        )
    )


def probe_authorities(
    tld: str, addresses: list[str], timeout: float = 5.0
) -> bool | None:
    """Whether the TLD's own authoritative servers answer us directly.

    This is the sharpest evidence available about a silent TLD. If its servers
    answer a direct query while our validating resolver could not get an answer
    out of them, then the TLD is plainly reachable and the failure is ours --
    which is the opposite of what a timeout would otherwise be recorded as. One
    reply is enough to establish that.

    Returns ``None`` if the root zone carries no addresses for the TLD's
    nameservers, leaving the caller with only the weaker root-server control.
    """
    qname = dns.name.from_text(tld + ".")
    return _probe_addresses(
        addresses, qname, dns.rdatatype.SOA, timeout, require_all=False
    )
