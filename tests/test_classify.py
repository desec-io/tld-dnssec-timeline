from measure.classify import classify
from measure.resolver import QueryResult


def test_secure_when_noerror_answered_and_ad():
    r = QueryResult(rcode="NOERROR", ad=True, answered=True)
    assert classify(r) == "secure"


def test_insecure_when_answered_without_ad_and_no_ds():
    r = QueryResult(rcode="NOERROR", ad=False, answered=True)
    assert classify(r) == "insecure"


def test_insecure_when_signed_only_with_optional_algorithm():
    # museum-style: DS uses algorithm 7, which a validator may decline.
    r = QueryResult(rcode="NOERROR", ad=False, answered=True)
    assert classify(r, frozenset({7})) == "insecure"


def test_error_when_signed_with_mandatory_algorithm_without_ad():
    # A TLD signed with a MUST-implement algorithm must validate or SERVFAIL.
    r = QueryResult(rcode="NOERROR", ad=False, answered=True)
    assert classify(r, frozenset({8})) == "error"
    assert classify(r, frozenset({13})) == "error"


def test_secure_overrides_mandatory_algorithm():
    r = QueryResult(rcode="NOERROR", ad=True, answered=True)
    assert classify(r, frozenset({8})) == "secure"


def test_bogus_on_dnssec_ede():
    r = QueryResult(rcode="SERVFAIL", ad=False, answered=False, ede=[(6, "bogus")])
    assert classify(r) == "bogus"


def test_bogus_on_expired_signature_ede():
    r = QueryResult(rcode="SERVFAIL", ad=False, answered=False, ede=[(7, "expired")])
    assert classify(r) == "bogus"


def test_unreachable_on_connectivity_ede():
    r = QueryResult(rcode="SERVFAIL", ad=False, answered=False, ede=[(22, "no auth")])
    assert classify(r) == "unreachable"


def test_unreachable_on_timeout():
    r = QueryResult(rcode="TIMEOUT", ad=False, answered=False, error="timeout")
    assert classify(r) == "unreachable"


def test_error_on_local_network_failure():
    r = QueryResult(rcode="NETWORK", ad=False, answered=False, error="boom")
    assert classify(r) == "error"


def test_error_on_servfail_without_ede():
    r = QueryResult(rcode="SERVFAIL", ad=False, answered=False)
    assert classify(r) == "error"
