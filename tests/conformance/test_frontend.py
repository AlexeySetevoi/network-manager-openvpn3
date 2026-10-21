# SPDX-License-Identifier: GPL-2.0-or-later
"""What a front-end hands to the core: docs/frontends.md, "Conformance".

Every check is on the connection the front-end saved, never on its UI.
"""

import pytest

import nmjson
from conftest import ANSWERS, FIXTURES, PROFILES, fixture
from fake_openvpn3 import activate
from nm_openvpn3 import connection as fmt
from nm_openvpn3 import migrate, pki

STORAGE = {"system": 0, "agent": 1, "ask": 2, "none": 4}


def vpn(con):
    return nmjson.to_dict(con)["vpn"]


def conforms(con):
    problems = fmt.validate(con.get_setting_vpn())
    assert problems == [], "the connection does not follow docs/connection-format.md"


def connects(con, profile_text=None):
    client, plugin, asked = activate(con, ANSWERS)
    assert ("config" in [e[0] for e in plugin.events]), f"did not connect: {plugin.events}"
    (_, imported), = client.imported
    stored = fmt.setting_profile(con.get_setting_vpn())
    if pki.has_pkcs12(stored):
        assert "<pkcs12>" not in imported and "PRIVATE KEY" in imported
    else:
        assert imported == stored
    return asked


def test_capabilities(caps):
    assert isinstance(caps.get("name"), str)
    assert set(caps.get("secret-storage", [])) <= set(STORAGE)
    assert isinstance(caps.get("secrets", False), bool)


# -- loading a profile ------------------------------------------------------

@pytest.mark.parametrize("name", PROFILES)
def test_load_gives_the_core_connection(loaded, core, profiles, name):
    """No second parser: the same connection nmcli or GNOME would import."""
    assert vpn(loaded(name)) == vpn(core.import_(profiles[name]))


@pytest.mark.parametrize("name", PROFILES)
def test_loaded_connection_conforms(loaded, name):
    conforms(loaded(name))


@pytest.mark.parametrize("name", PROFILES)
def test_loaded_connection_connects(driver, loaded, name):
    con = loaded(name)
    s_vpn = con.get_setting_vpn()
    if fmt.needs_user_pass(s_vpn.get_data_item) and not s_vpn.get_data_item(fmt.KEY_USERNAME):
        con = driver.edit(con, {"username": "testuser"})
    asked = connects(con)
    if name == "static-challenge":
        assert ["challenge-response", "x-challenge-echo"] in asked, "the one-time code was not asked for"


def test_profile_is_a_system_secret_by_default(loaded):
    s_vpn = loaded("cert").get_setting_vpn()
    assert s_vpn.get_data_item(fmt.KEY_PROFILE) is None, "the profile must not be in vpn.data"
    assert s_vpn.get_data_item(fmt.KEY_PROFILE_FLAGS) == "0"


# -- editing a connection ---------------------------------------------------

def expected_after(con, changes):
    """The input as the core expects it back: migrated, with @changes applied."""
    s_vpn = nmjson.from_dict(nmjson.to_dict(con)).get_setting_vpn()
    if migrate.needs_migration(s_vpn):
        migrate.move_profile(s_vpn)
    if "username" in changes:
        if changes["username"]:
            s_vpn.add_data_item(fmt.KEY_USERNAME, changes["username"])
        else:
            s_vpn.remove_data_item(fmt.KEY_USERNAME)
    for key in (fmt.KEY_PASSWORD, fmt.KEY_CERT_PASS):
        change = changes.get(key)
        if change is None:
            continue
        if "storage" in change:
            s_vpn.add_data_item(fmt.flags_key(key), str(STORAGE[change["storage"]]))
        flags = int(s_vpn.get_data_item(fmt.flags_key(key)) or 0)
        value = change.get("value")
        if flags in (0, 1) and value:
            s_vpn.add_secret(key, value)
        elif "storage" in change or value is not None:
            s_vpn.remove_secret(key)
    return {"service-type": s_vpn.get_service_type(),
            "data": {k: s_vpn.get_data_item(k) for k in s_vpn.get_data_keys()},
            "secrets": {k: s_vpn.get_secret(k) for k in s_vpn.get_secret_keys() or []}}


@pytest.mark.parametrize("name", FIXTURES)
def test_saving_unchanged_keeps_everything(driver, name):
    """Edit in place: keys the front-end does not know stay, old layouts move."""
    con = fixture(name)
    out = driver.edit(con, {})
    assert vpn(out) == expected_after(con, {})
    conforms(out)


@pytest.mark.parametrize("name", FIXTURES)
def test_edited_connection_connects(driver, name):
    con = fixture(name)
    connects(driver.edit(con, {}))


def test_username_change(driver):
    con = fixture("system-userpass")
    out = driver.edit(con, {"username": "otheruser"})
    assert vpn(out) == expected_after(con, {"username": "otheruser"})


def test_password_change(driver):
    con = fixture("system-userpass")
    changes = {"password": {"value": "n3w"}}
    out = driver.edit(con, changes)
    assert vpn(out) == expected_after(con, changes)
    conforms(out)


@pytest.mark.parametrize("storage", list(STORAGE))
def test_password_storage(driver, caps, storage):
    if storage not in caps.get("secret-storage", []):
        pytest.skip(f"the front-end does not offer '{storage}' storage")
    con = fixture("system-userpass")
    changes = {"password": {"value": "n3w", "storage": storage}}
    out = driver.edit(con, changes)
    assert vpn(out) == expected_after(con, changes)
    conforms(out)
    if storage in ("system", "agent", "ask"):
        connects(out)


def test_agent_owned_profile_stays_with_the_agent(driver, caps):
    if not caps.get("agent-profile", False):
        pytest.skip("the front-end does not keep agent-owned profiles")
    con = fixture("agent-profile")
    out = driver.edit(con, {})
    assert out.get_setting_vpn().get_data_item(fmt.KEY_PROFILE_FLAGS) == "1"


# -- secrets agent (optional) -----------------------------------------------

@pytest.mark.parametrize("hints", [["password"], ["challenge-response", "x-challenge-echo"], ["cert-pass"]])
def test_agent_answers_what_the_service_asks(driver, caps, hints):
    if not caps.get("secrets", False):
        pytest.skip("the front-end has no secrets agent of its own (libnma agents use the auth dialog)")
    con = fixture("system-userpass")
    answer = driver.secrets(con, {"hints": hints, "message": "Authentication required",
                                  "answers": ANSWERS})
    asked = [h for h in hints if not h.startswith("x-")]
    assert set(answer) - {fmt.KEY_PROFILE} == set(asked)
    assert all(answer[k] == ANSWERS[k] for k in asked)
