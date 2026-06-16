import json

from measure.metadata import (
    class_key_from_class,
    classify_tld,
    is_idn,
    load_idn_cctlds,
    tld_type,
)

MAPPING = frozenset({"xn--p1ai", "xn--90ais"})


def test_two_letter_ascii_is_cctld():
    assert tld_type("se", MAPPING) == "ccTLD"
    assert is_idn("se") is False


def test_generic_is_gtld():
    assert tld_type("com", MAPPING) == "gTLD"
    assert tld_type("museum", MAPPING) == "gTLD"


def test_idn_cctld_from_mapping():
    assert is_idn("xn--p1ai") is True
    assert tld_type("xn--p1ai", MAPPING) == "ccTLD"  # Russia, рф


def test_idn_not_in_mapping_defaults_to_gtld():
    assert tld_type("xn--unup4y", MAPPING) == "gTLD"  # generic IDN


def test_class_key_from_class():
    assert class_key_from_class({"type": "gTLD", "idn": False}) == "g-noidn"
    assert class_key_from_class({"type": "ccTLD", "idn": False}) == "cc-noidn"
    assert class_key_from_class({"type": "ccTLD", "idn": True}) == "cc-idn"
    assert class_key_from_class({"type": "gTLD", "idn": True}) == "g-idn"


def test_classify_tld_shape():
    assert classify_tld("se", MAPPING) == {"type": "ccTLD", "idn": False}


def test_load_missing_mapping_is_empty(tmp_path):
    assert load_idn_cctlds(tmp_path / "absent.json") == frozenset()


def test_load_mapping_from_file(tmp_path):
    path = tmp_path / "idn-cctlds.json"
    path.write_text(json.dumps({"idn_cctlds": {"xn--p1ai": "рф"}}))
    assert load_idn_cctlds(path) == frozenset({"xn--p1ai"})
