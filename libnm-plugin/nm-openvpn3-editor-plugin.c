/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * libnm VPN editor plugin for openvpn3: import/export of profiles, loads the
 * GTK editor on demand so that the plugin itself does not link GTK. Other
 * front-ends use import/export only (docs/frontends.md).
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <gio/gio.h>
#include <glib/gstdio.h>
#include <string.h>

#include "ovpn-import.h"

#define EDITOR_GTK4 "libnm-gtk4-vpn-plugin-openvpn3-editor.so"
#define EDITOR_GTK3 "libnm-vpn-plugin-openvpn3-editor.so"
#define EDITOR_FACTORY "nm_vpn_editor_factory_openvpn3"

typedef NMVpnEditor *(*EditorFactory)(NMVpnEditorPlugin *plugin, NMConnection *connection, GError **error);

enum { PROP_0, PROP_NAME, PROP_DESC, PROP_SERVICE };

#define OPENVPN3_TYPE_EDITOR_PLUGIN (openvpn3_editor_plugin_get_type())
G_DECLARE_FINAL_TYPE(Openvpn3EditorPlugin, openvpn3_editor_plugin, OPENVPN3, EDITOR_PLUGIN, GObject)

struct _Openvpn3EditorPlugin {
    GObject parent;
};

static void openvpn3_editor_plugin_interface_init(NMVpnEditorPluginInterface *iface);

G_DEFINE_TYPE_WITH_CODE(Openvpn3EditorPlugin, openvpn3_editor_plugin, G_TYPE_OBJECT,
                        G_IMPLEMENT_INTERFACE(NM_TYPE_VPN_EDITOR_PLUGIN, openvpn3_editor_plugin_interface_init))

static NMConnection *
import_from_file(NMVpnEditorPlugin *plugin, const char *path, GError **error)
{
    g_autoptr(Openvpn3Profile) p = NULL;
    g_autofree char *base        = NULL;
    char *dot;

    if (!g_str_has_suffix(path, ".ovpn") && !g_str_has_suffix(path, ".conf")) {
        g_set_error_literal(error, NM_CONNECTION_ERROR, NM_CONNECTION_ERROR_FAILED,
                            "Not an OpenVPN profile (expected .ovpn or .conf)");
        return NULL;
    }
    p = openvpn3_profile_load(path, error);
    if (!p)
        return NULL;
    base = g_path_get_basename(path);
    dot  = strrchr(base, '.');
    if (dot)
        *dot = '\0';
    return openvpn3_connection_from_profile(p, base);
}

/* nmcli hands over the connection without its secrets; ask NetworkManager
 * for them as the calling user would. */
static char *
fetch_secret_profile(NMConnection *connection, GError **error)
{
    const char *obj_path              = nm_connection_get_path(connection);
    g_autoptr(GDBusConnection) bus    = NULL;
    g_autoptr(GVariant) reply         = NULL;
    g_autoptr(GVariant) secrets       = NULL;
    g_autoptr(NMConnection) copy      = NULL;

    if (!obj_path)
        return NULL;
    bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, NULL, error);
    if (!bus)
        return NULL;
    reply = g_dbus_connection_call_sync(bus, NM_DBUS_SERVICE, obj_path, NM_DBUS_INTERFACE_SETTINGS_CONNECTION,
                                        "GetSecrets", g_variant_new("(s)", NM_SETTING_VPN_SETTING_NAME),
                                        G_VARIANT_TYPE("(a{sa{sv}})"), G_DBUS_CALL_FLAGS_NONE, -1, NULL, error);
    if (!reply)
        return NULL;
    g_variant_get(reply, "(@a{sa{sv}})", &secrets);
    copy = nm_simple_connection_new_clone(connection);
    if (!nm_connection_update_secrets(copy, NM_SETTING_VPN_SETTING_NAME, secrets, error))
        return NULL;
    return openvpn3_setting_get_profile(nm_connection_get_setting_vpn(copy));
}

static gboolean
export_to_file(NMVpnEditorPlugin *plugin, const char *path, NMConnection *connection, GError **error)
{
    NMSettingVpn *s_vpn     = nm_connection_get_setting_vpn(connection);
    g_autofree char *config = openvpn3_setting_get_profile(s_vpn);

    if (!config && openvpn3_setting_profile_is_secret(s_vpn)) {
        g_autoptr(GError) local = NULL;

        config = fetch_secret_profile(connection, &local);
        if (local) {
            g_set_error(error, NM_CONNECTION_ERROR, NM_CONNECTION_ERROR_MISSING_PROPERTY,
                        "The OpenVPN profile is stored as a secret and could not be read: %s", local->message);
            return FALSE;
        }
    }
    if (!config) {
        g_set_error_literal(error, NM_CONNECTION_ERROR, NM_CONNECTION_ERROR_MISSING_PROPERTY,
                            openvpn3_setting_profile_is_secret(s_vpn)
                                ? "The OpenVPN profile is stored as a secret and was not provided"
                                : "The connection has no OpenVPN profile");
        return FALSE;
    }
    /* Credentials stay in NetworkManager, the exported profile asks for
     * them; the private key is still in it. GLib keeps the mode of a file
     * it replaces, so tighten that first. */
    if (g_chmod(path, 0600) < 0 && errno != ENOENT) {
        int err = errno;

        g_set_error(error, G_FILE_ERROR, g_file_error_from_errno(err), "Cannot restrict %s: %s", path,
                    g_strerror(err));
        return FALSE;
    }
    return g_file_set_contents_full(path, config, -1, G_FILE_SET_CONTENTS_CONSISTENT, 0600, error);
}

