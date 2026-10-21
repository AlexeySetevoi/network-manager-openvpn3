# SPDX-License-Identifier: GPL-2.0-or-later
"""Minimal inspection of OpenVPN profile text.

The profile stored in the NetworkManager connection is already normalized by
the import code: every file reference is inlined and inline credentials are
moved into the connection's username/password.
"""

import shlex
from dataclasses import dataclass

# Directives naming a file; the import inlines them.
FILE_DIRECTIVES = ("ca", "cert", "key", "extra-certs", "tls-auth", "tls-crypt", "tls-crypt-v2",
                   "crl-verify", "pkcs12")


@dataclass(frozen=True)
class StaticChallenge:
    text: str
    echo: bool


def directives(text):
    """Yield (name, args, inline) for every directive outside inline <tag> blocks.

    An inline block yields its tag with inline=True. Directives inside a
    <connection> block count: openvpn3 takes the remote from there.
    """
    inline_tag = None
    for raw in text.splitlines():
        line = raw.strip()
        if inline_tag is not None:
            if line == f"</{inline_tag}>":
                inline_tag = None
            continue
        if not line or line[0] in "#;" or line in ("<connection>", "</connection>"):
            continue
        if line.startswith("<") and line.endswith(">") and not line.startswith("</"):
            inline_tag = line[1:-1]
            yield inline_tag, [], True
            continue
        try:
            parts = shlex.split(line, comments=False)
        except ValueError:
            parts = line.split()
        yield parts[0], parts[1:], False


def needs_user_pass(text):
    return any(name == "auth-user-pass" for name, _, _ in directives(text))


def static_challenge(text):
    for name, args, _ in directives(text):
        if name == "static-challenge" and args:
            echo = len(args) > 1 and args[1] == "1"
            return StaticChallenge(args[0], echo)
    return None


def first_remote(text):
    """Return (host, port) of the first remote, port may be None."""
    for name, args, _ in directives(text):
        if name == "remote" and args:
            return args[0], (args[1] if len(args) > 1 else None)
    return None
