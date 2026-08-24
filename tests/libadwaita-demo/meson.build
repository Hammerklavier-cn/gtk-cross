# libadwaita 测试项目（C）
# 验证 gtk-cross 构建的 GTK4 + libadwaita 可编译、可运行。
#
# 构建（在 msys2 ucrt64 bash 中，指向自建 sysroot）：
#   export PKG_CONFIG_LIBDIR="C:/msys64/home/q5vsx/project/gtk-cross/out/msys2-ucrt64/lib/pkgconfig;C:/msys64/home/q5vsx/project/gtk-cross/out/msys2-ucrt64/share/pkgconfig"
#   export PKG_CONFIG_PATH=
#   meson setup builddir
#   meson compile -C builddir
# 注意：MSYS2 的 pkgconf 是原生 Windows 程序，目录列表分隔符须用分号（;）而非冒号
#
# 运行（sysroot bin 前置到 PATH 找 DLL；XDG_DATA_DIRS 指向 sysroot 的 share，
# 避开 MSYS2 登录 shell 导出值导致的 GSettings schema 找不到——Windows 下
# glib 对非空 XDG_DATA_DIRS 排他使用，跳过 DLL/exe 相对 share 定位）：
#   PATH=$PWD/out/msys2-ucrt64/bin:$PATH XDG_DATA_DIRS=$PWD/out/msys2-ucrt64/share ./builddir/libadwaita-demo.exe --smoke

project('libadwaita-demo', 'c', version: '0.1.0')

gtk_dep = dependency('gtk4')
adw_dep = dependency('libadwaita-1')

executable('libadwaita-demo',
  'main.c',
  dependencies: [gtk_dep, adw_dep])