static char *
get_suggested_filename(NMVpnEditorPlugin *plugin, NMConnection *connection)
{
    const char *id = nm_connection_get_id(connection);

    return g_strdup_printf("%s.ovpn", id ? id : "openvpn3");
}

static NMVpnEditorPluginCapability
get_capabilities(NMVpnEditorPlugin *plugin)
{
    return NM_VPN_EDITOR_PLUGIN_CAPABILITY_IMPORT | NM_VPN_EDITOR_PLUGIN_CAPABILITY_EXPORT
           | NM_VPN_EDITOR_PLUGIN_CAPABILITY_IPV6;
}

static char *
editor_path(const char *library)
{
    Dl_info info;
    g_autofree char *dir = NULL;

    if (!dladdr((void *) get_capabilities, &info) || !info.dli_fname)
        return g_strdup(library);
    dir = g_path_get_dirname(info.dli_fname);
    return g_build_filename(dir, library, NULL);
}

static NMVpnEditor *
get_editor(NMVpnEditorPlugin *plugin, NMConnection *connection, GError **error)
{
    static EditorFactory factory;

    if (!factory) {
        g_autofree char *path = NULL;
        void *handle;

        const char *library;

        /* Pick the editor built for the GTK the host already runs: loading
         * the other major version into the process would abort it. */
        if (dlsym(RTLD_DEFAULT, "gtk_widget_get_first_child"))
            library = EDITOR_GTK4;
        else if (dlsym(RTLD_DEFAULT, "gtk_widget_show_all"))
            library = EDITOR_GTK3;
        else {
            g_set_error_literal(error, NM_VPN_PLUGIN_ERROR, NM_VPN_PLUGIN_ERROR_FAILED,
                                "The openvpn3 editor needs a GTK 3 or GTK 4 application");
            return NULL;
        }
        path   = editor_path(library);
        handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        if (!handle) {
            g_set_error(error, NM_VPN_PLUGIN_ERROR, NM_VPN_PLUGIN_ERROR_FAILED,
                        "Cannot load the openvpn3 editor: %s", dlerror());
            return NULL;
        }
        factory = (EditorFactory) dlsym(handle, EDITOR_FACTORY);
        if (!factory) {
            g_set_error(error, NM_VPN_PLUGIN_ERROR, NM_VPN_PLUGIN_ERROR_FAILED,
                        "Invalid openvpn3 editor library %s", path);
            dlclose(handle);
            return NULL;
        }
    }
    return factory(plugin, connection, error);
}

static void
get_property(GObject *object, guint prop_id, GValue *value, GParamSpec *pspec)
{
    switch (prop_id) {
    case PROP_NAME:
        g_value_set_string(value, "OpenVPN 3");
        break;
    case PROP_DESC:
        g_value_set_string(value, "Compatible with OpenVPN servers, using the OpenVPN 3 Linux client.");
        break;
    case PROP_SERVICE:
        g_value_set_string(value, OPENVPN3_SERVICE_TYPE);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
    }
}

static void
openvpn3_editor_plugin_init(Openvpn3EditorPlugin *plugin)
{
}

static void
openvpn3_editor_plugin_class_init(Openvpn3EditorPluginClass *klass)
{
    GObjectClass *object_class = G_OBJECT_CLASS(klass);

    object_class->get_property = get_property;
    g_object_class_override_property(object_class, PROP_NAME, NM_VPN_EDITOR_PLUGIN_NAME);
    g_object_class_override_property(object_class, PROP_DESC, NM_VPN_EDITOR_PLUGIN_DESCRIPTION);
    g_object_class_override_property(object_class, PROP_SERVICE, NM_VPN_EDITOR_PLUGIN_SERVICE);
}

static void
openvpn3_editor_plugin_interface_init(NMVpnEditorPluginInterface *iface)
{
    iface->get_editor             = get_editor;
    iface->get_capabilities       = get_capabilities;
    iface->import_from_file       = import_from_file;
    iface->export_to_file         = export_to_file;
    iface->get_suggested_filename = get_suggested_filename;
}

G_MODULE_EXPORT NMVpnEditorPlugin *
nm_vpn_editor_plugin_factory(GError **error)
{
    g_return_val_if_fail(!error || !*error, NULL);
    return g_object_new(OPENVPN3_TYPE_EDITOR_PLUGIN, NULL);
}
