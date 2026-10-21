# SPDX-License-Identifier: GPL-2.0-or-later
"""The connection format (docs/connection-format.md) as the Python side reads it."""

import os

import pytest

import nmjson
from nm_openvpn3 import connection as fmt

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "connections")


def fixture(name):
    return nmjson.read(os.path.join(FIXTURES, f"{name}.json")).get_setting_vpn()


@pytest.mark.parametrize("name", ["system-userpass", "agent-password", "agent-profile", "system-cert"])
def test_fixtures_conform(name):
    assert fmt.validate(fixture(name)) == []
    assert "client" in fmt.setting_profile(fixture(name))


def test_legacy_layout_is_read_but_reported():
    s = fixture("legacy-userpass")
    assert "auth-user-pass" in fmt.setting_profile(s)
    assert fmt.validate(s) == ["profile is in vpn.data, not a secret (no profile-flags)", "no remote"]


def test_agent_profile_not_provided_is_fine_without_secrets():
    s = fixture("agent-profile")
    s.remove_secret("profile")
    assert fmt.setting_profile(s) is None
    assert fmt.validate(s, with_secrets=False) == []


def test_system_profile_missing_is_a_problem():
    s = fixture("system-cert")
    s.remove_secret("profile")
    assert fmt.validate(s) == ["no profile"]


def test_stale_data_copy_is_ignored_and_reported():
    s = fixture("system-cert")
    s.add_data_item("profile", fmt.encode_profile("stale"))
    assert "stale" not in fmt.setting_profile(s)
    assert "stale profile copy in vpn.data next to the secret" in fmt.validate(s)


@pytest.mark.parametrize("flags", ["2", "4", "x", "99"])
def test_bad_profile_flags(flags):
    s = fixture("system-cert")
    s.add_data_item("profile-flags", flags)
    with pytest.raises(fmt.FormatError):
        fmt.setting_profile(s)
    assert fmt.validate(s)


def test_unnormalized_profile_is_reported():
    s = fixture("system-cert")
    s.add_secret("profile", fmt.encode_profile(
        "client\nremote vpn.example.net\nca /etc/ca.crt\nauth-user-pass creds.txt\n"))
    problems = fmt.validate(s)
    assert "ca points to a file; files are inlined at import" in problems
    assert "the profile carries credentials; they belong in the connection" in problems
    assert any("password-flags" in p for p in problems)


def test_challenge_must_never_be_stored():
    s = fixture("system-userpass")
    s.add_data_item("challenge-response-flags", "0")
    assert any("challenge-response-flags" in p for p in fmt.validate(s))
