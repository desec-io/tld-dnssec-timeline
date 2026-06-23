from pathlib import Path

from measure.rootzone import parse_tlds

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
