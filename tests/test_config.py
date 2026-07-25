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


def test_validation_reports_multiple_errors(tmp_path):
    bad = write_config(tmp_path).read_text()
    bad = bad.replace("retry_max_attempts = 8", "retry_max_attempts = nope").replace("retry_base_seconds = 30", "retry_base_seconds = 60").replace("retry_max_seconds = 3600", "retry_max_seconds = 10")
    bad += "\n[profile:broken]\nenabled = maybe\nunknown = value\n"
    path = tmp_path / "many-errors.conf"; path.write_text(bad)
    with pytest.raises(ValueError) as exc:
        load_config(path)
    message = str(exc.value)
    assert "retry_max_attempts" in message and "expected yes/no" in message and "unknown configuration key" in message


def test_exclusion_wins(tmp_path):
    config = write_config(tmp_path).read_text().replace("talkgroups = 8000-8999, 9056, 17344", "talkgroups = *", 1).replace("exclude_talkgroups =\n\n[rdio:fleetnet-opp-tac]", "exclude_talkgroups = 9056\n\n[rdio:fleetnet-opp-tac]", 1)
    path = tmp_path / "exclude.conf"; path.write_text(config)
    settings = load_config(path)
    route = next(d for d in settings.destinations if d.type == "rdio")
    assert route.matches(8000) and not route.matches(9056)


def test_migration_example_is_valid():
    path = Path(__file__).parents[1] / "config/migration-profiles.conf.example"
    settings = load_config(path)
    assert {"ems-paging", "renfrew-fire-paging", "fleetnet-kingston", "fleetnet-multi-site", "fleetnet-pembroke", "kingston-area-paging", "ottawa-renfrew-lanark-paging"} <= set(settings.profiles)
    assert {100, 101, 102, 103, 104, 105} <= {int(d.system_id) for d in settings.destinations if d.type == "rdio" and d.profile == "fleetnet-pembroke"}


def test_enabled_placeholder_is_rejected(tmp_path):
    bad = write_config(tmp_path).read_text().replace("enabled = no\nprofile = p\nurl", "enabled = yes\nprofile = p\nurl", 1)
    path = tmp_path / "bad.conf"; path.write_text(bad)
    with pytest.raises(ValueError): load_config(path)
