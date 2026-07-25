from pathlib import Path
import pytest
from trunk_uploader.config import load_config, parse_bool, parse_talkgroups, rules_overlap


def write_config(tmp_path: Path, extra=""):
    text = (Path(__file__).parents[1] / "config/uploader.conf.example").read_text().replace("default_profile = fleetnet-pembroke", "default_profile = p").replace("[profile:fleetnet-pembroke]", "[profile:p]").replace("profile = fleetnet-pembroke", "profile = p") + extra
    path = tmp_path / "uploader.conf"; path.write_text(text); return path


def test_boolean_and_talkgroups():
    assert parse_bool(" yes ") is True
    assert parse_bool("OFF") is False
    assert parse_talkgroups("8000-8999, 9056", "x").matches(9056)
    assert parse_talkgroups("*", "x").matches(123)
    assert not parse_talkgroups("8000-8999", "x").matches(9056)
    with pytest.raises(ValueError): parse_bool("maybe")
    with pytest.raises(ValueError): parse_talkgroups("9000-8000", "x")
    with pytest.raises(ValueError): parse_talkgroups("foo", "x")


def test_overlap_detection():
    assert rules_overlap(parse_talkgroups("8000-8999", "a"), parse_talkgroups("8500", "b"))
    assert not rules_overlap(parse_talkgroups("8000-8999", "a"), parse_talkgroups("9000", "b"))


def test_config_load_and_unknown_key(tmp_path):
    settings = load_config(write_config(tmp_path))
    assert settings.default_profile == "p"
    bad = write_config(tmp_path, "\n[profile:bad]\nenabled = maybe\n")
    with pytest.raises(ValueError): load_config(bad)


def test_enabled_placeholder_is_rejected(tmp_path):
    bad = write_config(tmp_path).read_text().replace("enabled = no\nprofile = p\nurl", "enabled = yes\nprofile = p\nurl", 1)
    path = tmp_path / "bad.conf"; path.write_text(bad)
    with pytest.raises(ValueError): load_config(path)
