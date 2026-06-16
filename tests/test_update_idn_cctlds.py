import json

import pytest

from tools.update_idn_cctlds import (
    ScrapeError,
    build_mapping,
    parse,
    update_file,
)


def _row(label, u_label, rtype):
    return (
        f'<tr><td><span class="domain tld">'
        f'<a href="/domains/root/db/{label}.html">.{u_label}</a></span></td>'
        f"<td>{rtype}</td><td>Some Manager</td></tr>"
    )


def _page(rows):
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def test_parse_splits_idn_cc_and_idn_g():
    html = _page(
        [
            _row("xn--p1ai", "рф", "country-code"),
            _row("xn--90ais", "бел", "country-code"),
            _row("xn--unup4y", "游戏", "generic"),
            _row("ac", "ac", "country-code"),  # ASCII ccTLD, ignored here
            _row("com", "com", "generic"),  # ASCII gTLD, ignored here
        ]
    )
    idn_cc, idn_g_count = parse(html)
    assert idn_cc == {"xn--p1ai": "рф", "xn--90ais": "бел"}
    assert idn_g_count == 1


def test_build_mapping_rejects_thin_scrape():
    # Only a couple of IDN entries -> below threshold -> scrape failure.
    html = _page([_row("xn--p1ai", "рф", "country-code")])
    with pytest.raises(ScrapeError):
        build_mapping(html)


def test_build_mapping_accepts_plausible_scrape():
    rows = [_row(f"xn--cc{i}", f"c{i}", "country-code") for i in range(12)]
    rows += [_row(f"xn--g{i}", f"g{i}", "generic") for i in range(12)]
    mapping = build_mapping(_page(rows))
    assert len(mapping) == 12
    assert list(mapping) == sorted(mapping)  # sorted output


def test_update_file_only_writes_on_change(tmp_path):
    path = tmp_path / "idn-cctlds.json"
    mapping = {"xn--p1ai": "рф"}
    assert update_file(path, mapping) is True  # created
    assert update_file(path, mapping) is False  # unchanged -> no rewrite
    assert json.loads(path.read_text())["idn_cctlds"] == mapping
    assert update_file(path, {"xn--p1ai": "рф", "xn--90ais": "бел"}) is True
