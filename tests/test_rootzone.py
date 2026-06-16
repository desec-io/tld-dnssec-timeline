from pathlib import Path

from measure.rootzone import parse_ds_signed_tlds

FIXTURE = Path(__file__).parent / "fixtures" / "sample-root.zone"


def test_extracts_only_ds_signed_tlds():
    tlds = parse_ds_signed_tlds(FIXTURE.read_text())
    names = [t.name for t in tlds]
    assert names == ["com", "museum", "se", "xn--p1ai", "xn--unup4y"]


def test_counts_multiple_ds_records():
    by_name = {t.name: t.ds_count for t in parse_ds_signed_tlds(FIXTURE.read_text())}
    assert by_name["se"] == 2  # two DS records, including an elided-owner line
    assert by_name["com"] == 1


def test_unsigned_tlds_excluded():
    names = {t.name for t in parse_ds_signed_tlds(FIXTURE.read_text())}
    assert "org" not in names
    assert "example-unsigned" not in names
