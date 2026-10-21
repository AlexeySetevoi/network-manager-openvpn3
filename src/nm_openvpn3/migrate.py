# SPDX-License-Identifier: GPL-2.0-or-later
"""Moves the profile of older connections from vpn.data into the secrets.

Run as root on package upgrade: before 0.2.0 the profile, private key and
all, was connection data any local user could read.
"""

import sys

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib, NM  # noqa: E402

from . import connection as fmt  # noqa: E402
from . import ovpn  # noqa: E402


def needs_migration(s_vpn):
    return s_vpn is not None and s_vpn.get_service_type() == fmt.SERVICE_TYPE \
        and not fmt.profile_is_secret(s_vpn.get_data_item) \
        and s_vpn.get_data_item(fmt.KEY_PROFILE) is not None


def move_profile(s_vpn):
    """Same layout the import writes: system-owned secret plus the remote."""
    text = fmt.setting_profile(s_vpn)
    s_vpn.remove_data_item(fmt.KEY_PROFILE)
    s_vpn.add_secret(fmt.KEY_PROFILE, fmt.encode_profile(text))
    s_vpn.add_data_item(fmt.KEY_PROFILE_FLAGS, str(int(NM.SettingSecretFlags.NONE)))
    remote = ovpn.first_remote(text)
    if remote:
        s_vpn.add_data_item(fmt.KEY_REMOTE, remote[0])


def migrate(connections, fetch_secrets, save):
    """Returns the ids of the connections moved.

    @fetch_secrets adds a connection's stored secrets to it, so that saving
    does not drop them; @save writes it back.
    """
    moved = []
    for con in connections:
        if not needs_migration(con.get_setting_vpn()):
            continue
        fetch_secrets(con)
        move_profile(con.get_setting_vpn())
        save(con)
        moved.append(con.get_id())
    return moved


def _fetch_secrets(con):
    secrets = con.get_secrets(NM.SETTING_VPN_SETTING_NAME, None)
    con.update_secrets(NM.SETTING_VPN_SETTING_NAME, secrets)


def _save(con):
    loop = GLib.MainLoop()
    errors = []

    def done(c, res):
        try:
            c.update2_finish(res)
        except GLib.Error as e:
            errors.append(e)
        loop.quit()

    con.update2(con.to_dbus(NM.ConnectionSerializationFlags.ALL), NM.SettingsUpdate2Flags.NONE,
                None, None, done)
    loop.run()
    if errors:
        raise errors[0]


def main():
    client = NM.Client.new(None)
    for name in migrate(client.get_connections(), _fetch_secrets, _save):
        print(f"nm-openvpn3: moved the OpenVPN profile of '{name}' into the connection secrets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
