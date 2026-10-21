# SPDX-License-Identifier: GPL-2.0-or-later
"""NetworkManager VPN service plugin driving OpenVPN 3 Linux sessions."""

import logging
import re
import urllib.parse

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib, NM  # noqa: E402

from . import connection as fmt  # noqa: E402
from . import desktop, ipconfig, ovpn, pki  # noqa: E402
from . import openvpn3 as ov3  # noqa: E402
from .vpnplugin import PluginError, VpnPlugin  # noqa: E402

log = logging.getLogger("nm-openvpn3")

SERVICE_NAME = "org.freedesktop.NetworkManager.openvpn3"

KEY_PROFILE = fmt.KEY_PROFILE
KEY_PROFILE_FLAGS = fmt.KEY_PROFILE_FLAGS
KEY_USERNAME = fmt.KEY_USERNAME
KEY_PASSWORD = fmt.KEY_PASSWORD
KEY_CHALLENGE = fmt.KEY_CHALLENGE
KEY_CERT_PASS = fmt.KEY_CERT_PASS

HINT_CHALLENGE_ECHO = "x-challenge-echo"

# openvpn3 reports client events in its log as "[NAME] info".
_EVENT = re.compile(r"^\[([A-Z_]+)\]\s*(.*)$")
_EVENT_TEXT = {
    "SESSION_EXPIRED": "The server ended the session (session expired)",
    "AUTH_FAILED": "Authentication failed",
    "CONNECTION_TIMEOUT": "The server did not answer",
}
_URL = re.compile(r"https?://\S+")


def get_profile(s_vpn):
    try:
        return fmt.setting_profile(s_vpn)
    except fmt.FormatError as e:
        raise PluginError("BadArguments", str(e))


def _secret_flags(s_vpn, key):
    try:
        return fmt.secret_flags(s_vpn.get_data_item, key)
    except fmt.FormatError as e:
        raise PluginError("BadArguments", str(e))


def _url_host(url):
    return urllib.parse.urlsplit(url).hostname or "?"


def _redact(text):
    """Login URLs carry one-time tokens: keep only the host."""
    return _URL.sub(lambda m: _url_host(m.group(0)), text)


