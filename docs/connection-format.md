# Connection format

What an openvpn3 connection looks like in NetworkManager. The core
(service, libnm plugin) and every front-end rely on it, so it changes only
on purpose: `tests/conformance/expected` holds what the import writes for
each test profile, and changing the format changes those files and this
page together.

In code: `libnm-plugin/ovpn-import.c` (C) and `src/nm_openvpn3/connection.py`
(Python, `validate()` checks a connection against this page).
`scripts/check-connection` runs that check on a live connection or a file.

## VPN setting

`vpn.service-type` is `org.freedesktop.NetworkManager.openvpn3`.

### `vpn.data`

| Key | Value |
|---|---|
| `profile-flags` | Secret flags of the profile: `0` (system-owned, the default) or `1` (agent-owned). Its presence means the profile is a secret. |
| `remote` | First remote host of the profile. For display only. |
| `username` | Username, when the profile uses `auth-user-pass`. |
| `password-flags` | Set exactly when the profile uses `auth-user-pass`. |
| `challenge-response-flags` | `2` (not saved) whenever `password-flags` is set: one-time codes are asked for on every connect. |
| `cert-pass-flags` | Set when the private key needs a passphrase (PKCS#12 bundle or encrypted PEM key). |
| `profile` | Only in connections from before 0.2.0, see [Layouts](#layouts). |

Unknown keys belong to someone else: keep them when saving.

### `vpn.secrets`

| Key | Value |
|---|---|
| `profile` | The OpenVPN profile, base64 of its UTF-8 text. |
| `password` | Password for `auth-user-pass`. |
| `cert-pass` | Passphrase of the private key. |
| `challenge-response` | One-time code; never stored, only passed while connecting. |

Flags follow NetworkManager: `0` stored by NetworkManager (root only),
`1` kept by the user's secret agent (GNOME keyring, KWallet), `2` asked for
every time, `4` not needed.

## The profile

Base64 because multi-line values do not survive every settings backend
(netplan escapes newlines twice). The text is the imported profile,
normalized by the import:

- every file it references (`ca`, `cert`, `key`, `extra-certs`, `tls-auth`,
  `tls-crypt`, `tls-crypt-v2`, `crl-verify`) is inlined as a `<tag>` block;
  `tls-auth <file> <dir>` becomes the block plus `key-direction <dir>`;
- `pkcs12 <file>` becomes a base64 `<pkcs12>` block; the service converts it
  to PEM when connecting, openvpn3 cannot read PKCS#12;
- credentials (`<auth-user-pass>` or the file `auth-user-pass` names) move to
  `username` and `password`; a bare `auth-user-pass` stays, so openvpn3
  asks for them;
- `<connection>` blocks stay as they are, nothing inside is inlined;
- everything else, comments and blank lines included, is kept as written.

## Layouts

| | Profile in | Written by |
|---|---|---|
| Secret (0.2.0 and later) | `vpn.secrets.profile`, with `vpn.data.profile-flags` | everything |
| Data (before 0.2.0) | `vpn.data.profile`, no `profile-flags` | nothing, read only |

- With `profile-flags` the profile is read from the secrets only; a
  `vpn.data.profile` next to it is stale and ignored.
- System-owned (`0`) is the default: NetworkManager keeps the profile
  root-only and hands it to the service itself, with no agent involved.
  In the data layout any local user could read the private key with
  `nmcli -f vpn.data connection show`.
- Agent-owned (`1`) is for front-ends whose agent keeps secrets per user.
  The service then asks the agent for the `vpn` setting before it knows
  anything else.
- The package upgrade to 0.2.0 moves existing connections
  (`/usr/libexec/nm-openvpn3-migrate`); the editors move a connection when
  they save it.

## Secrets requests

While connecting, the service asks NetworkManager for secrets with these
hints:

| Hints | Meaning |
|---|---|
| `password` | Password, missing or rejected. |
| `cert-pass` | Private key passphrase, missing or wrong. |
| `challenge-response` | One-time code; with `x-challenge-echo` it may be shown while typed. |

NetworkManager adds `x-vpn-message:<text>` with the server's prompt. Before
connecting, a secrets request without hints means: whatever the flags say
is needed and not stored.
