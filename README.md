# gtk-cross

从源码构建 GTK4 / libadwaita 及其全部依赖的可复现构建框架。

目标：除交叉编译器与宿主构建工具外，**所有 target 侧依赖库都从源码编译**，产出可复现的
sysroot（`prefix`/`lib`/`include`），供原生运行或交叉链接。

> 调查记录与笔记（验证结果、已知失败、运行时限制、版本升级说明、媒体链/EGL、
> CI 失败修复等）已迁至 [NOTES.md](NOTES.md)；本文件只保留稳定的使用文档。

## 静态优先（default_library: static）

除 GObject introspection 链之外，**所有库都只产出静态库（`.a`），不生成 DLL**。
`gtk-cross.yaml` 的 `default_library: static` 是这一点的唯一开关：Meson 工程由框架
注入 `default_library=static`，CMake 工程注入 `BUILD_SHARED_LIBS=OFF`，Autotools 注入
`--disable-shared`；个别包的自有开关（`ZLIB_BUILD_SHARED`、`PNG_SHARED`、`EXPAT_SHARED_LIBS`…）
在 recipe 的 `cmake.defines` 里显式指定。recipe 可用 `default_library:` 单包覆盖。

### 为什么保留少量 DLL

`g-ir-scanner` 无法 introspect 静态库（实测 `.a` 报
`can't resolve libraries to shared libraries`），且 typelib 在**运行期**经
`g_module_open` 动态加载目标库——零 DLL 与运行期反射在 Windows 上互斥。项目 README
明确 GIR/typelib 供 libadwaita-rs 等绑定消费，因此 introspection 链整体保持动态：

| 类别                        | 数量 | 内容                                                                                                                        |
| --------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------- |
| 静态（仅 `.a`）             | 25   | zlib expat pcre2 libpng libjpeg-turbo libtiff curl libxml2 libxmlb libfyaml libiconv gettext xz …                           |
| 共享（`.dll` + import lib） | 14   | glib(-base) gobject-introspection libffi cairo fontconfig freetype harfbuzz(-base) pango gdk-pixbuf graphene gtk libadwaita |

`libffi` 保留动态的原因特殊：其 `ffi_type_*` 是**按地址比较**的数据符号，而 glib 与
gobject-introspection 两个 DLL 都会用；静态化会让各 DLL 各嵌一份副本，跨 DLL 指针
比较必然不等（girepository 的 callable-info 测试即对此断言）。
`cairo`/`pango` 都链接 `freetype`/`fontconfig`，静态化会使两者各持一份拷贝而
`FT_Face`/`FcPattern` 跨边界传递，故这三者一并保持动态。

> 媒体链（gstreamer/gst-plugins-*）同样必须动态，理由见 [NOTES.md](NOTES.md)。

### 静态消费（pkg-config --static）

静态库不携带依赖信息：传递依赖与静态消费宏只写在 `.pc` 的
`Requires.private`/`Libs.private`/`Cflags.private`，仅在 `pkg-config --static` 时返回。
框架在**每个静态包安装后**自动把这三个私有字段并入同名公开字段（幂等，见
`gtkcross/builder.py` 的 `publish_pc_private_fields`），因此**不带 `--static` 也能**从
`out/<target>/lib/pkgconfig` 取到完整参数：

```bash
export PKG_CONFIG_LIBDIR="$PWD/out/msys2-ucrt64/lib/pkgconfig;$PWD/out/msys2-ucrt64/share/pkgconfig"
pkg-config --libs libpng16        # -> -L…/lib -lpng16 -lz -lm
pkg-config --cflags expat         # -> -I…/include -DXML_STATIC
```

> 注：`PKG_CONFIG_LIBDIR` 多目录在 Windows 上必须用**分号**分隔（MSYS2 的 pkgconf 是
> 原生 Windows 程序，冒号会被当作路径的一部分）。

之所以不依赖 meson 的 `prefer_static: true` 来带出这些字段：introspection 链各包
在 sysroot 里只有导入库（`libfoo.dll.a`），一旦让 `dependency()` 优先找 `.a`，
meson 会在编译器默认搜索目录里找到 **MSYS2 系统静态库**（如
`C:/msys64/ucrt64/lib/libglib-2.0.a`）——既不 hermetic，符号也对不上
（`undefined reference to __imp_g_free`）。因此 `prefer_static: false`，静态消费
宏改由上述 `.pc` 字段提升提供。

## 目标矩阵

