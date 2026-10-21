# network-manager-openvpn3

NetworkManager VPN plugin for [OpenVPN 3 Linux](https://github.com/OpenVPN/openvpn3-linux).

OpenVPN 3 Linux ships its own command line client and D-Bus services, but no
NetworkManager integration. This plugin lets NetworkManager start and stop
openvpn3 sessions, so OpenVPN 3 connections show up where every other VPN
does: the VPN toggle in GNOME Quick Settings, **Settings → Network**, and
`nmcli`.

Supported releases: Ubuntu 26.04, Ubuntu 24.04, Debian 13 (amd64).

## Desktops

| Desktop | Status |
|---|---|
| GNOME | Editor for GNOME Settings, prompts in GNOME Shell (`network-manager-openvpn3-gnome`). |
| Xfce, MATE, Cinnamon, LXQt and others with nm-applet | GTK 3 editor in nm-connection-editor, prompts from nm-applet (`network-manager-openvpn3-gnome`). |
| Any, from the command line | `nmcli` (`network-manager-openvpn3`). |
| KDE Plasma | Needs a front-end; see [Support for another desktop](docs/frontends.md). |

The core package works without any desktop; a front-end adds the editor
for one. [docs/frontends.md](docs/frontends.md) says how to write one and
how to check it with the conformance suite.

## Installation

### From the APT repository

```sh
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://alexeysetevoi.github.io/network-manager-openvpn3/network-manager-openvpn3.gpg \
  | sudo tee /etc/apt/keyrings/network-manager-openvpn3.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/network-manager-openvpn3.gpg] https://alexeysetevoi.github.io/network-manager-openvpn3 $(. /etc/os-release && echo "$VERSION_CODENAME") main" \
  | sudo tee /etc/apt/sources.list.d/network-manager-openvpn3.list
sudo apt update
sudo apt install network-manager-openvpn3-gnome
```

Check that the key you downloaded is the project key:

```sh
gpg --show-keys /etc/apt/keyrings/network-manager-openvpn3.gpg
```

The fingerprint must be `58DA FDC4 A0AE 538F 7D72  5E61 78EB B361 EFD7 F500`.

`network-manager-openvpn3-gnome` adds the GTK editors; without a GTK
desktop install `network-manager-openvpn3` alone.

The packages depend on the OpenVPN 3 client (`openvpn3-client`). Ubuntu
26.04 and Debian 13 have it in their archives. On Ubuntu 24.04 add the
[OpenVPN 3 repository](https://community.openvpn.net/openvpn/wiki/OpenVPN3Linux)
first. Tested with openvpn3 v24 (Debian 13) and v27.

### From a release

Each [release](https://github.com/AlexeySetevoi/network-manager-openvpn3/releases)
has the `.deb` files for every supported distribution, the source tarball, a
`SHA256SUMS` file and its signature.

```sh
gpg --import network-manager-openvpn3.asc
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install ./network-manager-openvpn3_*+noble1_amd64.deb ./network-manager-openvpn3-gnome_*+noble1_amd64.deb
```

The files also carry [build provenance attestations](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations):

```sh
gh attestation verify network-manager-openvpn3_*.deb -R AlexeySetevoi/network-manager-openvpn3
```

(`gh attestation` needs GitHub CLI 2.49 or newer.)

## Usage

Import an OpenVPN profile:

```sh
nmcli connection import type openvpn3 file office.ovpn
nmcli connection up office
```

In GNOME: **Settings → Network → VPN → +**, choose **OpenVPN 3**, then
**Load from file…**. If the classic OpenVPN plugin is installed as well,
**Import from file…** may pick that one; choosing **OpenVPN 3** explicitly
avoids it.

After that the connection is in the VPN menu of Quick Settings.

### Profiles and credentials

- Files referenced by the profile (`ca`, `cert`, `key`, `tls-auth`,
  `tls-crypt`, `auth-user-pass` …) are read once at import and stored inside
  the connection, the original files are no longer needed.
- The profile, private key included, is a NetworkManager secret: only root
  and the service can read it. Connections made before 0.2.0 kept it as
  plain connection data, readable by every local user; upgrading the
  package moves them.
- Credentials in the profile (an inline `<auth-user-pass>` block or the file
  it points to) move into the connection: the username as data, the password
  as a NetworkManager secret. Without them the password is agent-owned: GNOME
  asks for it and can keep it in the keyring.
- One-time codes (`static-challenge`, and dynamic challenges sent by the
  server) are asked for on every connect and never stored.
- Web based login (the server sends a URL, e.g. SSO/OAuth after the
  password): the login page opens in the browser of the user at the desktop
  (the active graphical session), and the connection completes once the
  login is done there. NetworkManager gives a VPN 60 seconds to come up by
  default; for slow logins raise it, e.g.
  `nmcli connection modify office vpn.timeout 180`.
- Client certificates: PEM `cert`/`key`, encrypted private keys and PKCS#12
  bundles (`pkcs12`). openvpn3 itself cannot read PKCS#12, so the bundle is
  kept in the connection and converted to PEM in memory when connecting. The
  passphrase of an encrypted key or bundle is a NetworkManager secret
  ("Private key passphrase"), asked for by GNOME and optionally kept in the
  keyring.
- Exported profiles contain no credentials, but the private key; they are
  written readable by their owner only.

### Editors

- **GNOME Settings** uses the GTK 4 editor, **nm-connection-editor** the
  GTK 3 one; both come with `network-manager-openvpn3-gnome`. They load a
  profile from a file and edit the username and the stored secrets.
- Editors for other desktops: [docs/frontends.md](docs/frontends.md).

### Limitation: PKCS#11

Hardware tokens and smart cards (`pkcs11-providers`, `pkcs11-id`) do not work
because openvpn3 has no PKCS#11 / external key support: in openvpn3-linux
(checked v24 to v27) the external PKI callbacks are unimplemented
([`core-client.hpp`](https://github.com/OpenVPN/openvpn3-linux/blob/master/src/client/core-client.hpp),
`external_pki_cert_request` / `external_pki_sign_request`), and a profile
without a PEM key is rejected with *"Configuration requires external PKI
which is not implemented yet"*. Nothing a front-end can do works around it;
use the classic OpenVPN 2 NetworkManager plugin for such profiles until
openvpn3 implements it.

## How it works

- `nm-openvpn3-service` is started by NetworkManager. It imports the profile
  into openvpn3 as a single-use configuration, creates a session and drives
  it over openvpn3's D-Bus API, answering credential requests from the
  connection's secrets.
- openvpn3 configures the tunnel itself (`openvpn3-service-netcfg`: device,
  addresses, routes, DNS). Once connected the plugin reports the same
  addresses, routes and DNS settings to NetworkManager, so NetworkManager
  shows the connection as active with correct details and keeps its own DNS
  configuration consistent.
- openvpn3 only applies DNS when its netcfg service is told which resolver to
  use. On a fresh install with systemd-resolved running, the package enables
  it (`/var/lib/openvpn3/netcfg.json`); an existing configuration is left
  alone. To enable it by hand:
  `sudo openvpn3-admin netcfg-service --config-set systemd-resolved 1`.
- Sessions belong to root; `sudo openvpn3 sessions-list` shows them.
- The profile is stored base64-encoded as a connection secret
  (`vpn.secrets` key `profile`): some NetworkManager settings backends
  (netplan on Ubuntu) do not keep multi-line values intact. The whole
  format: [docs/connection-format.md](docs/connection-format.md).
- When openvpn3 ends a session, the journal says why (for example the
  server expiring the session); openvpn3's own log is in
  `journalctl -t openvpn3-service-log`.

Logs: `journalctl -u NetworkManager | grep nm-openvpn3`.

## Reporting problems

Open an [issue](https://github.com/AlexeySetevoi/network-manager-openvpn3/issues/new/choose).
The bug report form asks for the distribution, package versions, desktop and
the NetworkManager journal, and has a command that collects all of it.
Remove server names, addresses, usernames and login URLs before posting, and
never attach a profile as is.

## Building

```sh
meson setup build
ninja -C build
meson test -C build
```

Build dependencies (Debian/Ubuntu): `meson pkgconf libnm-dev
libglib2.0-dev python3-gi python3-pytest python3-cryptography
gir1.2-nm-1.0 dbus-daemon`, and for the GTK front-end `libnma-gtk4-dev libgtk-4-dev
libnma-dev libgtk-3-dev`. `-Dgnome=false -Dgtk3=false` builds the core
alone.

Layout: `src/` the service (Python), `libnm-plugin/` import, export and the
connection format (C, no GTK), `auth-dialog/` the helper for secret agents,
`frontends/gtk/` the GTK editors.

Packages for one distribution, built in a container:

```sh
scripts/build-deb.sh ubuntu:26.04 resolute dist/
```

### Tests

- `tests/unit`: profile import (through the built libnm plugin) against the
  documented connections in `tests/conformance/expected`, the connection
  format, the VPN plugin D-Bus contract, the session state machine against
  a fake openvpn3, PKCS#12 conversion, the upgrade migration and the auth
  dialog.
- `frontends/gtk/tests`: the GTK editors, with a display
  (`GDK_BACKEND=broadway` works headless; `OPENVPN3_EDITOR_GTK=3` tests the
  GTK 3 one).
- `tests/conformance`: what any front-end must hand to the core, run with
  `scripts/conformance <driver>` against the core's reference driver and the
  GTK editors ([docs/frontends.md](docs/frontends.md#conformance)).
- `tests/integration/run.sh`: end to end on a disposable machine — a local
  OpenVPN server, openvpn3 as the client, NetworkManager with this plugin:
  connect, routes and DNS, the profile hidden from other users, migration
  of an old connection, reconnect, disconnect, wrong password, password
  from a secret agent, one-time code, PKCS#12 bundle and encrypted key
  against a certificate-only server. `tests/integration/Vagrantfile` runs it
  in libvirt VMs:

  ```sh
  cd tests/integration
  BOX=cloud-image/ubuntu-26.04 DEBS=$PWD/../../dist vagrant up
  vagrant ssh -c 'sudo /repo/tests/integration/run.sh /debs'
  ```

CI builds and tests every distribution, the core alone without GTK, the
GTK editors and their conformance, and runs the integration test on
Ubuntu 24.04. Tags `v*` publish a release and the APT repository.

## License

GPL-2.0-or-later, see [LICENSE](LICENSE).

This project is developed with [Claude Code](https://claude.com/claude-code).
