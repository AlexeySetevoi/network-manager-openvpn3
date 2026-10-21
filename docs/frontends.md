# Support for another desktop

The plugin is a desktop-independent core plus front-ends. GTK is one
front-end; a desktop without one needs only its own editor, and maybe a
secrets dialog. This page says what that takes and how to check it works.

## Layers

| | What | Where | Package |
|---|---|---|---|
| Core | NetworkManager service, libnm plugin (import, export), connection format, helper for libnma secret agents | `src/`, `libnm-plugin/`, `auth-dialog/` | `network-manager-openvpn3` |
| Front-end | Connection editor; secrets dialog if the desktop's agent asks the front-end | `frontends/<name>/` or your own repository | `network-manager-openvpn3-<name>` |

The core already does, for every desktop:

- import and export of `.ovpn` files: `nmcli connection import type
  openvpn3 file …`, and the libnm plugin for editors;
- connecting, credentials, one-time codes, PKCS#12 and encrypted keys;
- web based login: the login page opens in the browser of the user at the
  desktop through xdg-desktop-portal, so a portal backend has to be
  installed (every major desktop ships one);
- the auth dialog that libnma-based agents run (GNOME Shell, nm-applet,
  nmcli). Desktops using nm-applet (Xfce, MATE, Cinnamon, LXQt and others)
  are covered by the GTK front-end already.

## Writing an editor

The front-end never parses OpenVPN profiles; the connection says everything
it needs ([connection format](connection-format.md)).

1. **Loading a profile** is the libnm plugin's import:
   `nm_vpn_editor_plugin_import()` on the plugin found through
   `nm_vpn_plugin_info_list_find_by_service()` for
   `org.freedesktop.NetworkManager.openvpn3` (Python:
   `NM.VpnEditorPlugin.import_()`). Take the VPN setting it returns as it
   is. A second parser would make the same profile behave differently on
   different desktops.
2. **Fields to show** follow `vpn.data`: username and password when
   `password-flags` is set, a private key passphrase when `cert-pass-flags`
   is set, the server from `remote`.
3. **Saving** edits the existing VPN setting in place: change `username`,
   the `password` / `cert-pass` secrets and their `-flags`, nothing else.
   Do not build a new setting: the profile secret, `profile-flags` and keys
   you do not know must survive. If the connection has `profile-flags` but
   you did not get the `profile` secret, refuse to save instead of saving a
   connection without a profile.
4. **Old connections** (`vpn.data.profile`, no `profile-flags`): on save,
   move the profile into the secret with `profile-flags=0` and set `remote`.
5. **Agent-owned profile** (`profile-flags=1`) only if your desktop's agent
   stores secrets per user (KWallet, keyring) and hands them back on connect.

## Secrets

- libnma-based agents run the core's auth dialog; nothing to do.
- If your desktop's agent asks the front-end instead (plasma-nm does,
  through its VPN UI plugin), answer the keys listed under
  [Secrets requests](connection-format.md#secrets-requests): the hinted
  keys, with the server's prompt from `x-vpn-message:`; and return an
  agent-owned `profile` with them.

## Where it lives

- **Your own repository**: build and ship it at your own pace, depend on
  the `network-manager-openvpn3` package only. For plasma-nm that is a VPN
  UI plugin installed to `plasma/network/vpn`, matched by
  `X-NetworkManager-Services=org.freedesktop.NetworkManager.openvpn3`.
- **Here**, under `frontends/<name>/`: its own `meson.build` included from
  the top one, a meson option (off by default), a package
  `network-manager-openvpn3-<name>` in `debian/control` and a conformance
  driver. `frontends/gtk/` is the example.

Either way, open an issue to get it listed in the README.

## Conformance

`tests/conformance` checks what a front-end hands to the core: the
connections it saves (and its secrets answers, if it has its own dialog).
It does not look at the UI. If it passes, the core will connect what your
editor saves.

```sh
git clone https://github.com/AlexeySetevoi/network-manager-openvpn3
cd network-manager-openvpn3
meson setup build -Dgnome=false -Dgtk3=false && ninja -C build
OPENVPN3_PLUGIN_DIR=$PWD/build scripts/conformance /path/to/your-driver
```

Without `OPENVPN3_PLUGIN_DIR` the installed package is used. Needs
`python3-gi`, `gir1.2-nm-1.0`, `python3-pytest` and `python3-cryptography`.

### The driver

An executable through which the suite works your editor, in any language.
Connections go in and out as JSON:

```json
{"id": "office",
 "vpn": {"service-type": "org.freedesktop.NetworkManager.openvpn3",
         "data": {"profile-flags": "0", "remote": "vpn.example.net"},
         "secrets": {"profile": "Y2xpZW50Cg=="}}}
```

That is the VPN setting your editor hands to NetworkManager, secrets an
agent would keep included.

| Command | Does |
|---|---|
| `driver capabilities` | Prints `{"name": …, "secret-storage": [...], "agent-profile": bool, "secrets": bool}`: storage choices your UI offers (`system`, `agent`, `ask`, `none`), whether it keeps agent-owned profiles, whether it has its own secrets dialog. Scenarios you do not offer are skipped. Exit 77 when it cannot run here (no display): the suite is skipped. |
| `driver load <profile.ovpn> <out.json>` | New connection, load the profile the way your UI does, save. |
| `driver edit <in.json> <changes.json> <out.json>` | Open the connection, apply the changes, save. Changes: `{"username": "…", "password": {"value": "…", "storage": "agent"}, "cert-pass": {…}}`, each part optional. |
| `driver secrets <in.json> <request.json> <out.json>` | Only with `"secrets": true`. Request: `{"hints": […], "message": "…", "answers": {key: value}}`, where answers are what the user types. Write the secrets your dialog returns as `{key: value}`. |

`tests/conformance/drivers/core` is the reference driver (the core alone,
as nmcli does it) and `frontends/gtk/tests/driver` drives the GTK editors.

### What is checked

- the connection follows the [connection format](connection-format.md);
- loading a profile gives exactly the connection the core's import gives;
- saving keeps what was not changed, moves old connections to the secret
  layout and applies the changes;
- the saved connections connect: they go through the real service against
  a fake openvpn3, which asks for passwords, one-time codes and key
  passphrases the way openvpn3 does;
- your secrets dialog returns what the service asked for.

For the real thing, `tests/integration/run.sh` brings up connections
against a local OpenVPN server in a VM; with `FRONTEND_DRIVER=<driver>`
the profiles are loaded through your driver.