target（构建档案）命名约定 = 工具链标识（`宿主-工具链[-运行时]`），与
`toolchains/*.yaml` 文件名一一对应，便于未来区分 msvc、linux 交叉等工具链。

| target                                   | 状态    | 说明                                                                     |
| ---------------------------------------- | ------- | ------------------------------------------------------------------------ |
| msys2-mingw64（MSYS2 MINGW64 原生）      | ✅ 完成 | 39 个 recipe 全链构建 + 自带测试                                         |
| msys2-ucrt64（MSYS2 UCRT64 原生）        | ✅ 完成 | 同 recipe 复用；UCRT 运行时，独立 sysroot（全链 39 recipe + 测试已复验） |
| linux-x64（Linux 原生）                  | 规划    | 同 recipe 复用                                                           |
| linux-musl-x64 / linux-mingw-x64（交叉） | 规划    | meson cross-file + exe_wrapper                                           |

> msys2-mingw64 与 msys2-ucrt64 同为 win64 输出平台（x86_64-w64-mingw32 三元组，
> toolchain 的 `platform: win64` 字段标注），区别在 CRT 运行时（msvcrt.dll vs
> ucrtbase.dll，后者 Windows 10+ 自带）：工具链分别来自 `mingw-w64-x86_64-*`
> 与 `mingw-w64-ucrt-x86_64-*` 系统包，PATH 中的子系统目录由 toolchain 的
> `msystem` 派生（框架无硬编码），产物隔离在 `out/msys2-mingw64` /
> `out/msys2-ucrt64`。两个 target 的已知失败**可以不同**：recipe 的
> `targets:` 块按目标覆盖（`Recipe.for_target`），因为 CRT 能力本身不同——
> 最典型的是 msvcrt 没有 `en_US.UTF-8` 这个 POSIX 区域名（见 [NOTES.md](NOTES.md)
> 的 libadwaita mingw64 条目）。

## 快速开始（msys2-mingw64 / msys2-ucrt64）

```bash
# MSYS2 环境，Python 3.12+（需安装 PyYAML 和 setuptools）
python -m gtkcross doctor -t msys2-ucrt64      # 环境自检
python -m gtkcross build gtk -t msys2-ucrt64   # 构建单包（自动拉取依赖链）
python -m gtkcross build libadwaita -t msys2-ucrt64 -j8   # 全链（幂等，stamp 续跑）
python -m gtkcross graph -t msys2-ucrt64       # 依赖图
python -m gtkcross list -t msys2-ucrt64        # recipe 清单
# 不带 -t 时回退到 gtk-cross.yaml 的 default_target（当前 msys2-mingw64）
# msys2-mingw64：以上命令把 -t 换成 -t msys2-mingw64（需 mingw64 工具链包）
```

构建产物在 `out/<target>/`（bin/lib/include/pkgconfig 齐全），构建中间态在
`build/<target>/<recipe>/`（configure/compile/install/test 四类 stamp 断点续跑）。

## 结构

```
gtk-cross.yaml              项目配置
toolchains/*.yaml           各目标工具链描述（MSYSTEM、host_triple、工具映射）
recipes/*.yaml              依赖 recipe（声明式：源、构建引擎、选项、测试）
patches/*.patch             recipe 源码补丁（解包后自动应用）
gtkcross/                   Python 框架（config/recipe/resolver/download/toolchain/engines/builder/events/cli）
versions.lock.yaml          sha256 版本锁定（首次 fetch 自动锁定，换源需手动改）
gtkcross-build.log          最近一次 build 的完整输出（含 bash/cmake 等子进程输出）
gtkcross-events.log         追加式事件日志（跨 run 持久，记录异常事件）
out/<target>/               可复现 sysroot
NOTES.md                    调查记录与笔记（本文件的记录性附录）
```

### recipe 示例（关键字段）

```yaml
name: glib
version: 2.90.0
source:
  url: https://download.gnome.org/sources/glib/2.90/glib-2.90.0.tar.xz
build: meson # meson | cmake | autotools
deps: [zlib, libffi, pcre2, libiconv, gettext]
meson:
  options: [-Dtests=true, ...]
patches: [xxx.patch] # 解包后经 patch -p1 应用
test:
  enabled: true
  # 项目自带测试中已确认的失败（构建时显示但忽略，未知失败才报错）
  known_failures: [glib:private, ...]
```

## 验证原则

以**编译通过 + 项目自带测试**为准（不额外自造验证程序）：

