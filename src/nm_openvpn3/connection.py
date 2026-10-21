# SPDX-License-Identifier: GPL-2.0-or-later
"""The connection format, see docs/connection-format.md.

The C side (libnm-plugin/ovpn-import.c) writes it, this module reads and
checks it; both are tested against tests/fixtures/connections.
"""

import base64
import binascii

import gi

gi.require_version("NM", "1.0")
from gi.repository import NM  # noqa: E402

from . import ovpn, pki  # noqa: E402

SERVICE_TYPE = "org.freedesktop.NetworkManager.openvpn3"

KEY_PROFILE = "profile"
KEY_PROFILE_FLAGS = "profile-flags"
KEY_REMOTE = "remote"
KEY_USERNAME = "username"
KEY_PASSWORD = "password"
KEY_CHALLENGE = "challenge-response"
KEY_CERT_PASS = "cert-pass"

SECRETS = (KEY_PROFILE, KEY_PASSWORD, KEY_CHALLENGE, KEY_CERT_PASS)
# A profile has to be there on every connect.
PROFILE_FLAGS = (NM.SettingSecretFlags.NONE, NM.SettingSecretFlags.AGENT_OWNED)
_ALL_FLAGS = int(NM.SettingSecretFlags.AGENT_OWNED | NM.SettingSecretFlags.NOT_SAVED
                 | NM.SettingSecretFlags.NOT_REQUIRED)


class FormatError(Exception):
    pass


def flags_key(key):
    return key + "-flags"


def secret_flags(get_data, key):
    """Flags of a VPN secret; NONE when not set, like NetworkManager reads them.

    NM.Setting.get_secret_flags() is not callable from Python (the out
    argument lacks an annotation); VPN secret flags live in the data items.
    """
    value = get_data(flags_key(key))
    if value is None:
        return NM.SettingSecretFlags.NONE
    try:
        number = int(value)
    except ValueError:
        number = -1
    if not 0 <= number <= _ALL_FLAGS:
        raise FormatError(f"Invalid {flags_key(key)} value")
    return NM.SettingSecretFlags(number)


def profile_is_secret(get_data):
    return get_data(KEY_PROFILE_FLAGS) is not None


def profile_flags(get_data):
    """Flags of the profile secret; NONE for an old data copy."""
    if not profile_is_secret(get_data):
        return NM.SettingSecretFlags.NONE
    flags = secret_flags(get_data, KEY_PROFILE)
    if flags not in PROFILE_FLAGS:
        raise FormatError(f"{KEY_PROFILE_FLAGS}={int(flags)} would never store the profile")
    return flags


def read_profile(get_data, get_secret):
    """Profile text, or None when the connection has none or it was not provided.

    @get_data and @get_secret look a key up in the data items and secrets:
    NMSettingVpn methods or the dict.get of the maps an auth dialog reads.
    """
    profile_flags(get_data)
    if profile_is_secret(get_data):
        # A data copy next to the secret is stale.
        value = get_secret(KEY_PROFILE)
    else:
        value = get_data(KEY_PROFILE)
    if not value:
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise FormatError("The stored OpenVPN profile is corrupt")


def setting_profile(s_vpn):
    if s_vpn is None:
        return None
    return read_profile(s_vpn.get_data_item, s_vpn.get_secret)


def needs_user_pass(get_data):
    return get_data(flags_key(KEY_PASSWORD)) is not None


def needs_cert_pass(get_data):
    return get_data(flags_key(KEY_CERT_PASS)) is not None


def encode_profile(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def validate(s_vpn, with_secrets=True):
    """Problems of a VPN setting against the format, empty when it conforms.

    @with_secrets: the setting carries its secrets (a system-owned profile
    must then be there); False for a connection read without them.
    """
    if s_vpn is None:
        return ["no vpn setting"]
    get = s_vpn.get_data_item
    problems = []
    if s_vpn.get_service_type() != SERVICE_TYPE:
        problems.append(f"service-type is {s_vpn.get_service_type()!r}, not {SERVICE_TYPE}")
    for key in SECRETS:
        try:
            secret_flags(get, key)
        except FormatError as e:
            problems.append(str(e))
    try:
        flags = profile_flags(get)
        profile = read_profile(get, s_vpn.get_secret)
    except FormatError as e:
        return problems + [str(e)]
    if not profile_is_secret(get):
        problems.append(f"profile is in vpn.data, not a secret (no {KEY_PROFILE_FLAGS})")
    elif get(KEY_PROFILE) is not None:
        problems.append("stale profile copy in vpn.data next to the secret")
    if profile is None:
        if not profile_is_secret(get) or (with_secrets and flags == NM.SettingSecretFlags.NONE):
            problems.append("no profile")
        return problems
    if get(KEY_REMOTE) is None:
        problems.append(f"no {KEY_REMOTE}")
    if ovpn.needs_user_pass(profile) != needs_user_pass(get):
        problems.append(f"{flags_key(KEY_PASSWORD)} must be set exactly when the profile has auth-user-pass")
    if needs_user_pass(get) and secret_flags(get, KEY_CHALLENGE) != NM.SettingSecretFlags.NOT_SAVED:
        problems.append(f"{flags_key(KEY_CHALLENGE)} must be 2 (one-time codes are never stored)")
    if (pki.has_pkcs12(profile) or pki.has_encrypted_key(profile)) and not needs_cert_pass(get):
        problems.append(f"the private key needs a passphrase but there is no {flags_key(KEY_CERT_PASS)}")
    for name, args, inline in ovpn.directives(profile):
        if name == "auth-user-pass" and (args or inline):
            problems.append("the profile carries credentials; they belong in the connection")
        elif name in ovpn.FILE_DIRECTIVES and args and args[0] != "[inline]" \
                and not (name == "crl-verify" and args[1:2] == ["dir"]):
            problems.append(f"{name} points to a file; files are inlined at import")
    return problems
