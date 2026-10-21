# SPDX-License-Identifier: GPL-2.0-or-later
# Editors: GTK 4 for GNOME Settings, GTK 3 for nm-connection-editor.
editors = [
  ['gnome', 'nm-gtk4-vpn-plugin-openvpn3-editor', ['gtk4', '>= 4.10'], 'libnma-gtk4', '4'],
  ['gtk3', 'nm-vpn-plugin-openvpn3-editor', ['gtk+-3.0', '>= 3.22'], 'libnma', '3'],
]
foreach e : editors
  if get_option(e[0])
    shared_module(e[1], 'nm-openvpn3-editor.c',
      name_prefix: 'lib',
      link_with: import_lib,
      include_directories: plugin_inc,
      dependencies: [libnm, dependency(e[2][0], version: e[2][1]), dependency(e[3], version: '>= 1.10')],
      link_args: ['-Wl,--version-script=' + meson.current_source_dir() / 'editor.ver'],
      install: true, install_dir: plugindir)
    # Needs a display (GDK_BACKEND=broadway works headless), skipped without one.
    if pytest.found()
      test('conformance-gtk' + e[4], conformance,
        args: [meson.current_source_dir() / 'tests' / 'driver'],
        env: test_env + ['OPENVPN3_EDITOR_GTK=' + e[4]])
    endif
  endif
endforeach