- meson 工程在 install 后跑 `meson test`（cmake 跑 `ctest`），结果按测试名与
  `known_failures` 比对：已知失败打印并忽略，未知失败使构建失败。
- 运行一个 recipe 的测试：`gtkcross build <pkg> && rm -f build/<t>/<pkg>/done/test.stamp && gtkcross build <pkg>`
- 端到端冒烟：`tests/libadwaita-demo` 经 `pkg-config` 链接 sysroot 产物构建并跑
  `--smoke`（GTK+libadwaita 窗口 2 秒后自动退出，退出码 0 即通过）。
  ```bash
  cd tests/libadwaita-demo
  export PKG_CONFIG_LIBDIR="$PWD/../../out/msys2-ucrt64/lib/pkgconfig:$PWD/../../out/msys2-ucrt64/share/pkgconfig"
  meson setup builddir && meson compile -C builddir
  PATH=../../out/msys2-ucrt64/bin:$PATH XDG_DATA_DIRS=../../out/msys2-ucrt64/share \
      ./builddir/libadwaita-demo.exe --smoke
  ```

当前逐包测试结果、已知失败与各自根因见 [NOTES.md](NOTES.md)。

### 构建日志与事件日志

- `gtkcross-build.log`：每次 `gtkcross build` 的完整输出快照（bash/meson/gcc
  等子进程输出经管道中继落盘，超时中断也不丢已产生的部分）。
- `gtkcross-events.log`：追加式事件日志（跨 run 持久，含 target 名），记录
  异常事件，行格式 `{时间} [{target}] {类型}: {内容}`。事件类型：

  | 类型                                  | 含义                                               |
  | ------------------------------------- | -------------------------------------------------- |
  | `run-start` / `run-ok` / `run-failed` | 一次 build 的起止与结局                            |
  | `recipe-failed`                       | 单个 recipe 构建失败的异常摘要                     |
  | `tests-pass`                          | 测试全部通过（附 OK 数）                           |
  | `tests-known-observed`                | known_failures 如期出现（正常，留档核对）          |
  | `tests-known-absent`                  | **预期失败未出现**（测试转好或环境变化，值得留意） |
  | `tests-unexpected`                    | 未知失败（构建会因此报错）                         |
  | `tests-exit-anomaly`                  | 退出码非零但解析不到失败行（输出异常）             |

### 在 Windows 上运行/测试 sysroot 产物的环境要求

- **PATH**：把 sysroot 的 `bin` 前置，以找到自建 DLL。
- **XDG_DATA_DIRS**：指向 sysroot 的 `share`，即
  `XDG_DATA_DIRS=$PWD/out/<target>/share`。
  原因：MSYS2 登录 shell 由 `/etc/profile.d/000-msys2.sh` 导出
  `XDG_DATA_DIRS`（指向 MSYS2 各前缀的 share）；而 Windows 下 glib 对
  **非空**的 `XDG_DATA_DIRS` 排他使用（`g_build_system_data_dirs()`），
  完全跳过「按 libglib DLL / exe 所在目录定位 share」的回退逻辑。
  两头都不含 sysroot 的 `share/glib-2.0/schemas/gschemas.compiled`，
  GSettings schema source 便整体为 NULL，报
  `g_settings_schema_source_lookup: assertion 'source != NULL' failed`。
  备选：`GSETTINGS_SCHEMA_DIR` 直接指向 `share/glib-2.0/schemas` 目录。
  注：MSYS2 bash 启动原生 exe 时自动把 POSIX 路径转为 Windows 形式，
  无需手动 cygpath。
  框架侧已修复：gtkcross 构建命令（含 meson test）统一注入
  `XDG_DATA_DIRS=$SYSROOT/share`；手动运行产物时需自行设置。

## 版本与来源说明

- 版本对齐 MSYS2 当前包（`pacman -Si mingw-w64-x86_64-<pkg>`）与 gvsbuild，
  定期以 MSYS2 仓库为参照升级；`versions.lock.yaml` 锁定每个 recipe 的 sha256。
- appstream 链（libxml2 → libxmlb → libfyaml → curl → appstream，另有 xz）自
  libadwaita **1.10.0 起已脱离依赖闭包**：upstream 把 appstream 换成 vendored 的
  **ministream**（tarball 内含完整源码，以 `install-profile=vendored-no-excludelibs`
  - `default_library=static` 内置静态链接，不安装、不产出额外 DLL，测试数 408 → 430）。
    这些 recipe 仍保留在仓库中（可独立构建），但 `build libadwaita` 不再触及；
    `patches/libadwaita-0001-remove-appstream.patch` 随之彻底失去用途。
    原 appstream 链的替代源记录：gitlab.freedesktop.org 归档有登录墙 → 改用
    Debian pool orig 包（pixman/cairo/libepoxy/appstream）；cairographics.org
    不可达 → Debian pool。
