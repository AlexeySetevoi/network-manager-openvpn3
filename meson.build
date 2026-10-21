# SPDX-License-Identifier: GPL-2.0-or-later
project('network-manager-openvpn3', 'c',
  version: '0.2.0',
  license: 'GPL-2.0-or-later',
  meson_version: '>= 1.0',
  default_options: ['c_std=gnu11', 'warning_level=2'])

prefix = get_option('prefix')
libexecdir = prefix / get_option('libexecdir')
plugindir = prefix / get_option('libdir') / 'NetworkManager'
pkgdatadir = prefix / get_option('datadir') / meson.project_name()

add_project_arguments('-Wno-unused-parameter', '-Wno-missing-field-initializers', language: 'c')

libnm = dependency('libnm', version: '>= 1.42')
gmodule = dependency('gmodule-2.0')
dl = meson.get_compiler('c').find_library('dl', required: false)

conf = configuration_data()
conf.set('libexecdir', libexecdir)
conf.set('plugindir', plugindir)
conf.set('pkgdatadir', pkgdatadir)

configure_file(input: 'data/nm-openvpn3-service.name.in', output: 'nm-openvpn3-service.name',
  configuration: conf, install_dir: prefix / 'lib' / 'NetworkManager' / 'VPN')
install_data('data/nm-openvpn3-service.conf',
  install_dir: get_option('datadir') / 'dbus-1' / 'system.d')

# Service and auth dialog (Python)
configure_file(input: 'src/service/nm-openvpn3-service.in', output: 'nm-openvpn3-service',
  configuration: conf, install_dir: libexecdir, install_mode: 'rwxr-xr-x')
install_subdir('src/nm_openvpn3', install_dir: pkgdatadir,
  exclude_directories: ['__pycache__'])

# Secret agents (GNOME Shell, nmcli) run it to learn which secrets to ask for.
configure_file(input: 'auth-dialog/nm-openvpn3-auth-dialog.in', output: 'nm-openvpn3-auth-dialog',
  configuration: conf, install_dir: libexecdir, install_mode: 'rwxr-xr-x')

# Moves profiles of existing connections into the secrets (package upgrade).
configure_file(input: 'src/service/nm-openvpn3-migrate.in', output: 'nm-openvpn3-migrate',
  configuration: conf, install_dir: libexecdir, install_mode: 'rwxr-xr-x')

# libnm plugin (no GTK): import/export and the connection format.
plugin_inc = include_directories('libnm-plugin')
import_lib = static_library('ovpn-import', 'libnm-plugin/ovpn-import.c',
  dependencies: libnm, pic: true)

shared_module('nm-vpn-plugin-openvpn3', 'libnm-plugin/nm-openvpn3-editor-plugin.c',
  name_prefix: 'lib',
  link_with: import_lib,
  dependencies: [libnm, gmodule, dl],
  link_args: ['-Wl,--version-script=' + meson.current_source_dir() / 'libnm-plugin/plugin.ver'],
  install: true, install_dir: plugindir)

pytest = find_program('pytest-3', 'pytest', required: false)
conformance = find_program('scripts/conformance')
test_env = ['OPENVPN3_PLUGIN_DIR=' + meson.current_build_dir(),
  'OPENVPN3_EDITOR_DIR=' + meson.current_build_dir() / 'frontends' / 'gtk']
if pytest.found()
  test('unit', pytest, args: ['-q', '-p', 'no:cacheprovider', meson.current_source_dir() / 'tests' / 'unit'],
    env: test_env)
  test('conformance-core', conformance,
    args: [meson.current_source_dir() / 'tests' / 'conformance' / 'drivers' / 'core'],
    env: test_env)
endif

# Front-ends: each one is a subdir with its own options and package.
subdir('frontends/gtk')
