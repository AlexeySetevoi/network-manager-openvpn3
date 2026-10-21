# SPDX-License-Identifier: GPL-2.0-or-later
"""Import/export through the built libnm plugin, the way nmcli uses it."""

import base64
import json
import os
import stat

import gi
import pytest

gi.require_version("NM", "1.0")
from gi.repository import GLib, NM  # noqa: E402

import nmjson  # noqa: E402
from nm_openvpn3 import connection  # noqa: E402

PLUGIN_DIR = os.environ.get("OPENVPN3_PLUGIN_DIR")
pytestmark = pytest.mark.skipif(not PLUGIN_DIR, reason="needs the built plugin (meson test)")

SERVICE = "org.freedesktop.NetworkManager.openvpn3"
PEM = "-----BEGIN CERTIFICATE-----\nMIIBsyntheticTESTDATA\n-----END CERTIFICATE-----\n"


@pytest.fixture(scope="module")
def plugin():
    path = os.path.join(PLUGIN_DIR, "libnm-vpn-plugin-openvpn3.so")
    return NM.VpnEditorPlugin.load(path, SERVICE)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def vpn(con):
    s = con.get_setting_vpn()
    return s, base64.b64decode(s.get_secret("profile")).decode()


def test_plugin_properties(plugin):
    assert plugin.props.service == SERVICE
    caps = plugin.get_capabilities()
    assert caps & NM.VpnEditorPluginCapability.IMPORT
    assert caps & NM.VpnEditorPluginCapability.EXPORT


def test_inline_credentials_move_into_connection(plugin, tmp_path):
    path = write(tmp_path, "office.ovpn",
                 "client\nremote vpn.example.net 1194\n"
                 "<auth-user-pass>\ntestuser\ns3cret\n</auth-user-pass>\n"
                 f"<ca>\n{PEM}</ca>\n")
    con = plugin.import_(path)
    s, config = vpn(con)
    assert con.get_id() == "office"
    assert s.get_service_type() == SERVICE
    assert s.get_data_item("username") == "testuser"
    assert s.get_secret("password") == "s3cret"
    assert s.get_data_item("password-flags") == "0"
    assert "s3cret" not in config and "testuser" not in config
    assert "auth-user-pass\n" in config
    assert f"<ca>\n{PEM}</ca>\n" in config


def test_credentials_block_followed_by_other_block(plugin, tmp_path):
    path = write(tmp_path, "a.ovpn",
                 "client\nremote 192.0.2.1\n<auth-user-pass>\ntestuser\npw\n</auth-user-pass>\n"
                 f"<ca>\n{PEM}</ca>\n")
    s, config = vpn(plugin.import_(path))
    assert s.get_data_item("username") == "testuser"
    assert s.get_secret("password") == "pw"
    assert "<ca>" in config


def test_referenced_files_are_inlined(plugin, tmp_path):
    (tmp_path / "keys").mkdir()
    write(tmp_path, "keys/ca.crt", PEM)
    write(tmp_path, "keys/ta.key", "-----BEGIN OpenVPN Static key V1-----\n00ff\n-----END OpenVPN Static key V1-----\n")
    write(tmp_path, "creds.txt", "testuser\npw-from-file\n")
    path = write(tmp_path, "files.ovpn",
                 "client\nremote vpn.example.net 443 tcp\nca keys/ca.crt\n"
                 "tls-auth \"keys/ta.key\" 1\nauth-user-pass creds.txt\n")
    s, config = vpn(plugin.import_(path))
    assert "ca keys/ca.crt" not in config
    assert f"<ca>\n{PEM}</ca>\n" in config
    assert "<tls-auth>\n-----BEGIN OpenVPN Static key V1-----" in config
    assert "key-direction 1\n" in config
    assert s.get_data_item("username") == "testuser"
    assert s.get_secret("password") == "pw-from-file"
    assert "creds.txt" not in config


