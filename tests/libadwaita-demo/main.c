/* libadwaita 测试项目：验证 gtk-cross 构建出的 GTK4 + libadwaita 能否正常运行。
 *
 * 功能：
 *  - 用 AdwApplication 应用框架初始化 libadwaita（同时拉起 GTK4）
 *  - 打开 AdwApplicationWindow：AdwToolbarView + AdwHeaderBar + AdwToastOverlay
 *  - 打印 GTK4 / libadwaita 版本号，按钮弹出 AdwToast
 *  - --smoke 参数：窗口显示 2 秒后自动退出（供无人值守验证运行栈）
 *
 * 编译（msys2 ucrt64 bash，与 gtk-cross 构建环境一致）：
 *   export PKG_CONFIG_LIBDIR=$PWD/out/msys2-ucrt64/lib/pkgconfig:$PWD/out/msys2-ucrt64/share/pkgconfig
 *   export PKG_CONFIG_PATH=
 *   gcc $(pkg-config --cflags gtk4 libadwaita-1) main.c $(pkg-config --libs gtk4 libadwaita-1) \
 *       -o libadwaita-demo.exe
 *   # 注意：-l 库参数必须放在 main.c 之后（GNU ld 单遍扫描），否则符号全 undefined
 *
 * 或 meson：
 *   meson setup builddir -Dpkg_config_path=$PWD/out/msys2-ucrt64/lib/pkgconfig
 *
 * 运行（需将 sysroot 的 bin 目录前置到 PATH 以找到 DLL，并设置
 * XDG_DATA_DIRS 指向 sysroot 的 share——Windows 下 glib 对非空
 * XDG_DATA_DIRS 排他使用并跳过 DLL/exe 相对定位，MSYS2 登录 shell
 * 导出的值会让 GSettings schema 找不到）：
 *   PATH=$PWD/out/msys2-ucrt64/bin:$PATH XDG_DATA_DIRS=$PWD/out/msys2-ucrt64/share ./libadwaita-demo.exe          # 交互窗口
 *   PATH=$PWD/out/msys2-ucrt64/bin:$PATH XDG_DATA_DIRS=$PWD/out/msys2-ucrt64/share ./libadwaita-demo.exe --smoke  # 2 秒后自动退出
 *
 * 历史问题（2026-08-24 定位，修复已入库，待重建 cairo 后复验）：
 * 1) GLib-GIO-CRITICAL g_settings_schema_source_lookup: assertion
 *    'source != NULL'：MSYS2 登录 shell 的 /etc/profile.d/000-msys2.sh 导出
 *    XDG_DATA_DIRS 指向 MSYS2 前缀，schema source 整体为 NULL（libadwaita
 *    上游本不携带 gschema，非安装缺失）；运行命令带 XDG_DATA_DIRS 即消除。
 * 2) 窗口 present 的字体度量段错误（gdb: gtk_label_measure →
 *    pango_context_get_metrics → cairo_win32_font_face_create_for_logfontw_hfont，
 *    0xc0000005）：cairo 的 Win32 静态互斥体（CRITICAL_SECTION）由
 *    cairo_win32_tls_callback（.CRT$XLD）初始化，GCC/bfd 下无 PE TLS
 *    directory、回调不被调用，与 glib 的 TLS 修复同族；cairo recipe 已加
 *    -Dc_link_args=-Wl,--undefined=_tls_used 修复，见 README「当前已知失败」。
 */
#include <adwaita.h>

static gboolean opt_smoke = FALSE;

static const GOptionEntry app_entries[] = {
    { "smoke", 0, 0, G_OPTION_ARG_NONE, &opt_smoke,
      "auto-quit after 2s (unattended verification)", NULL },
    { NULL }
};

static gboolean smoke_quit(gpointer user_data) {
    gtk_window_destroy(GTK_WINDOW(user_data));
    return G_SOURCE_REMOVE;
}

static void toast_cb(GtkButton *btn, AdwToastOverlay *overlay) {
    (void) btn;
    AdwToast *toast = adw_toast_new("Hello from gtk-cross libadwaita!");
    adw_toast_overlay_add_toast(overlay, toast);
}

static void activate_cb(GApplication *app, gpointer user_data) {
    (void) user_data;

    AdwApplicationWindow *win = ADW_APPLICATION_WINDOW(
        adw_application_window_new(GTK_APPLICATION(app)));
    gtk_window_set_title(GTK_WINDOW(win), "gtk-cross libadwaita demo");
    gtk_window_set_default_size(GTK_WINDOW(win), 480, 320);

    AdwToolbarView *toolbar = ADW_TOOLBAR_VIEW(adw_toolbar_view_new());
    adw_application_window_set_content(win, GTK_WIDGET(toolbar));

    AdwHeaderBar *header = ADW_HEADER_BAR(adw_header_bar_new());
    AdwWindowTitle *title = ADW_WINDOW_TITLE(adw_window_title_new(
        "gtk-cross libadwaita demo", NULL));
    adw_header_bar_set_title_widget(header, GTK_WIDGET(title));
    adw_toolbar_view_add_top_bar(toolbar, GTK_WIDGET(header));

    AdwToastOverlay *overlay = ADW_TOAST_OVERLAY(adw_toast_overlay_new());
    adw_toolbar_view_set_content(toolbar, GTK_WIDGET(overlay));

    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    gtk_widget_set_valign(box, GTK_ALIGN_CENTER);
    gtk_widget_set_halign(box, GTK_ALIGN_CENTER);
    adw_toast_overlay_set_child(overlay, box);

    /* 版本信息：GTK 取运行时版本，libadwaita 取编译时版本 */
    char *ver = g_strdup_printf("GTK %u.%u.%u + libadwaita %u.%u.%u",
        gtk_get_major_version(), gtk_get_minor_version(), gtk_get_micro_version(),
        ADW_MAJOR_VERSION, ADW_MINOR_VERSION, ADW_MICRO_VERSION);
    gtk_box_append(GTK_BOX(box), gtk_label_new(ver));
    g_free(ver);

    GtkWidget *btn = gtk_button_new_with_label("Show toast");
    g_signal_connect(btn, "clicked", G_CALLBACK(toast_cb), overlay);
    gtk_box_append(GTK_BOX(box), btn);

    if (opt_smoke) {
        g_print("smoke mode: auto-quit in 2s\n");
        g_timeout_add_seconds(2, smoke_quit, win);
    }

    gtk_window_present(GTK_WINDOW(win));
}

int main(int argc, char **argv) {
    AdwApplication *app = adw_application_new(
        "org.gtkcross.LibadwaitaDemo", G_APPLICATION_DEFAULT_FLAGS);
    if (!app)
        g_error("adw_application_new failed: libadwaita init error");

    g_application_add_main_option_entries(G_APPLICATION(app), app_entries);

    g_signal_connect(app, "activate", G_CALLBACK(activate_cb), NULL);

    int status = g_application_run(G_APPLICATION(app), argc, argv);
    g_object_unref(app);

    g_print("libadwaita demo exited with status %d\n", status);
    return status;
}