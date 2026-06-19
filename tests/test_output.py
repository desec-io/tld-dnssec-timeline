import json

from measure.metadata import classify_tld
from measure.output import regenerate_derived, write_daily

MAPPING = frozenset({"xn--p1ai"})


def _day(date, results):
    return {
        "measurement_date": date,
        "tool_version": "test",
        "resolver": "127.0.0.1",
        "results": results,
    }


def _result(tld, status):
    return {
        "tld": tld,
        "timestamp": f"{tld}T00:00:00Z",
        "status": status,
        "ad": status == "secure",
        "rcode": "NOERROR",
        "ds_count": 1,
        "ede": [],
        "class": classify_tld(tld, MAPPING),
    }


def test_write_and_aggregate(tmp_path):
    write_daily(tmp_path, _day("2026-06-15", [_result("se", "secure")]))
    write_daily(
        tmp_path,
        _day(
            "2026-06-16",
            [
                _result("se", "secure"),
                _result("com", "bogus"),
                _result("xn--p1ai", "secure"),
                _result("xn--unup4y", "unreachable"),
            ],
        ),
    )
    regenerate_derived(tmp_path)

    index = json.loads((tmp_path / "index.json").read_text())
    assert index["dates"] == ["2026-06-15", "2026-06-16"]

    timeline = json.loads((tmp_path / "timeline.json").read_text())
    day16 = next(d for d in timeline["days"] if d["date"] == "2026-06-16")
    assert day16["counts"]["cc-noidn"]["secure"] == 1  # se
    assert day16["counts"]["g-noidn"]["bogus"] == 1  # com
    assert day16["counts"]["cc-idn"]["secure"] == 1  # xn--p1ai
    assert day16["counts"]["g-idn"]["unreachable"] == 1  # xn--unup4y


def test_tld_history_transitions(tmp_path):
    # se: secure throughout; com: appears day 2, goes bogus, then disappears.
    write_daily(tmp_path, _day("2026-06-15", [_result("se", "secure")]))
    write_daily(
        tmp_path,
        _day("2026-06-16", [_result("se", "secure"), _result("com", "secure")]),
    )
    write_daily(
        tmp_path,
        _day("2026-06-17", [_result("se", "secure"), _result("com", "bogus")]),
    )
    write_daily(tmp_path, _day("2026-06-18", [_result("se", "secure")]))
    regenerate_derived(tmp_path)

    hist = json.loads((tmp_path / "tld-history.json").read_text())
    assert hist["dates"] == [
        "2026-06-15",
        "2026-06-16",
        "2026-06-17",
        "2026-06-18",
    ]
    assert hist["classes"]["se"] == "cc-noidn"
    assert hist["classes"]["com"] == "g-noidn"
    # se never changes: a single entry at day 0.
    assert hist["tlds"]["se"] == [[0, "s"]]
    # com: secure at day 1, bogus at day 2, absent (gap) at day 3.
    assert hist["tlds"]["com"] == [[1, "s"], [2, "b"], [3, "-"]]


def test_rerun_same_day_overwrites(tmp_path):
    write_daily(tmp_path, _day("2026-06-16", [_result("se", "secure")]))
    write_daily(tmp_path, _day("2026-06-16", [_result("se", "bogus")]))
    regenerate_derived(tmp_path)

    index = json.loads((tmp_path / "index.json").read_text())
    assert index["dates"] == ["2026-06-16"]
    timeline = json.loads((tmp_path / "timeline.json").read_text())
    assert timeline["days"][0]["counts"]["cc-noidn"]["bogus"] == 1