def test_prompted_password_is_agent_owned(plugin, tmp_path):
    path = write(tmp_path, "p.ovpn", "client\nremote vpn.example.net\nauth-user-pass\n")
    s, _ = vpn(plugin.import_(path))
    assert s.get_secret("password") is None
    assert s.get_data_item("password-flags") == str(int(NM.SettingSecretFlags.AGENT_OWNED))
    assert s.get_data_item("challenge-response-flags") == str(int(NM.SettingSecretFlags.NOT_SAVED))


def test_certificate_only_profile_has_no_password(plugin, tmp_path):
    path = write(tmp_path, "c.ovpn", f"client\nremote vpn.example.net\n<ca>\n{PEM}</ca>\n")
    s, config = vpn(plugin.import_(path))
    assert "auth-user-pass" not in config
    assert s.get_data_item("username") is None


def test_commented_directive_is_not_credentials(plugin, tmp_path):
    path = write(tmp_path, "k.ovpn", "client\nremote vpn.example.net\n#auth-user-pass\n")
    s, config = vpn(plugin.import_(path))
    assert "\nauth-user-pass\n" not in config
    assert s.get_data_item("password-flags") is None


@pytest.mark.parametrize("text,msg", [
    ("client\nremote vpn.example.net\npkcs12 missing.p12\n", "Cannot read file"),
    ("client\nremote vpn.example.net\n<ca>\nabc\n", "Unterminated"),
    ("dev tun\nremote vpn.example.net\n", "Not an OpenVPN client profile"),
    ("client\nremote vpn.example.net\nca missing.crt\n", "Cannot read file"),
])
def test_invalid_profiles(plugin, tmp_path, text, msg):
    path = write(tmp_path, "bad.ovpn", text)
    with pytest.raises(GLib.Error, match=msg):
        plugin.import_(path)


def test_wrong_extension_is_rejected(plugin, tmp_path):
    path = write(tmp_path, "profile.txt", "client\nremote vpn.example.net\n")
    with pytest.raises(GLib.Error):
        plugin.import_(path)


def test_export_omits_credentials(plugin, tmp_path):
    src = write(tmp_path, "e.ovpn",
                "client\nremote vpn.example.net\n<auth-user-pass>\ntestuser\npw\n</auth-user-pass>\n")
    con = plugin.import_(src)
    out = str(tmp_path / "out.ovpn")
    assert plugin.export(out, con)
    text = open(out).read()
    assert "pw" not in text.split("remote vpn.example.net")[1]
    assert "auth-user-pass\n" in text
    assert plugin.get_suggested_filename(con) == "e.ovpn"
    # Round trip keeps the profile.
    again = plugin.import_(out)
    assert vpn(again)[1] == vpn(con)[1]


def test_stored_values_are_single_line(plugin, tmp_path):
    # Some settings backends (netplan) mangle multi-line values.
    path = write(tmp_path, "m.ovpn", f"client\nremote vpn.example.net\n<ca>\n{PEM}</ca>\n")
    s = plugin.import_(path).get_setting_vpn()
    for key in s.get_data_keys():
        assert "\n" not in s.get_data_item(key)


def test_pkcs12_file_is_embedded(plugin, tmp_path):
    from pkihelp import p12
    data, _, _ = p12()
    (tmp_path / "client.p12").write_bytes(data)
    path = write(tmp_path, "p.ovpn", "client\nremote vpn.example.net\npkcs12 client.p12\n")
    s, config = vpn(plugin.import_(path))
    assert "pkcs12 client.p12" not in config
    b64 = config.split("<pkcs12>\n")[1].split("</pkcs12>")[0]
    assert base64.b64decode("".join(b64.split())) == data
    assert s.get_data_item("cert-pass-flags") == str(int(NM.SettingSecretFlags.AGENT_OWNED))


