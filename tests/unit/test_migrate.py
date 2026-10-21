# SPDX-License-Identifier: GPL-2.0-or-later
import os

import nmjson
from nm_openvpn3 import connection as fmt
from nm_openvpn3 import migrate

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "connections")


def fixture(name):
    return nmjson.read(os.path.join(FIXTURES, f"{name}.json"))


def test_legacy_connection_is_moved_with_its_secrets():
    con = fixture("legacy-userpass")
    profile = fmt.setting_profile(con.get_setting_vpn())
    con.get_setting_vpn().remove_secret("password")
    saved = []

    def fetch(c):
        # Stands for NetworkManager handing out the stored secrets.
        c.get_setting_vpn().add_secret("password", "s3cret")

    moved = migrate.migrate([con, fixture("system-cert")], fetch, saved.append)
    assert moved == ["legacy-userpass"]
    s = saved[0].get_setting_vpn()
    assert s.get_secret("password") == "s3cret"
    assert fmt.setting_profile(s) == profile
    assert s.get_data_item("x-conformance") == "keep"
    assert fmt.validate(s) == []


def test_other_vpn_types_are_left_alone():
    con = fixture("legacy-userpass")
    con.get_setting_vpn().set_property("service-type", "org.freedesktop.NetworkManager.openvpn")
    assert migrate.migrate([con], lambda c: None, lambda c: None) == []