class Tunnel:
    """One openvpn3 session backing one NetworkManager activation."""

    def __init__(self, plugin, client, connection, interactive):
        self.plugin = plugin
        self.client = client
        self.interactive = interactive
        self.config_path = None
        self.session = None
        self.connect_started = False
        self.connected_once = False
        self.stopping = False
        self.failed = False
        self.ended = False
        self._last_status = None
        self._last_log = None
        self._last_event = None
        self._sub_ids = []
        self.name = connection.get_id()
        # NewSecrets may come without the profile; it does not change anyway.
        self.config = get_profile(connection.get_setting_vpn())
        self.update_connection(connection)

    def update_connection(self, connection):
        s_vpn = connection.get_setting_vpn()
        self.username = s_vpn.get_data_item(KEY_USERNAME)
        self.password = s_vpn.get_secret(KEY_PASSWORD)
        self.challenge = s_vpn.get_secret(KEY_CHALLENGE)
        self.cert_pass = s_vpn.get_secret(KEY_CERT_PASS)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        try:
            profile = self._profile_for_openvpn3()
        except pki.WrongPassphrase:
            if not self.interactive:
                raise PluginError("BadArguments", "Wrong or missing passphrase for the PKCS#12 bundle")
            # Ask once Connect has returned; the session starts in new_secrets().
            GLib.idle_add(self._ask_cert_pass)
            return
        except pki.InvalidBundle as e:
            raise PluginError("BadArguments", str(e))
        self._start_session(profile)

    def _ask_cert_pass(self):
        self._ask([KEY_CERT_PASS], "Private key passphrase")
        return GLib.SOURCE_REMOVE

    def _profile_for_openvpn3(self):
        if pki.has_pkcs12(self.config):
            return pki.expand_pkcs12(self.config, self.cert_pass)
        return self.config

    def _start_session(self, profile):
        self.config_path = self.client.import_config(self.name, profile)
        # Subscribe before NewTunnel: early status changes must not be lost.
        # StatusChange reaches us through the log service once LogForward is
        # enabled; AttentionRequired is broadcast by the session manager.
        bus = self.client.bus
        for sender, iface, sig in ((ov3.LOG_SVC, ov3.BACKENDS_IF, "StatusChange"),
                                   (ov3.LOG_SVC, ov3.BACKENDS_IF, "Log"),
                                   (ov3.SESSIONS_SVC, ov3.SESSIONS_SVC, "StatusChange"),
                                   (ov3.SESSIONS_SVC, ov3.SESSIONS_SVC, "AttentionRequired")):
            self._sub_ids.append(bus.signal_subscribe(sender, iface, sig, None, None,
                                                      0, self._on_signal))
        self.session = self.client.new_tunnel(self.config_path)
        log.info("session %s created", self.session.path)
        self.session.log_forward(True)
        major, minor, msg = self.session.prop("status")
        self._on_status(major, minor, msg)

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        if not self.failed and not self.connected_once:
            log.info("stopped before connecting, %s", self._last_words())
        if self.session is not None and not self.ended:
            try:
                self.session.disconnect()
            except GLib.Error as e:
                # The session is already gone when the backend exited on its own.
                log.info("disconnect: %s", e.message)
        self._cleanup()

    def _cleanup(self):
        for sid in self._sub_ids:
            self.client.bus.signal_unsubscribe(sid)
        self._sub_ids.clear()

    # -- openvpn3 events ---------------------------------------------------

    def _on_signal(self, _conn, _sender, path, _iface, signal, params):
        if self.session is None or path != self.session.path:
            return
        try:
            if signal == "StatusChange":
                self._on_status(*params.unpack())
            elif signal == "Log":
                self._on_log(params.unpack()[-1])
            elif signal == "AttentionRequired":
                t, g, _msg = params.unpack()
                log.info("attention required: type=%d group=%d", t, g)
        except Exception as e:
            # A GLib callback cannot propagate: fail the activation so the
            # user sees it instead of a connection stuck in "connecting".
            log.exception("handling %s failed", signal)
            self._fail(NM.VpnPluginFailure.CONNECT_FAILED, f"Internal error: {e}")

    def _on_log(self, message):
        # openvpn3-service-log already writes these to the journal; keep the
        # last ones to say why a connection ended.
        message = message.strip()
        if not message:
            return
        self._last_log = message
        if _EVENT.match(message):
            self._last_event = message

    def _last_words(self):
        status = "status %d/%d" % self._last_status[:2] if self._last_status else "no status"
        if self._last_log:
            return f"{status}, last openvpn3 message: {_redact(self._last_log)}"
        return status

    def _reason(self, msg, default):
        """Why openvpn3 gave up: its status message or the last client event."""
        if msg:
            return msg
        if self._last_event:
            name, info = _EVENT.match(self._last_event).groups()
            text = _EVENT_TEXT.get(name, name)
            return f"{text}: {_redact(info)}" if info else text
        return default

    def _on_status(self, major, minor, msg):
        # The same change can arrive twice: once from the session manager
        # and once forwarded by the log service.
        if (major, minor, msg) == self._last_status:
            return
        self._last_status = (major, minor, msg)
        is_url = major == ov3.MAJOR_SESSION and minor == ov3.SESS_AUTH_URL
        # The login URL carries a one-time token: keep it out of the journal.
        log.info("status %d/%d %s", major, minor, _url_host(msg) if is_url else msg)
        if (major == ov3.MAJOR_CONNECTION and minor in (ov3.CONN_DISCONNECTED, ov3.CONN_DONE)) \
                or (major == ov3.MAJOR_PROCESS and minor in (ov3.PROC_STOPPED, ov3.PROC_KILLED)):
            # The backend is gone, there is nothing left to disconnect.
            self.ended = True
        if self.stopping:
            return
        if is_url:
            if self.connected_once:
                log.info("the server asks to log in again")
            self._open_login_url(msg)
        elif minor == ov3.CFG_OK and not self.connect_started:
            self._ready_and_connect()
        elif minor == ov3.CFG_REQUIRE_USER:
            self._provide_inputs()
        elif major == ov3.MAJOR_CONNECTION and minor == ov3.CONN_CONNECTED:
            self._report_connected()
        elif major == ov3.MAJOR_CONNECTION and minor == ov3.CONN_AUTH_FAILED:
            self._fail(NM.VpnPluginFailure.LOGIN_FAILED, self._reason(msg, "Authentication failed"))
        elif (major == ov3.MAJOR_CONNECTION
              and minor in (ov3.CONN_FAILED, ov3.CONN_DISCONNECTED, ov3.CONN_DONE)) \
                or (major == ov3.MAJOR_PROCESS and minor in (ov3.PROC_STOPPED, ov3.PROC_KILLED)) \
                or major == ov3.MAJOR_CFG_ERROR:
            self._fail(NM.VpnPluginFailure.CONNECT_FAILED, self._reason(msg, "Connection failed"))

    def _open_login_url(self, url):
        """Web based login: open the server's URL in the desktop user's browser."""
        if urllib.parse.urlsplit(url).scheme != "https":
            self._fail(NM.VpnPluginFailure.LOGIN_FAILED, "The server asked to open a non-https login URL")
            return
        try:
            user = desktop.open_url(url)
        except desktop.NoDesktopUser as e:
            self._fail(NM.VpnPluginFailure.LOGIN_FAILED, f"Web based login needs a browser: {e}")
            return
        log.info("opened the login page on %s for %s, waiting for the server", _url_host(url), user)

    def _ready_and_connect(self):
        try:
            self.session.ready()
        except GLib.Error as e:
            remote = e.message or ""
            if "Missing user credentials" in remote:
                self._provide_inputs()
                return
            if "not ready" in remote:
                # Backend still starting: CFG_OK or CFG_REQUIRE_USER follows.
                return
            raise
        self.connect_started = True
        log.info("backend ready, connecting")
        self.session.connect()

    def _provide_inputs(self):
        slots = self.session.pending_inputs()
        if not slots:
            # Nothing queued yet; openvpn3 announces it with CFG_REQUIRE_USER.
            return
        log.info("backend asks for %s", ", ".join(s[3] for s in slots))
        for t, g, i, name, description, hidden in slots:
            if t != ov3.ATTN_CREDENTIALS:
                self._fail(NM.VpnPluginFailure.LOGIN_FAILED,
                           f"Unsupported authentication request: {description}")
                return
            value = self._value_for(g, name, description, hidden)
            if value is None:
                return  # waiting for NetworkManager secrets
            self.session.provide(t, g, i, value)
        self.connect_started = False
        self._ready_and_connect()

    def _value_for(self, group, name, description, hidden):
        if group == ov3.GRP_USER_PASSWORD and name == "username":
            if self.username:
                return self.username
            self._fail(NM.VpnPluginFailure.LOGIN_FAILED, "No username configured")
            return None
        if group == ov3.GRP_USER_PASSWORD and name == "password":
            if self.password:
                return self.password
            return self._ask([KEY_PASSWORD], description)
        if group == ov3.GRP_PK_PASSPHRASE:
            if self.cert_pass:
                return self.cert_pass
            return self._ask([KEY_CERT_PASS], description)
        if group in (ov3.GRP_CHALLENGE_STATIC, ov3.GRP_CHALLENGE_DYNAMIC):
            if self.challenge:
                value, self.challenge = self.challenge, None
                return value
            hints = [KEY_CHALLENGE]
            if not hidden:
                hints.append(HINT_CHALLENGE_ECHO)
            return self._ask(hints, description)
        self._fail(NM.VpnPluginFailure.LOGIN_FAILED,
                   f"Unsupported authentication request: {description}")
        return None

    def _ask(self, hints, message):
        log.info("requesting secrets from NetworkManager: %s", ", ".join(hints))
        if not self.interactive:
            self._fail(NM.VpnPluginFailure.LOGIN_FAILED,
                       f"Interactive authentication required: {message}")
            return None
        # NetworkManager adds the message to the hints itself (x-vpn-message:).
        self.plugin.request_secrets(message, hints)
        return None

    def new_secrets(self, connection):
        self.update_connection(connection)
        if self.session is None:
            # Waiting for the PKCS#12 passphrase before any session exists.
            try:
                profile = self._profile_for_openvpn3()
            except pki.WrongPassphrase:
                self._fail(NM.VpnPluginFailure.LOGIN_FAILED, "Wrong passphrase for the PKCS#12 bundle")
                return
            self._start_session(profile)
            return
        self._provide_inputs()

    def _report_connected(self):
        device = self.session.prop("device_name")
        _proto, server, _port = self.session.prop("connected_to")
        dns, domains = self.client.netcfg_dns(device)
        if not dns and not ov3.netcfg_manages_dns():
            log.warning("openvpn3 does not manage DNS, pushed DNS servers are ignored; "
                        "enable it with: openvpn3-admin netcfg-service --config-set systemd-resolved 1")
        state = ipconfig.snapshot(device, dns, domains)
        log.info("connected on %s", device)
        self.plugin.set_config(ipconfig.general_config(state, server))
        if state.ip4.address:
            self.plugin.set_ip4_config(ipconfig.ip4_config(state))
        if state.ip6.address:
            self.plugin.set_ip6_config(ipconfig.ip6_config(state))
        self.connected_once = True

    def _fail(self, reason, message):
        log.warning("%s", message)
        if self.stopping:
            return
        self.failed = True
        # Both paths end in do_disconnect() -> stop(), which tears the session down.
        if self.connected_once:
            self.plugin.disconnect()
        else:
            self.plugin.failure(reason)