def test_encrypted_key_needs_passphrase(plugin, tmp_path):
    from pkihelp import pem_key
    write(tmp_path, "client.key", pem_key(b"keypass"))
    path = write(tmp_path, "k.ovpn", f"client\nremote vpn.example.net\n<ca>\n{PEM}</ca>\ncert c.crt\nkey client.key\n")
    write(tmp_path, "c.crt", PEM)
    s, config = vpn(plugin.import_(path))
    assert "ENCRYPTED PRIVATE KEY" in config
    assert s.get_data_item("cert-pass-flags") == str(int(NM.SettingSecretFlags.AGENT_OWNED))


def test_plain_key_needs_no_passphrase(plugin, tmp_path):
    from pkihelp import pem_key
    path = write(tmp_path, "n.ovpn", f"client\nremote vpn.example.net\n<key>\n{pem_key()}</key>\n")
    s, _ = vpn(plugin.import_(path))
    assert s.get_data_item("cert-pass-flags") is None


def test_profile_is_a_system_owned_secret(plugin, tmp_path):
    path = write(tmp_path, "s.ovpn", f"client\nremote vpn.example.net 1194\n<ca>\n{PEM}</ca>\n")
    s = plugin.import_(path).get_setting_vpn()
    assert s.get_data_item("profile") is None
    assert s.get_data_item("profile-flags") == "0"
    assert s.get_data_item("remote") == "vpn.example.net"
    assert connection.validate(s) == []


def test_remote_inside_connection_block(plugin, tmp_path):
    path = write(tmp_path, "b.ovpn", "client\n<connection>\nremote vpn1.example.net 1194\n</connection>\n"
                 "<connection>\nremote vpn2.example.net\nca not-inlined.crt\n</connection>\n")
    s, config = vpn(plugin.import_(path))
    assert s.get_data_item("remote") == "vpn1.example.net"
    assert "<connection>\nremote vpn1.example.net 1194\n</connection>\n" in config
    assert "ca not-inlined.crt" in config


def test_unterminated_connection_block(plugin, tmp_path):
    path = write(tmp_path, "u.ovpn", "client\n<connection>\nremote vpn.example.net\n")
    with pytest.raises(GLib.Error, match="Unterminated <connection>"):
        plugin.import_(path)


def test_export_is_private(plugin, tmp_path):
    con = plugin.import_(write(tmp_path, "x.ovpn", "client\nremote vpn.example.net\n"))
    out = tmp_path / "out.ovpn"
    out.write_text("old")
    out.chmod(0o644)
    assert plugin.export(str(out), con)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_export_without_secret_profile_explains(plugin, tmp_path):
    con = plugin.import_(write(tmp_path, "y.ovpn", "client\nremote vpn.example.net\n"))
    con.get_setting_vpn().remove_secret("profile")
    with pytest.raises(GLib.Error, match="stored as a secret"):
        plugin.export(str(tmp_path / "y-out.ovpn"), con)


EXPECTED = os.path.join(os.path.dirname(__file__), "..", "conformance", "expected")
PROFILES = os.path.join(os.path.dirname(__file__), "..", "conformance", "profiles")


@pytest.mark.parametrize("name", sorted(f[:-5] for f in os.listdir(PROFILES) if f.endswith(".ovpn")))
def test_import_matches_documented_connection(plugin, name):
    """The core side of the front-end boundary: changing what import writes
    changes these files and docs/connection-format.md with them."""
    got = nmjson.to_dict(plugin.import_(os.path.join(PROFILES, f"{name}.ovpn")))["vpn"]
    # Readable in a diff: the profile as lines instead of base64.
    got["secrets"]["profile"] = base64.b64decode(got["secrets"]["profile"]).decode().splitlines()
    path = os.path.join(EXPECTED, f"{name}.json")
    if os.environ.get("OPENVPN3_UPDATE_EXPECTED"):
        with open(path, "w") as f:
            json.dump(got, f, indent=2, sort_keys=True)
            f.write("\n")
    with open(path) as f:
        assert got == json.load(f)
