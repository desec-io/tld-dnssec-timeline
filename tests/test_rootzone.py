from pathlib import Path

from measure.rootzone import parse_glue, parse_tlds

FIXTURE = Path(__file__).parent / "fixtures" / "sample-root.zone"


def _by_name():
    return {t.name: t for t in parse_tlds(FIXTURE.read_text())}


def test_returns_all_delegated_tlds():
    names = [t.name for t in parse_tlds(FIXTURE.read_text())]
    assert names == [
        "com",
        "example-unsigned",
        "museum",
        "org",
        "se",
        "xn--p1ai",
        "xn--unup4y",
    ]


def test_counts_multiple_ds_records():
    by_name = _by_name()
    assert by_name["se"].ds_count == 2  # two DS records, incl. an elided-owner line
    assert by_name["com"].ds_count == 1


def test_unsigned_tlds_have_no_ds():
    by_name = _by_name()
    assert by_name["org"].ds_count == 0
    assert by_name["org"].ds_algorithms == frozenset()
    assert by_name["example-unsigned"].ds_count == 0
    assert by_name["example-unsigned"].ds_algorithms == frozenset()


def test_captures_ds_algorithms():
    by_name = _by_name()
    assert by_name["com"].ds_algorithms == frozenset({13})
    assert by_name["se"].ds_algorithms == frozenset({8})
    assert by_name["museum"].ds_algorithms == frozenset({7})


def test_glue_maps_tlds_to_their_nameserver_addresses():
    glue = parse_glue(FIXTURE.read_text())
    assert glue["se"] == ["192.36.144.107"]
    assert glue["com"] == ["192.5.6.30"]
    assert glue["museum"] == ["199.254.31.1"]


def test_glue_skips_ipv6_to_match_the_resolvers_egress():
    # a.ns.se. has an AAAA in the fixture; the measurement resolver runs
    # do-ip6:no, so probing v6 would answer a question we never asked.
    assert parse_glue(FIXTURE.read_text())["se"] == ["192.36.144.107"]


def test_glue_is_empty_for_out_of_bailiwick_nameservers_without_addresses():
    # org's nameserver lives under afilias-nst.info, for which the fixture
    # carries no address record: callers must cope with having no glue.
    assert parse_glue(FIXTURE.read_text())["org"] == []


def test_glue_covers_every_delegated_tld():
    glue = parse_glue(FIXTURE.read_text())
    assert set(glue) == {t.name for t in parse_tlds(FIXTURE.read_text())}
