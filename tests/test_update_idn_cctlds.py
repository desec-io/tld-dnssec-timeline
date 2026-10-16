import json

import pytest

from tools.update_idn_cctlds import (
    MappingUpdateError,
    build_mapping,
    parse,
    update_file,
)


def _domain(name, ulabel, rtype):
    return {"name": name, "ulabel": ulabel, "status": "active", "type": rtype}


def _export(domains, specversion=1):
    return json.dumps({"meta": {"specversion": specversion}, "domains": domains})


def test_parse_splits_idn_cc_and_idn_g():
    text = _export(
        [
            _domain("xn--p1ai", "рф", "country-code"),
            _domain("xn--90ais", "бел", "country-code"),
            _domain("xn--unup4y", "游戏", "generic"),
            _domain("ac", None, "country-code"),  # ASCII ccTLD, ignored here
            _domain("com", None, "generic"),  # ASCII gTLD, ignored here
        ]
    )
    idn_cc, idn_g_count = parse(text)
    assert idn_cc == {"xn--p1ai": "рф", "xn--90ais": "бел"}
    assert idn_g_count == 1


def test_parse_rejects_unknown_specversion():
    with pytest.raises(MappingUpdateError):
        parse(_export([_domain("xn--p1ai", "рф", "country-code")], specversion=2))


def test_parse_rejects_malformed_export():
    with pytest.raises(MappingUpdateError):
        parse("<html>not json</html>")
    with pytest.raises(MappingUpdateError):
        parse(_export([{"name": "xn--p1ai", "ulabel": "рф"}]))  # no type


def test_build_mapping_rejects_thin_export():
    # Only a couple of IDN entries -> below threshold -> mapping update failure.
    text = _export([_domain("xn--p1ai", "рф", "country-code")])
    with pytest.raises(MappingUpdateError):
        build_mapping(text)


def test_build_mapping_accepts_plausible_export():
    rows = [_domain(f"xn--cc{i}", f"c{i}", "country-code") for i in range(12)]
    rows += [_domain(f"xn--g{i}", f"g{i}", "generic") for i in range(12)]
    mapping = build_mapping(_export(rows))
    assert len(mapping) == 12
    assert list(mapping) == sorted(mapping)  # sorted output


def test_update_file_only_writes_on_change(tmp_path):
    path = tmp_path / "idn-cctlds.json"
    mapping = {"xn--p1ai": "рф"}
    assert update_file(path, mapping) is True  # created
    assert update_file(path, mapping) is False  # unchanged -> no rewrite
    assert json.loads(path.read_text())["idn_cctlds"] == mapping
    assert update_file(path, {"xn--p1ai": "рф", "xn--90ais": "бел"}) is True
