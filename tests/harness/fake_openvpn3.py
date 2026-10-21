# SPDX-License-Identifier: GPL-2.0-or-later
"""A fake openvpn3 and NetworkManager side for driving the service's Tunnel.

Shared by the unit tests and the front-end conformance suite.
"""

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib  # noqa: E402

from nm_openvpn3 import ipconfig, service  # noqa: E402
from nm_openvpn3 import openvpn3 as ov3  # noqa: E402

SESSION_PATH = "/net/openvpn/v3/sessions/test0"


class FakeBus:
    def __init__(self):
        self.handlers = {}

    def signal_subscribe(self, _svc, _iface, member, _path, _arg0, _flags, cb):
        sid = len(self.handlers) + 1
        self.handlers[sid] = (member, cb)
        return sid

    def signal_unsubscribe(self, sid):
        del self.handlers[sid]

    def emit(self, member, path, params):
        for m, cb in list(self.handlers.values()):
            if m == member:
                cb(None, None, path, None, member, params)


class FakeSession:
    def __init__(self, client):
        self.client = client
        self.path = SESSION_PATH
        self.calls = []
        self.inputs = []
        self.provided = {}
        self.need_creds = False

    def prop(self, name):
        return {"status": (ov3.MAJOR_SESSION, ov3.SESS_NEW, ""),
                "device_name": "tun7",
                "connected_to": ("tcp", "203.0.113.10", 1194)}[name]

    def log_forward(self, enable):
        pass

    def ready(self):
        self.calls.append("ready")
        if self.need_creds and len(self.provided) < len(self.inputs):
            raise GLib.Error.new_literal(GLib.quark_from_string("test"),
                                         "net.openvpn.v3.error.ready: Missing user credentials", 1)

    def connect(self):
        self.calls.append("connect")

    def disconnect(self):
        self.calls.append("disconnect")

    def pending_inputs(self):
        return [s for s in self.inputs if s[2] not in self.provided]

    def provide(self, t, g, i, value):
        self.provided[i] = value


class FakeClient:
    def __init__(self):
        self.bus = FakeBus()
        self.session = FakeSession(self)
        self.imported = []

    def import_config(self, name, text):
        self.imported.append((name, text))
        return "/net/openvpn/v3/configuration/cfg0"

    def new_tunnel(self, _path):
        return self.session

    def netcfg_dns(self, _device):
        return ["192.0.2.53"], ["corp.example.com"]


class FakePlugin:
    """Mirrors VpnPlugin: failure() and disconnect() end in the tunnel's stop()."""

    def __init__(self):
        self.events = []
        self.tunnel = None

    def set_config(self, v):
        self.events.append(("config", v.unpack()))

    def set_ip4_config(self, v):
        self.events.append(("ip4", v.unpack()))

    def set_ip6_config(self, v):
        self.events.append(("ip6", v.unpack()))

    def failure(self, reason):
        self.events.append(("failure", reason))
        self.tunnel.stop()

    def disconnect(self):
        self.events.append(("disconnect",))
        self.tunnel.stop()

    def request_secrets(self, message, hints):
        self.events.append(("secrets", message, hints))


def fake_snapshot(monkeypatch):
    """Tunnel addresses without a real tun device."""
    def snap(device, dns, domains):
        return ipconfig.TunnelState(
            device, 1400,
            ipconfig.FamilyConfig("198.51.100.6", 24, [], False, dns),
            ipconfig.FamilyConfig(), domains)
    monkeypatch.setattr(ipconfig, "snapshot", snap)


def start(con, interactive=False):
    client, plugin = FakeClient(), FakePlugin()
    t = service.Tunnel(plugin, client, con, interactive)
    plugin.tunnel = t
    t.start()
    return t, client, plugin


def status(client, major, minor, msg=""):
    client.bus.emit("StatusChange", SESSION_PATH, GLib.Variant("(uus)", (major, minor, msg)))


def log(client, message):
    client.bus.emit("Log", SESSION_PATH, GLib.Variant("(uuss)", (1, 4, "token", message)))


USERPASS = [(ov3.ATTN_CREDENTIALS, ov3.GRP_USER_PASSWORD, 0, "username", "Auth User name", False),
            (ov3.ATTN_CREDENTIALS, ov3.GRP_USER_PASSWORD, 1, "password", "Auth Password", True)]


def activate(con, answers):
    """Brings @con up the way NetworkManager would, against the fake openvpn3.

    @answers are what the user types when an agent asks: password, cert-pass,
    challenge-response. Returns (client, plugin, asked) where asked lists the
    hints of every secrets request made while connecting.
    """
    from gi.repository import NM
    from nm_openvpn3 import connection as fmt
    from nm_openvpn3 import ovpn

    s_vpn = con.get_setting_vpn()
    asked = []
    if service.Plugin.do_need_secrets(None, con):
        # The agent fills in what is needed and not stored.
        for key in (fmt.KEY_PASSWORD, fmt.KEY_CERT_PASS):
            flags = fmt.secret_flags(s_vpn.get_data_item, key)
            if s_vpn.get_data_item(fmt.flags_key(key)) is not None and not s_vpn.get_secret(key) \
                    and not flags & NM.SettingSecretFlags.NOT_REQUIRED:
                asked.append([key])
                s_vpn.add_secret(key, answers[key])
        assert service.Plugin.do_need_secrets(None, con) is None, "secrets still missing after the agent answered"
    t, client, plugin = start(con, interactive=True)
    profile = fmt.setting_profile(s_vpn)
    if ovpn.needs_user_pass(profile):
        client.session.inputs = list(USERPASS)
        if ovpn.static_challenge(profile):
            client.session.inputs.append((ov3.ATTN_CREDENTIALS, ov3.GRP_CHALLENGE_STATIC, 2,
                                          "static_challenge", "Enter OTP", False))
        client.session.need_creds = True
        status(client, ov3.MAJOR_CONNECTION, ov3.CFG_REQUIRE_USER)
        requests = [e for e in plugin.events if e[0] == "secrets"]
        for _, _message, hints in requests:
            asked.append(hints)
            answer = NM.SimpleConnection.new_clone(con)
            for key in hints:
                if key in answers:
                    answer.get_setting_vpn().add_secret(key, answers[key])
            t.new_secrets(answer)
    else:
        status(client, ov3.MAJOR_CONNECTION, ov3.CFG_OK)
    status(client, ov3.MAJOR_CONNECTION, ov3.CONN_CONNECTED)
    return client, plugin, asked
