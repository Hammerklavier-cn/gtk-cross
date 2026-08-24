/* M0 冒烟测试：验证自建 sysroot 的 GLib/GIO 可用（不依赖系统 GTK）。
 * 编译:
 *   gcc $(pkg-config --cflags --libs gio-2.0) smoke_glib.c -o smoke_glib.exe
 */
#include <glib.h>
#include <gio/gio.h>
#include <stdio.h>

static gboolean quit_cb(gpointer loop) {
    g_main_loop_quit((GMainLoop *)loop);
    return G_SOURCE_REMOVE;
}

int main(void) {
    GMainLoop *loop = g_main_loop_new(NULL, FALSE);

    const char *ver = glib_check_version(2, 0, 0);
    GFile *tmp = g_file_new_tmp("gtkcross-smoke-XXXXXX", NULL, NULL);
    char *uri = g_file_get_uri(tmp);

    g_print("glib %s | gio OK | temp: %s\n", ver, uri);
    g_timeout_add(10, quit_cb, loop);
    g_main_loop_run(loop);

    g_free(uri);
    g_object_unref(tmp);
    g_main_loop_unref(loop);

    g_print("GMainLoop run OK\n");
    return 0;
}
