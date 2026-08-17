"""The deferred second pass and its control query.

These tests pin down the distinction the two-pass design exists to make: a TLD
that does not answer is not thereby unreachable -- we may simply have stopped
being able to see. Only a second silence, minutes later, with a control query
proving our own path out was working, is evidence about the TLD.
"""

import json

import pytest

from measure import __main__ as cli
from measure.classify import UNMEASURED
from measure.resolver import QueryResult
from measure.rootzone import TLD

TLDS = {
    "se": TLD(name="se", ds_count=1, ds_algorithms=frozenset({8})),
    "org": TLD(name="org", ds_count=0, ds_algorithms=frozenset()),
}

ANSWER = QueryResult(rcode="NOERROR", ad=True, answered=True)
SILENCE = QueryResult(rcode="TIMEOUT", ad=False, answered=False, error="timeout")


@pytest.fixture
def dns(monkeypatch):
    """Script the resolver's answers and the two control probes' verdicts."""

    class Fake:
        def __init__(self):
            self.answers = []
            self.vantage = True  # can we reach the root servers?
            self.authorities = None  # do the TLD's own servers answer directly?
            self.probes = 0

        def install(self):
            def query_soa(tld, resolver, **kw):
                return self.answers.pop(0)

            def probe_vantage(**kw):
                self.probes += 1
                return self.vantage

            def probe_authorities(tld, addresses, **kw):
                return self.authorities

            monkeypatch.setattr(cli, "query_soa", query_soa)
            monkeypatch.setattr(cli, "probe_vantage", probe_vantage)
            monkeypatch.setattr(cli, "probe_authorities", probe_authorities)
            monkeypatch.setattr(cli.time, "sleep", lambda s: None)
            return self

    return Fake().install()


PROBE = cli.Probe(resolver="127.0.0.1", timeout=1.0, retries=0)


def _measure(tld="se"):
    return cli._measure_one(TLDS[tld], PROBE)


def _recheck(records):
    return cli.recheck(records, TLDS, PROBE)


def test_silence_is_unmeasured_not_unreachable(dns):
    dns.answers = [SILENCE]
    assert _measure()["status"] == UNMEASURED


def test_first_pass_records_the_control_verdict(dns):
    # The control question is only answerable while the failure is current, so
    # the first pass asks it even though it decides nothing.
    dns.answers, dns.vantage = [SILENCE], False
    assert _measure()["control"]["vantage_ok"] is False
    assert dns.probes == 1


def test_no_control_probe_when_the_tld_answers(dns):
    dns.answers = [ANSWER]
    assert dns.probes == 0


def test_transient_silence_recovers_on_the_second_pass(dns):
    dns.answers = [SILENCE, ANSWER]
    first = _measure()
    (settled,) = _recheck([first])
    assert settled["status"] == "secure"
    # Both observations are retained so the recovery is auditable.
    assert [c["status"] for c in settled["checks"]] == [UNMEASURED, "secure"]


def test_persistent_silence_is_unreachable_when_both_controls_agree(dns):
    # We could reach the root servers, and the TLD's own authorities ignored a
    # direct probe too: the silence is the TLD's.
    dns.answers, dns.vantage, dns.authorities = [SILENCE, SILENCE], True, False
    (settled,) = _recheck([_measure()])
    assert settled["status"] == "unreachable"
    # A bare timeout carries no EDE, so the verdict explains itself.
    assert settled["ede"] == [cli._SYNTHETIC_UNREACHABLE_EDE]
    assert [c["vantage_ok"] for c in settled["checks"]] == [True, True]


def test_persistent_silence_while_blind_stays_unmeasured(dns):
    # The regression this whole design exists for: our own outage must never be
    # recorded as a property of the TLD.
    dns.answers, dns.vantage = [SILENCE, SILENCE], False
    (settled,) = _recheck([_measure()])
    assert settled["status"] == UNMEASURED
    assert settled["ede"] == []


def test_reachable_authorities_exonerate_a_silent_tld(dns):
    # The sharpest case: our resolver got nothing, but the TLD's own servers
    # answer us directly. The TLD is plainly fine; the failure was ours.
    dns.answers, dns.vantage, dns.authorities = [SILENCE, SILENCE], True, True
    (settled,) = _recheck([_measure()])
    assert settled["status"] == UNMEASURED


def test_missing_glue_is_not_evidence_against_the_tld(dns):
    # Nothing to probe means we could not ask, which is not the same as the
    # authorities failing to answer. Without positive evidence the TLD keeps
    # the benefit of the doubt -- this is the exact reading that, with a
    # blackholed resolver on a healthy network, blamed 6 of 9 TLDs.
    dns.answers, dns.vantage, dns.authorities = [SILENCE, SILENCE], True, None
    (settled,) = _recheck([_measure()])
    assert settled["status"] == UNMEASURED


def test_recheck_replaces_a_bogus_verdict_that_does_not_reproduce(dns):
    # Not just timeouts: a one-off bogus is provisional too.
    dns.answers = [
        QueryResult(rcode="SERVFAIL", ad=False, answered=False, ede=[(6, "bogus")]),
        ANSWER,
    ]
    first = _measure()
    assert first["status"] == "bogus"
    (settled,) = _recheck([first])
    assert settled["status"] == "secure"


def test_unmeasured_tlds_are_held_out_of_the_published_results(dns, tmp_path):
    zone = tmp_path / "root.zone"
    zone.write_text(
        "se.\t172800\tIN\tNS\ta.ns.se.\n"
        "se.\t86400\tIN\tDS\t36900 8 2 DEADBEEF\n"
        "org.\t172800\tIN\tNS\ta0.org.afilias-nst.info.\n"
    )
    # org answers; se is silent in both passes and we are blind throughout.
    dns.answers, dns.vantage = [ANSWER, SILENCE, SILENCE], False
    rc = cli.main(
        [
            "--root-zone-file", str(zone),
            "--output", str(tmp_path / "data"),
            "--date", "2026-08-13",
            "--recheck-delay", "0",
        ]
    )
    assert rc == 0

    day = json.loads((tmp_path / "data/measurements/2026-08-13.json").read_text())
    assert [r["tld"] for r in day["results"]] == ["org"]
    assert [r["tld"] for r in day["unmeasured"]] == ["se"]

    # The timeline must show a gap for se, not an invented status.
    timeline = json.loads((tmp_path / "data/timeline.json").read_text())
    counts = timeline["days"][0]["counts"]
    assert sum(sum(c.values()) for c in counts.values()) == 1
    assert UNMEASURED not in timeline["statuses"]
    history = json.loads((tmp_path / "data/tld-history.json").read_text())
    assert "se" not in history["tlds"]
