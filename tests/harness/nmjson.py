# SPDX-License-Identifier: GPL-2.0-or-later
"""Connections as the JSON the conformance suite exchanges with a front-end.

    {"id": "office",
     "vpn": {"service-type": "org.freedesktop.NetworkManager.openvpn3",
             "data": {"username": "...", ...}, "secrets": {"profile": "...", ...}}}

That is the VPN setting a front-end hands to NetworkManager, including secrets
an agent would keep; see docs/frontends.md.
"""

import json
import os

import gi

gi.require_version("NM", "1.0")
from gi.repository import NM  # noqa: E402

SERVICE = "org.freedesktop.NetworkManager.openvpn3"
PLUGIN = "libnm-vpn-plugin-openvpn3.so"


def to_dict(con):
    s_vpn = con.get_setting_vpn()
    return {"id": con.get_id(),
            "vpn": {"service-type": s_vpn.get_service_type(),
                    "data": {k: s_vpn.get_data_item(k) for k in s_vpn.get_data_keys() or []},
                    "secrets": {k: s_vpn.get_secret(k) for k in s_vpn.get_secret_keys() or []}}}


def from_dict(d):
    con = NM.SimpleConnection.new()
    s_con = NM.SettingConnection.new()
    s_con.set_property(NM.SETTING_CONNECTION_ID, d.get("id") or "conformance")
    s_con.set_property(NM.SETTING_CONNECTION_UUID, NM.utils_uuid_generate())
    s_con.set_property(NM.SETTING_CONNECTION_TYPE, NM.SETTING_VPN_SETTING_NAME)
    con.add_setting(s_con)
    s_vpn = NM.SettingVpn.new()
    s_vpn.set_property(NM.SETTING_VPN_SERVICE_TYPE, d["vpn"]["service-type"])
    for k, v in d["vpn"].get("data", {}).items():
        s_vpn.add_data_item(k, v)
    for k, v in d["vpn"].get("secrets", {}).items():
        s_vpn.add_secret(k, v)
    con.add_setting(s_vpn)
    return con


def read(path):
    with open(path) as f:
        return from_dict(json.load(f))


def write(con, path):
    with open(path, "w") as f:
        json.dump(to_dict(con), f, indent=2, sort_keys=True)
        f.write("\n")


def load_plugin():
    """The libnm plugin: the build tree (OPENVPN3_PLUGIN_DIR) or the installed one.

    In a build tree the GTK editors live in OPENVPN3_EDITOR_DIR; the plugin
    looks for them next to itself, so both are linked into one directory.
    """
    plugin_dir = os.environ.get("OPENVPN3_PLUGIN_DIR")
    if not plugin_dir:
        info = NM.VpnPluginInfo.list_find_by_service(NM.VpnPluginInfo.list_load(), SERVICE)
        if info is None:
            raise RuntimeError("the openvpn3 libnm plugin is neither built (OPENVPN3_PLUGIN_DIR) nor installed")
        return info.load_editor_plugin()
    editor_dir = os.environ.get("OPENVPN3_EDITOR_DIR")
    if editor_dir:
        plugin_dir = _linked(plugin_dir, editor_dir)
    return NM.VpnEditorPlugin.load(os.path.join(plugin_dir, PLUGIN), SERVICE)


def _linked(*dirs):
    import tempfile
    out = tempfile.mkdtemp(prefix="nm-openvpn3-plugins-")
    for d in dirs:
        for name in os.listdir(d):
            if name.endswith(".so"):
                os.symlink(os.path.abspath(os.path.join(d, name)), os.path.join(out, name))
    return out
