"""The control probes and, above all, their deliberately asymmetric burdens.

Both probes are tuned so that uncertainty costs us a data point rather than
costing a TLD its reputation: we must see *every* root server we ask before
trusting our own sight, but a single reply from a TLD's authorities is enough
to clear it.
"""

import dns.rdatatype
import pytest

from measure import resolver as res
from measure.resolver import QueryResult

ANSWER = QueryResult(rcode="NOERROR", ad=True, answered=True)
REFUSED = QueryResult(rcode="REFUSED", ad=False, answered=False)
SILENCE = QueryResult(rcode="TIMEOUT", ad=False, answered=False, error="timeout")


@pytest.fixture
def wire(monkeypatch):
    """Script per-address responses and record who was actually contacted."""

    class Fake:
        def __init__(self):
            self.by_address = {}
            self.default = ANSWER
            self.asked = []
            self.queries = []

        def install(self):
            def once(qname, query, address, port, timeout, tcp_fallback):
                self.asked.append(address)
                self.queries.append(query)
                return self.by_address.get(address, self.default)

            monkeypatch.setattr(res, "_query_soa_once", once)
            return self

    return Fake().install()


def test_probes_never_go_through_our_own_resolver(wire):
    # The whole point: a cached answer proves nothing, and with RFC 8198
    # aggressive NSEC a resolver can even synthesize NXDOMAIN for a nonce TLD
    # without emitting a packet. Only authoritative servers are contacted.
    res.probe_vantage(timeout=1.0, probes=3)
    assert set(wire.asked) <= set(res.ROOT_SERVER_ADDRESSES)


def test_probes_are_non_recursive(wire):
    res.probe_vantage(timeout=1.0, probes=1)
    assert not (wire.queries[0].flags & res.dns.flags.RD)


def test_vantage_needs_every_root_server_it_asked(wire):
    wire.by_address = {res.ROOT_SERVER_ADDRESSES[0]: SILENCE}
    # Ask all 13 so the one silent server is certainly among them.
    assert res.probe_vantage(timeout=1.0, probes=13) is False


def test_vantage_passes_when_all_answer(wire):
    assert res.probe_vantage(timeout=1.0, probes=3) is True


def test_vantage_counts_any_response_as_sight(wire):
    # We are testing reachability, not hospitality: a REFUSED still proves the
    # packet made the round trip.
    wire.default = REFUSED
    assert res.probe_vantage(timeout=1.0, probes=3) is True


def test_authorities_cleared_by_a_single_reply(wire):
    # One flaky anycast node must not stop a reachable TLD being exonerated.
    wire.by_address = {"192.0.2.1": SILENCE, "192.0.2.2": ANSWER}
    assert res.probe_authorities("se", ["192.0.2.1", "192.0.2.2"], timeout=1.0) is True


def test_authorities_false_only_when_all_are_silent(wire):
    wire.default = SILENCE
    assert res.probe_authorities("se", ["192.0.2.1", "192.0.2.2"], timeout=1.0) is False


def test_authorities_stop_at_the_first_reply(wire):
    res.probe_authorities("se", ["192.0.2.1", "192.0.2.2"], timeout=1.0)
    assert wire.asked == ["192.0.2.1"]


def test_authorities_without_glue_yield_no_evidence(wire):
    # Distinct from False: "we could not ask" is not "they did not answer".
    assert res.probe_authorities("se", [], timeout=1.0) is None
    assert wire.asked == []


def test_authorities_are_asked_about_the_tld_itself(wire):
    res.probe_authorities("se", ["192.0.2.1"], timeout=1.0)
    assert wire.queries[0].question[0].rdtype == dns.rdatatype.SOA
    assert wire.queries[0].question[0].name.to_text() == "se."