- 变更 recipe 版本后需同步 `versions.lock.yaml`（sha256），并清空对应
  `build/<target>/<recipe>` 重建（stamp 不感知版本变化）。

> 历次升级的完整说明（glib 2.90.0 / gtk 4.24.0 / libadwaita 1.10.0、静态化、
> TLS 修复、静态 vulkan loader 等）见 [NOTES.md](NOTES.md)。

### 当前版本清单（msys2-mingw64 / msys2-ucrt64）

| recipe         | 版本      |     | recipe                | 版本      |
| -------------- | --------- | --- | --------------------- | --------- |
| zlib           | 1.3.2     |     | fontconfig            | 2.18.3    |
| libffi         | 3.8.0     |     | libpng                | 1.6.58    |
| pcre2          | 10.47     |     | pixman                | 0.46.4    |
| libiconv       | 1.19      |     | libjpeg-turbo         | 3.2.0     |
| gettext        | 0.24      |     | libtiff               | 4.7.2     |
| glib           | 2.90.0    |     | cairo                 | 1.18.4    |
| expat          | 2.8.3     |     | pango                 | 1.58.2    |
| freetype       | 2.14.3    |     | gdk-pixbuf            | 2.44.7    |
| harfbuzz       | 14.3.1    |     | graphene              | 1.10.8    |
| fribidi        | 1.0.16    |     | json-glib             | 1.10.8    |
| libepoxy       | 1.5.10    |     | gtk                   | 4.24.0    |
| libadwaita     | 1.10.0    |     | gobject-introspection | 1.86.0    |
| vulkan-headers | 1.4.357.0 |     | vulkan-loader         | 1.4.357.0 |
| spirv-headers  | 1.4.357.0 |     | spirv-tools           | 1.4.357.0 |
| glslang        | 1.4.357.0 |     | shaderc               | 2026.3    |
| libxml2        | 2.15.3    |     | libxmlb               | 0.3.28    |
| libfyaml       | 0.9.5     |     | curl                  | 8.21.0    |
| appstream      | 1.1.6     |     | directx-headers       | 1.611.0   |
| xz (liblzma)   | 5.8.1     |     |                       |           |

## 当前已完成链（msys2-mingw64 / msys2-ucrt64，52 recipes）

libadwaita 依赖闭包为 **42 recipe**（2026-09-27 起媒体链并入，33 → 42）；下列
appstream 链与运行期数据包不在闭包内（见版本说明）。

zlib → libffi → pcre2 → libiconv → gettext → glib-base
└→ expat / freetype → fontconfig → harfbuzz(-base) → fribidi → pixman → libpng →
libjpeg-turbo → libtiff → cairo → gobject-introspection → glib（两段式）→
pango → gdk-pixbuf → graphene → json-glib → libepoxy → directx-headers →
vulkan-loader → libogg → libopus / libvorbis → gstreamer → gl-headers →
gst-plugins-base → directxmath → gst-plugins-bad → gtk → libadwaita
├→ vulkan-headers → vulkan-loader；spirv-headers → spirv-tools → glslang → shaderc
├→ 运行期数据包（不参与编译，独立 recipe）：shared-mime-info → share/mime +
   mime.cache；adwaita-icon-theme → share/icons/Adwaita + icon-theme.cache
└→ 独立（不在 libadwaita 闭包内）：libxml2 → xz(liblzma) → libxmlb → libfyaml →
curl（schannel）→ appstream

（GTK4 构建配置（2026-09-27 起）：win32 后端；vulkan=enabled、
introspection=enabled、**media-gstreamer=enabled**、build-demos=true
（gtk4-demo / gtk4-widget-factory）；仍禁 x11/wayland。SPIRV-Tools/shaderc 的
tag 归档不含 git submodule，由框架 `submodules` 字段从已构建依赖源码树自动
填充 `external/`、`third_party/` 目录；gst-plugins-base 的 gl-headers 子项目
同样如此（上游 wrap 是 revision=master 的 wrap-git，未固定版本）。）

媒体链各包的构建开关、EGL 现状与逐次验证范围见 [NOTES.md](NOTES.md)。