def _no_profile(s_vpn):
    if fmt.profile_is_secret(s_vpn.get_data_item):
        return "The OpenVPN profile is stored as a secret and was not provided"
    return "The connection has no OpenVPN profile"


class Plugin(VpnPlugin):
    def __init__(self, bus_name, loop, client_factory=ov3.Client, **kwargs):
        super().__init__(bus_name, loop, **kwargs)
        self.client_factory = client_factory
        self.tunnel = None

    def do_connect(self, connection, interactive):
        s_vpn = connection.get_setting_vpn()
        config = get_profile(s_vpn)
        if not config:
            raise PluginError("BadArguments", _no_profile(s_vpn))
        if ovpn.needs_user_pass(config) and not s_vpn.get_data_item(KEY_USERNAME):
            raise PluginError("BadArguments", "The profile requires a username")
        self.tunnel = Tunnel(self, self.client_factory(), connection, interactive)
        self.tunnel.start()

    def do_need_secrets(self, connection):
        s_vpn = connection.get_setting_vpn()
        config = get_profile(s_vpn)
        if config is None:
            if fmt.profile_flags(s_vpn.get_data_item) & NM.SettingSecretFlags.AGENT_OWNED:
                # The agent keeps it; what else is needed follows from it.
                return NM.SETTING_VPN_SETTING_NAME
            raise PluginError("BadArguments", _no_profile(s_vpn))

        def missing(key):
            return not s_vpn.get_secret(key) \
                and not _secret_flags(s_vpn, key) & NM.SettingSecretFlags.NOT_REQUIRED

        if ovpn.needs_user_pass(config) and missing(KEY_PASSWORD):
            return NM.SETTING_VPN_SETTING_NAME
        if missing(KEY_CERT_PASS):
            try:
                if pki.needs_passphrase(config):
                    return NM.SETTING_VPN_SETTING_NAME
            except pki.InvalidBundle as e:
                raise PluginError("BadArguments", str(e))
        return None

    def do_new_secrets(self, connection):
        self.tunnel.new_secrets(connection)

    def do_disconnect(self):
        if self.tunnel is not None:
            self.tunnel.stop()
            self.tunnel = None
