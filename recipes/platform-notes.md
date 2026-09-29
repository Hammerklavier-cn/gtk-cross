# recipe 的平台分平台事实（为什么某项在 base、某项在家族块）

框架按 `toolchains/*.yaml` 的 `target_os`（`windows` | `linux`）匹配 recipe 里
`targets:` 的选择器；机制与合并语义见 `gtkcross/recipe.py` 的模块 docstring，
补丁逐条归属见 `patches/README.md`。本文件记录**判断依据**，避免下一个人重新
推断一遍。

约定：**base 只放平台中立项**，平台专属内容一律进 `for: [windows]` /
`for: [linux]` 家族块；只有"某个 target 与其他同族 target 不同"时才用精确
target 名（现存唯一例子：msvcrt 的区域名问题只影响 msys2-mingw64）。

## 已门控到 windows 的项

| recipe | 项 | 为什么是 Windows 专属 |
| --- | --- | --- |
| glib / glib-base / cairo | `-Dc_link_args=-Wl,--undefined=_tls_used` | `_tls_used` 是 PE 的 TLS 目录锚点；ELF 上没有这个符号，Linux 上注入会让**所有**链接报 undefined reference |
| harfbuzz / harfbuzz-base | `-Ddirectwrite=enabled` | 实测上游 meson.build 整段以 `host_machine.system() == 'windows'` 为门（Linux 上该选项被静默忽略，但它是"Windows 配置全局启用"的典型，必须显式归位） |
| gst-plugins-base | `-Dgl_winsys=win32 -Dgl_platform=wgl` | 上游合法取值（实测 meson.options）：winsys 含 `win32`、platform 含 `wgl`，都是 Windows 专有；Linux 侧对应 `x11` + `glx` |
| gst-plugins-bad | `-Dd3d11=enabled -Dd3d12=enabled` + deps `directx-headers`/`directxmath` + 补丁 | D3D11/D3D12 只在 Windows 存在；GTK 要的 `gstreamer-d3d12-1.0.pc` 也是 Windows 侧需求 |
| gst-plugins-good | `-Ddirectsound=enabled` | DirectSound 是 Windows 音频后端 |
| gtk | `-Dx11-backend=false -Dwayland-backend=false -Dbroadway-backend=false` + dep `directx-headers` | Windows 只要 win32 后端（上游在非 win32 宿主自动关 win32，故不必反向写 `-Dwin32-backend=false`）；DirectX-Headers 供 gdk/win32 的 d3d12 纹理路径 |
| gtk | 补丁 `gtk-0001-fallback-to-windows-locale.patch` | **精确 target 块**（msys2-mingw64）：msvcrt 不认 `en_US.UTF-8`，ucrt64 认——同族两个 target 结论不同 |
| vulkan-loader | 补丁 `vulkan-loader-0001-static-library-support.patch` | 补丁只改上游 WIN32 分支；Linux 分支是 `add_library(vulkan SHARED)` |
| libvpx | `--target=x86_64-win64-gcc` | libvpx 自己的 `<arch>-<os>-<toolchain>` 命名；Linux 侧给 `--target=x86_64-linux-gcc` |
| curl | `CURL_USE_SCHANNEL=ON` | schannel = Windows SSPI；Linux 侧显式 `OFF`（无 TLS 后端） |
| gobject-introspection | `test.env GI_SCANNER_DISABLE_CACHE=1` + `known_failures: test_scanner.py` | 前者是 Windows 上 `shutil.move` 不能原子替换导致的并发竞争；后者断言的是盘符语义 |
| shared-mime-info | `meson.env GETTEXTDATADIR=$SRC/data` + 补丁 | 前者绕开盘符冒号把 GETTEXTDATADIRS 切碎；后者绕开 Windows 的裸名 bash 搜索顺序 |
| fontconfig / pango / zlib | 见 `patches/README.md` | 补丁正文即 Windows 分支 |
| pango | `test.known_failures: test-font / test-fonts / test-font-data` | 三项失败登记的理由是 **Windows 宿主字体集**（Cantarell 变体缺失、fontconfig 排序平序、boxes.ttf 回退字体派生度量）。Linux 有自己的字体环境，照搬会掩盖真实回归——**实测复核**：Linux 首跑这三项全绿（junit 348 子测试 failures=0），Linux 侧一个登记都不需要 |

## 已门控到 linux 的项

反方向同样要门：这些条目在 Windows 上是空操作、甚至有害，所以只写进
`for: [linux]`。它们全部来自实测失败，没有一条是预防性配置。

| recipe | 项 | 为什么只在 Linux 生效 |
| --- | --- | --- |
| gdk-pixbuf | deps `shared-mime-info` | 上游 `src/meson.build:208` 的条件是 `host_system not in ['windows','darwin','android']`，Windows 根本不进这段；Linux 上那句 `dependency('shared-mime-info')` **没有** `required: false`，缺 `.pc` 直接中止 configure |
| gdk-pixbuf | `-Dglycin=disabled` | `get_option('glycin').enable_auto_if(system == 'linux')` **只在 Linux** 把该 feature 从 `auto` 提成 `enabled`（Windows 上 `auto` 找不到就静默跳过），于是 Glycin 变硬依赖。glycin 是 Rust 的 loader daemon，上游无 wrap 回退，而框架的引擎表只有 meson/cmake/autotools（`gtkcross/builder.py`），没有 cargo 路径 ⇒ 不可自建；关掉后 png/jpeg/tiff/gif 走 `disable_auto_if(glycin_dep.found())` 反分支，仍用本闭包自建库 |
| gst-plugins-base | `-Dx11=enabled` | 与上一条正好相反的方向，也是本轮踩到的坑：base 里有 `-Dauto_features=disabled`（插件集刻意精简），把 `x11` 这个 `value:'auto'` 的 **feature 选项**一并关了 → `src/meson.build:336` 的 `dependency('x11', required: get_option('x11'))` 整条被跳过（日志原文 `Dependency x11 for host machine skipped: feature x11 disabled`），于是即便写了 `gl_winsys=x11`，到 `gst-libs/gst/gl/meson.build:712-741` 仍 `error('Could not find requested X11 libraries')`。Windows 上这个 feature 本来就不该开，故留在 linux 家族块 |
| libxml2 | `-DIconv_IS_BUILT_IN=OFF` | 见第 9 条的 CMake 探测不对称。mingw 上那次探测自然失败、结论本来就对，加这个 define 反而多余 |
| libffi | `--disable-multi-os-directory` | libtool 的 `multi_os_directory` 取自 `gcc -print-multi-os-directory`，Linux 返回 `../lib64` ⇒ 产物装进 `lib64/`，破坏"sysroot 只有一个 lib 目录"。PE 上没有这套布局 |
| glib | `test.env LC_ALL: C.UTF-8` | sysroot 会把自己 `po/` 的产物装进 `share/locale/zh_CN/.../glib20.mo`，宿主 `LANG=zh_CN.UTF-8` 时诊断被翻译，而 `spawn-test.c` 断言 `strstr(erroutput, g_strerror(ENOENT))` ⇒ 必不匹配。Windows 的 CRT 不走这条 gettext locale 路径 |

## 留在 base 的"看着像 Windows 但其实是跨平台决策"

这些容易被误门控，**不要**挪进 windows 块：

- `libpng-0001` / `spirv-tools-0001` / `shaderc-0001` 三个补丁：动机是
  `default_library: static`（本项目两平台共用同一形态），不是 Windows。
- glib/glib-base 的 `-Dselinux=disabled -Dlibmount=disabled -Dlibelf=disabled
  -Dsysprof=disabled`、gtk 的 `-Dsysprof/-Dcloudproviders/-Dtracker/-Dcolord/
  -Dprint-cups=disabled`、appstream 的 `-Dsystemd=false`、vulkan-loader 的
  `BUILD_WSI_*=OFF`、gstreamer 的 `-Dlibunwind/-Dlibdw/-Ddbghelp=disabled`：
  这些特性在 Windows 上是空操作、在 Linux 上**真实存在**，关掉是"不引入系统
  依赖、保持 hermetic"的主动裁剪，两平台同值。Linux 侧的代价已在
  vulkan-loader recipe 里写明（不导出 xlib/xcb/wayland surface 扩展 → Vulkan
  渲染器无法呈现到窗口）。
  其中 `-Ddbghelp=disabled` 值得单说：`dbghelp` 是 Windows 独有的回溯来源，
  Linux 上这个选项存在但无对应实现，所以它是"显式声明不要"而非"平台专属配置"，
  与 `-Dlibunwind/-Dlibdw=disabled`（Linux 独有）成对留在 base 更连贯。
- 各 recipe 的 `default_library: shared` 例外（glib/cairo/pango/gtk/gstreamer
  链/libffi）：理由文案里满是"DLL"字眼，但同样的机理对 `.so` 成立
  （g-ir-scanner 无法 introspect 静态库、typelib 运行期 dlopen、插件按地址
  比较的全局单例）。Linux 沿用同一套形态，一个都不要改成 static。
- `prefer_static: false` 与 `.pc` 私有字段提升（`publish_pc_private_fields`）：
  Windows 的理由是"会摸到 MSYS2 系统静态库"，Linux 同理会摸到
  `/usr/lib64/libglib-2.0.a`，两平台都保持 false。
- 源码 URL 里的 Debian pool / codeload 替换：那是当时那台机器的**网络可达性**
  问题，与平台无关，Linux 上不要"顺手改回上游"。
- `PYTHONUTF8=1`：只在 msys2-* 的 toolchain env 里设（Windows 代码页问题），
  linux-x64 不设；glib 的 `glib:mkenums.py` 等在 Linux 上本就不受影响。

## Linux 目标的结构性差异（不是"补丁"，是平台事实）

1. **必须 PIC**：静态 `.a` 会被链进 `.so`，所以 toolchain prelude 对非 Windows
   目标注入 `CFLAGS/CXXFLAGS=-fPIC`。不加的症状是链接共享库时报
   `relocation R_X86_64_32 ... can not be used when making a shared object`。
2. **不传 `--host/--build`**：`toolchains/linux-x64.yaml` 故意**不写**
   `host_triple`。autotools 的 `--host=` 是给交叉编译用的，喂一个"看着不同其实
   相同"的三元组会让 autoconf 误判为交叉，改写若干运行期探测。
3. **XDG_DATA_DIRS 是前置而非独占**：Windows 上 glib 对非空 `XDG_DATA_DIRS`
   排他使用，所以框架把它指到 `$SYSROOT/share` 单个目录；Linux 上只给 sysroot
   会丢掉 `/usr/share`（hicolor 图标主题等），故 Linux 侧写成
   `$SYSROOT/share:/usr/local/share:/usr/share`。
4. **不注入 MSYSTEM**：以前 `Toolchain.run()` 无条件写 `MSYSTEM=MINGW64`，
   Linux 宿主上的上游构建脚本会被这个假信号误导（它们用 `test -n "$MSYSTEM"`
   判定 MSYS2）。现在只有 `host: windows` 才注入。
5. **`.pc` 静态判定按平台看共享变体**：Windows 查 `libNAME.dll.a`，ELF 查
   `libNAME.so`（`Toolchain.shared_suffixes`）。硬编码 `.dll.a` 会让 Linux 上
   的共享包 `.pc` 被误判成静态并把 `Libs.private` 提升进 `Libs`。
6. **`-lm` 要显式给（Linux 专属）**：libm 属 C 运行时，不在自建 sysroot 里，而
   CMake 的 `CMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY`（本项目用来保证 hermetic）
   会让 `find_library(m)` 查不到。实测后果：libtiff 4.7.2 的
   `cmake/FindCMath.cmake` 两次探测 `pow` 都失败 →
   `find_package(CMath REQUIRED)` 报 Configuring incomplete。mingw 上同一探测
   本来就过（spec 文件默认链 libmingwex）。**不要**因此把 MODE_LIBRARY 放宽成
   BOTH——那才是真的 hermetic 破口（包会嗅到发行版的 libjpeg/libpng）。
7. **产物要带 RUNPATH**（`-Wl,-rpath,$SYSROOT/lib`）：ELF 上没有"可执行文件所在
   目录天然参与 DLL 搜索"这回事，凡是用**清洗过的环境**启动子进程的上游测试
   （glib 的 gschema-compile / gsubprocess）都会找不到自建库。实测
   `env -i out/linux-x64/bin/glib-compile-schemas --version` 可正常输出，说明
   闭包自定位成立，消费者也不必再设 `LD_LIBRARY_PATH`。
8. **GNU libiconv / gettext 仍在 Linux 闭包内**（本轮明确决定：与 Windows
   保持**完全同一 closure**，不因 glibc 自带 iconv/libintl 就裁掉）。风险已
   实测确认，并且**没有**用"裁依赖"绕开，而是补齐 ELF 侧应有的链接参数：
   - sysroot 的 `include/iconv.h` 遮蔽 glibc 同名头（`CPPFLAGS` 把
     `-I$SYSROOT/include` 前置），实测 `tests/iconv_check.c` 只带
     `-I$SYSROOT/include -L$SYSROOT/lib` 编译时**链接失败**：
     `undefined reference to libiconv_open / libiconv_close`
     ——GNU libiconv 把符号改名成 `libiconv_*`，宏重写了 `iconv_open`。
     结论：Linux 上一旦**看见** sysroot 的 `iconv.h`，就必须自己给 `-liconv`，
     不能指望 glibc 的隐式 iconv 符号。
   - 但要注意两包在 ELF 上的**实际作用并不对称**（实测 `out/linux-x64`）：
     - libiconv 是"活的"：`include/iconv.h` 遮蔽 glibc 头，`libglib-2.0.so` 的
       `DT_NEEDED` 是 `libiconv.so.2` + `libc.so.6`。
     - gettext 基本是"惰"的：它不装 `libintl.h`、也不装 `libintl.so`（sysroot 里
       只有 `preloadable_libintl.so` 与 `autosprintf.h`/`libcharset.h`/
       `localcharset.h`），glib 的 NLS 于是走 glibc 自带的 libintl —— 与 Windows
       上"必须自建 libintl-8.dll"的处境不同，但**闭包成员一致**（这是本轮的决定）。
   - **消费者拿不到 `-liconv` 的 pkg-config 路径**：GNU libiconv 不安装 `.pc`
     （实测 sysroot 的 `lib/pkgconfig` 里没有 iconv.pc / intl.pc）。所以
     `pkg-config --exists iconv` 在 Linux 上必然失败；需要它的人要么手写
     `-liconv`，要么用 `pkg-config --static --libs glib-2.0`（glib 的
     `Libs.private` 里带 `-liconv`，静态消费时由 `publish_pc_private_fields`
     提升的只有共享包以外的项，glib 是共享包故不提升）。链接共享 glib 的消费者
     不受影响：`libiconv.so.2` 由 `libglib-2.0.so` 的 DT_NEEDED 带着，运行期
     经 RUNPATH 解析。
   - 于是 `libglib-2.0.so` 的 DT_NEEDED 里带 `libiconv.so.2`，需要
     `-Wl,-rpath-link`（链接期）与 `-Wl,-rpath`（运行期自定位）两条参数，
     见 `gtkcross/toolchain.py` 的 prelude 注释。
   - 顺带的收益：闭包产物自定位后，`out/linux-x64` 里的工具与库在**不设
     `LD_LIBRARY_PATH`** 时也能运行（glib 自带测试里有用空环境 execve 的子进程，
     正是这条救回了 3 项测试）。
   - 如果将来决定改用 glibc 原生 iconv/gettext，做法是给 glib-base / libxml2 的
     `deps` 加 `for: [windows]` 块并从 base 移走，而不是在 Linux 上继续留着
     这两个包又指望它们不被头文件遮蔽。
9. **CMake 的自动探测看不见 `CPPFLAGS`**（第 8 条的第二笔代价，实测于 libxml2）：
   CMake 只从环境取 `CFLAGS`/`CXXFLAGS`/`LDFLAGS`，**不取 `CPPFLAGS`**。本框架把
   sysroot 头目录放在 `CPPFLAGS` 里（Meson/Autotools 都认它），于是 CMake 工程的
   `try_compile`/`check_*` 探测一律在**宿主头文件**上求值，而真正编译时用的是
   target 的 include 目录（受 `CMAKE_FIND_ROOT_PATH` + `MODE_INCLUDE=ONLY` 约束，
   命中 sysroot）。两侧结论可以相反：libxml2 的 `FindIconv` 就是这样判成
   "iconv built in to C library"（看到 glibc 的头），编译 `encoding.c` 时却按
   GNU libiconv 的宏改写引用 `libiconv_open`，链接期又没有 `-liconv` ⇒
   `undefined reference to libiconv_open`。
   **通用做法**：给这类探测显式定义结论（本项目用 `Iconv_IS_BUILT_IN=OFF`，
   条件正是 FindIconv 自己的 `if(NOT DEFINED …)`），而不是去放宽 find-root 模式。
   同类坑还要留意别的 CMake 工程的 `check_symbol_exists`/`find_library` 组合。
10. **上游会按 `host_machine.system()` 分叉"必需依赖"**，这是最容易漏的一类
    Linux-only 阻塞（实测两处，都在 gdk-pixbuf）：
    - 直接写死分支：`if host_system not in ['windows','darwin','android']`
      里 `dependency('shared-mime-info')`（无 `required: false`）；
    - 更隐蔽的是 feature 选项被提级：`get_option('glycin')` 默认 `auto`，
      但外面包了 `.enable_auto_if(system == 'linux')` ⇒ 只有 Linux 把 `auto`
      变成 `enabled`，`required:` 随之变真。
    两种在 Windows 上都不会暴露（前者不进分支、后者静默跳过），所以只能靠
    Linux 首跑把它撞出来。判据：configure 报 `Dependency "X" not found` 时，
    先读该 `dependency()` 的 `required` 实参是不是 feature/条件表达式，再决定
    "补 dep"还是"显式 disabled"——前者是上游的功能需求，后者是本项目的边界裁剪。

## linux-x64 当前打通到哪里

**全链已通**（2026-09-29）：`build libadwaita -t linux-x64` 闭包 **62 recipe**
全部构建、安装、测试通过，`exit 0`，重跑幂等。自带测试 0 失败：
expat 1、libpng 37、GI 65、glib 424 项（418 通过 + 6 跳过）、fribidi 8、
pango 29 项（27 通过 + 2 跳过）、gdk-pixbuf 23、shared-mime-info 8、gtk 1
（上游大测试套件由 `-Dbuild-testsuite=false` 关着，两平台同形态）、
libadwaita 68 个测试程序 = **430 个子用例**（与 Windows 记录的 430 是同一集合）。
**Linux 侧没有登记任何 `known_failures`**。

端到端（都实测过）：

- `tests/libadwaita-demo` 只靠 `PKG_CONFIG_LIBDIR` 指向 sysroot 即可配置编译；
  在 `env -i`（不设 `LD_LIBRARY_PATH`）下经**自建 gtk4-broadwayd** 跑 `--smoke`
  退出码 0。坑：broadway 的 socket 在 `XDG_RUNTIME_DIR` 里，server 与 app
  必须共用同一个目录，否则报 `Failed to open display`。
- 只链接 sysroot 头/库的 C 程序可跑通 X11 与 GLX：`libX11` 连上 X.Org、
  `XRRQueryVersion` 得 1.6、`XineramaQueryExtension` 为真、`glXQueryVersion`
  得 **1.4（来自自建 Mesa 的 libGL）**。X 客户端库按本项目的静态优先策略
  链进产物，所以 `readelf -d` 里只出现 `libGL.so.1` 这一条 NEEDED。

闭包差异（两个方向都钉在 `tests/test_platform_gating.py`）：Windows 42
（含 `directx-headers`/`directxmath`/`egl-headers`），linux-x64 62
（含 X11 栈 17 个 + `mesa`/`libdrm`/`libpciaccess`/`libxshmfence` +
`libxml2`/`shared-mime-info`）。

## X11 客户端栈（2026-09-29 落地，全部从源码建）

新增 21 个 recipe：**X11 客户端栈 17 个** + **GL/EGL 侧 4 个**（mesa、libdrm、
libpciaccess、libxshmfence，见下一节）。X11 侧的构建顺序：

```
util-macros → xorgproto → xtrans → libpthread-stubs → xcb-proto → libxau
  → libxcb → libx11 → {libxext, libxfixes, libxrender} → {libxi, libxrandr,
  libxcursor, libxdamage, libxinerama, libxxf86vm}
```

版本对齐 **x.org 上游发布**（不是 MSYS2）：MSYS2 的 `mingw-w64-*libX11` 是走
Win32 API 的移植版，拿它当 Linux 侧版本基准是错的。实测取值：util-macros 1.20.2、
xorgproto 2025.1、xtrans 1.6.0、libpthread-stubs **0.5**（不是流传的 0.4，上游
索引里没有 0.4）、xcb-proto/libxcb 1.17.0、libXau 1.0.12、libX11 1.8.13、
libXext 1.3.7、libXfixes 6.0.2、libXrender 0.9.12、libXi 1.8.3、libXrandr 1.5.5、
libXcursor 1.2.3、libXdamage 1.1.7、libXinerama 1.1.6。

### 构建引擎按实测的归档内容选，不按印象

`tar tjf` 看根目录（同一 tarball 可能双构建）：

| 引擎 | 包 |
| --- | --- |
| 只有 `configure` | util-macros、libpthread-stubs、xcb-proto、**libxcb**、**libX11**、libXext、libXrender、libXi、libXcursor |
| `configure` + `meson.build` 都有 | xorgproto、libXau、libXfixes、libXrandr、libXdamage、libXinerama |

统一取 **autotools**：既然 libxcb/libX11 这些核心包只有 configure，`ACLOCAL_PATH`
那条路无论如何都要走，双引擎只增加核对面。xorgproto 的 meson 路线另有一个
`legacy` 开关（`meson_options.txt` 里唯一一项），autotools 路线用不到。

### 三条实测到的 X.org 特有事实

1. **`ACLOCAL_PATH=$SYSROOT/share/aclocal` 必须显式给**：X.org 各包自带的
   configure 脚本在**运行时**去找 `xorg-macros.m4` 校验宏版本，框架的
   `CPPFLAGS`/`PKG_CONFIG_LIBDIR` 都指不到那里，所以要
   `autotools.env: ACLOCAL_PATH`，并把 `util-macros` 列进每个 X 包的 deps
   （它是纯宏包，不产出库）。
2. **`xtrans` 是 libX11 的真实 pkg-config 依赖**（Xtrans 头文件已从 libX11 拆出）：
   实测 libX11 1.8.13 configure 那句是
   `Package requirements (xproto >= 7.0.25 xextproto xtrans xcb >= 1.11.1 kbproto inputproto)`，
   缺它报 `Package 'xtrans' not found`。旧说法"Xtrans 自 1.6 起 vendored 在
   libX11 里"对 1.8.x 不成立。
3. **缺文档工具只是 warning，不必加开关**：本机没装 xmlto/fop/doxygen/asciidoc，
   构建里只出现
   `configure: WARNING: xmlto not found - documentation targets will be skipped`
   这类提示，上游探测自动跳过，因此 X 栈的 recipe 一个 `--disable-*docs` 都不写。
   （对比：glib 那种"选项是 feature 且 required"的包就必须显式写。）

### 唯一的宿主例外：xcb-proto 找 Python

`xcb-proto` 的 configure 用 `PKG_CHECK_MODULES([PYTHON], [python-${PYTHON_VERSION}])`
定位**宿主 Python**。框架把 `PKG_CONFIG_LIBDIR` 整体替换成 sysroot 来保证
hermetic，宿主 Python 的 `.pc` 因此在默认搜索路径外。该 recipe 用
`autotools.env: PKG_CONFIG_PATH: /usr/lib64/pkgconfig` 放开（pkg-config 把它
**追加**到搜索路径，且只作用于这一条 configure 命令）。理由：Python 与
meson/ninja/perl 同类，属宿主构建工具，从不在 target 侧闭包里——
gobject-introspection 依赖的是同一个 `python3-devel`。
`xcbgen` 代码生成器随本包装进 `$SYSROOT/lib/python3.14/site-packages/`，
libxcb 构建期按 `xcb-proto.pc` 里的路径取用（实测目录存在）。

### X 栈装进 sysroot 后的形态

- 全部落 `lib/`，**没有** `lib64/`（X.org 用普通 `lib_LTLIBRARIES`，不走 libffi
  那套 `toolexeclibdir`/multi-os-directory 逻辑，故不需要 `--disable-multi-os-directory`）。
- libxcb 一次产出 24 个 `xcb-*.pc`；`pkg-config --modversion` 对
  x11/xext/xi/xrender/xrandr/xcursor/xdamage/xfixes/xinerama/xcb/xau/xtrans
  全部可解析。
- cairo 放开 X 后端后新增 `cairo-xlib.pc`、`cairo-xlib-xrender.pc`、
  `cairo-xcb.pc`、`cairo-xcb-shm.pc`（cairo 1.18.4 的
  `xlib`/`xcb` 是 feature 型选项，`xlib-xcb` 默认 disabled 保持不动；
  老 cairo 的 `xlib-xrender` 选项名在本版本**不存在**）。
- GTK 的 x11 依赖清单与版本号取自 `gtk-4.24.0/meson.build`：`:599-608`
  （xrandr>=1.2.99、x11、xrender、xi、xext、xcursor、xdamage、xfixes、fontconfig）、
  `:610` 的 `x11_pkgs`（含 xinerama）、`:657` 起还会
  `cc.has_header_symbol('X11/extensions/Xinerama.h','XineramaQueryExtension')`
  实测符号，不通过就 `error('X11 backend enabled, but Xinerama extension does not work.')`

## Mesa（GL/EGL 实现）为什么进闭包，以及为什么不要 LLVM

2026-09-29 的决定：Linux 目标的 OpenGL 实现**自建 Mesa**，不取宿主包。
记录在这个决定上的是三条实测事实，不是偏好：

1. **没有"只借个头和链接名"的中间路**。上游链条是硬的：
   - `gst-plugins-base/src/gst-libs/gst/gl/meson.build:355-368` 在非 Windows 走
     `cc.find_library('GL')` + `cc.has_header('GL/gl.h')`，且 recipe 显式要求
     `gl_api=opengl` ⇒ 缺任一即 `error('Could not find requested OpenGL library')`；
   - `gtk-4.24.0/meson.build:497-498` 的
     `dependency('gstreamer-gl-1.0', required: get_option('media-gstreamer'))`
     是硬要求 ⇒ 关 GL 等于关整个媒体后端（GtkMediaFile 一用就 g_error）；
   - 本机 `dnf repoquery --whatprovides "*/GL/gl.h"` 与 `"*/libGL.so"` 命中的都是
     **libglvnd-devel**（外加 mingw/ucrt 交叉头包）——即 GL 的头与链接名在 Linux
     上属"系统图形栈"，不像 Windows 那样天然属于工具链（mingw-w64 自带
     `GL/gl.h` + `libopengl32.a`，运行期实现是 OS 的 opengl32.dll）。
     所以"装个 -devel 包"在 Linux 上是把 GL ABI 交给宿主，与 Windows 的
     "工具链提供导入库"并不同构。
2. **softpipe 不需要 LLVM**，所以自建并不需要拖进 LLVM。判据取自源码：
   `meson.build` 里 `with_gallium_swrast = with_gallium_softpipe or
   with_gallium_llvmpipe`，而写着 "requires LLVM" 的只有 llvmpipe、i915、
   r300(IGP)、radeonsi、lavapipe（`gallium-drivers` 的候选列表里
   softpipe 与 llvmpipe 是两个独立项）。本机
   `llvm-config`/`llvm-devel` 都不存在，正是这条路线成立的前提（也不需要装）。
   代价：只有软件光栅化，没有硬件加速驱动路径（iris/radeonsi 那些才要 LLVM）。
3. **配置很便宜，构建也很快**：`-Dglx=dri -Degl=enabled -Dgles1/2=disabled
   -Dllvm=disabled -Dgallium-drivers=softpipe -Dvulkan-drivers= -Dvideo-codecs=
   -Dplatforms=x11`，本机 `-j8` 约 50 秒建完，产出 `libGL.so.1.2.0`、
   `libEGL.so.1.0.0`、`gl.pc`、`dri.pc`、`lib/dri/{swrast,kms_swrast,libdril}_dri.so`
   与 `GL/{gl,glx,glext}.h`、`EGL/egl.h`。

由此带来的两处归属调整：

- **EGL 归 Mesa，不归 egl-headers**。`egl-headers`（Khronos EGL-Registry）当初
  是为了不依赖 MSYS2 的 `mingw-w64-egl-headers` 而存在的，动机是 Windows 侧的；
  现在把它收进 libepoxy 的 `for: [windows]` deps，Linux 上由 Mesa 提供
  `EGL/egl.h` + `egl.pc`（`include/meson.build:62-68` 的 `install_headers`
  受 `with_egl` 控制，实测安装日志有这几行）。libepoxy 的 `-Degl` 也从
  base 的单值改成两平台分块（Windows 仍 `no`——那边没有可用的 libEGL 实现，
  开了会在只用 WGL 的路径上跳到 NULL，见该 recipe 里的 gdb 记录）。
- **`vulkan-loader` 的 WSI 仍旧关着**。base 里那批 `BUILD_WSI_*_OFF` 的代价
  已在 `recipes/vulkan-loader.yaml` 写明；现在 X11/xcb 已就位，放开 XCB/X11
  WSI 只剩"要不要"而没有"能不能"，留给下一轮实测。

## 框架侧的 Linux 专属隔离（不是 recipe 的事，但同属平台事实）

- `XDG_CONFIG_HOME=$SYSROOT/etc/xdg`（`gtkcross/toolchain.py` 的 prelude，
  仅非 Windows 目标）：构建/测试期不读开发机桌面配置。实测起因是 libadwaita
  首跑 68 项测试全部 SIGABRT，唯一报错是宿主
  `~/.config/gtk-4.0/settings.ini` 里一行 GTK4 已不认的 `gtk-modules`，
  而 libadwaita 的测试环境自带 `G_DEBUG=fatal-warnings` ⇒ warning 即致命。
  这与 `PKG_CONFIG_LIBDIR` 整体替换、`XDG_DATA_DIRS` 前置 sysroot 属同一类
  隔离；Windows 目标不动（那边现况全绿，且 APPDATA 语义不同）。
- 下载器的 IPv4 优先重试（`gtkcross/download.py` 的 `_prefer_ipv4`）：同一
  主机名的 IPv6 出口会被中间设备换成一张 subject 为空、SAN 只有 IP 的证书，
  `urllib` 走到那条就 `CERTIFICATE_VERIFY_FAILED: Hostname mismatch`
  （实测 `gstreamer.freedesktop.org`）；`curl` 检不出来因为它先试 IPv4。
  重试时只调 A/AAAA 顺序，不禁用 IPv6，IPv6-only 的机器不受影响。

## linux-x64 尚未打通的部分（下一轮）

- ~~X11 客户端栈（从源码补）~~ **已落地**（见上面的 X11 一节）。剩下的是
  **Wayland**，卡点现在比上一轮更具体：`gtk-4.24.0/meson.build:588` 的
  `wlegldep = dependency('wayland-egl')` **没有** `required: false`，GTK 自带
  subprojects 里也只有 `wayland.wrap` 与 `wayland-protocols.wrap`（实测清单）。
  `libwayland-egl` 属 Mesa 的 **wayland 平台**，而本轮只开了
  `-Dplatforms=x11` ⇒ 开 Wayland 要先决定"Mesa 加不加 wayland 平台"，并且这里
  有个顺序依赖：Mesa 的 wayland 前端要 wayland-client，GTK 的 wayland 后端要
  Mesa 的 wayland-egl，所以链条必须是
  `wayland → mesa(-Dplatforms=x11,wayland) → libxkbcommon(+xkeyboard-config)
  → wayland-protocols → gtk`。

- **GL/EGL 实现（mesa）不在计划内**：Windows 上 `opengl32.dll`/驱动同样是宿主
  提供，Linux 对应物是驱动的 `libGL.so.1`/`libEGL.so.1`；头文件由已有的
  `gl-headers`/`egl-headers` 覆盖，libepoxy 是 dlopen 的。这条决定直接后果是
  gst-plugins-base 的 `-Dgl_winsys=x11 -Dgl_platform=glx` 需要 `gl` 依赖
  （`gl.pc` 也来自 Mesa），故 Linux 侧 gst 的 GL 元素多半要显式关掉——
  本轮实跑确认。
  ↑ **这条上一轮的判断已被推翻并改正**：Mesa 最终**进了闭包**（见上面"Mesa"
  一节的三条实测依据），gst 的 GL 不但没关、还要 `-Dx11=enabled` 才配得起来。
  错在把"Windows 上 GL 由宿主提供"当成两平台同构的事实——Linux 上
  `GL/gl.h`+`libGL.so` 属系统图形栈（libglvnd-devel），不是工具链自带物，
  所以"用宿主"在这里等于交出 GL ABI，而"自建"反而更便宜。
  留着 `gl-headers` 是因为 gst 把它当**源码树**用（submodules 喂
  `subprojects/gl-headers` 的 GL 兼容头），与"谁提供 GL 头给编译器"是两回事。
- ~~到那一步需要同时放开 `gtk` 的 `-Dx11-backend=true`、`cairo` 的
  `-Dxlib/-Dxcb`、`gst-plugins-base` 的 `gl_winsys=x11/gl_platform=glx`~~
  **三项都已放开并实测通过**（gst 那条还多补了 `-Dx11=enabled`，见
  "已门控到 linux 的项"表）。只剩 `vulkan-loader` 的 XCB/X11 WSI：base 里那批
  `BUILD_WSI_*=OFF` 的代价已写在 `recipes/vulkan-loader.yaml`，如今 X11/xcb
  已就位，放开它只剩"要不要"而没有"能不能"。
- ~~GTK4 在 Linux 上还会拉 `at-spi2-core`（无障碍）~~ **这条推测被证伪**：
  在 `gtk-4.24.0` 的根 `meson.build` 里 grep `at-spi|dbus|accessibility`
  **零命中**（实测），4.24 的 AT-SPI 实现已在 GTK 树内，不需要外部 at-spi2-core
  与 dbus 库。留着这项会白白把两个包塞进闭包。
- ~~libepoxy 在 Linux 上能否构建尚未实测~~ **已实测：能建，而且 EGL 必须开**。
  上游 1.5.10 的 `build_egl = not ['windows','darwin'].contains(host_system)`
  ⇒ Linux 默认开，上一轮留在 base 的 `-Degl=no` 因此在 Linux 上是**主动裁剪**，
  本轮改成两平台分块。裁剪的后果不是"少一个后端"而是**编译不过**：GTK 的 x11
  后端无条件 `#include <epoxy/egl.h>`（`gdk/x11/gdkdisplay-x11.c:61`，没有
  `#ifdef`）并无条件把 `gdkglcontext-egl.c` 列进 sources
  （`gdk/x11/meson.build:11`），实测报
  `fatal error: epoxy/egl.h：没有那个文件或目录`。
  上一轮那条"GLX 侧 `dependency('x11', required: false)` 是可选依赖"的判断
  **成立**（libepoxy 在 X11 就位前也建得过，本轮日志里 `x11 found: YES 1.8.13`）。
- ~~Linux 侧各包的 `known_failures` 需按实测重新登记~~ **全链实测完毕，
  Linux 侧一项都不需要**：pango 的三项 Windows 字体登记在 Linux 全绿；gtk 1 项
  （上游大测试套件由 base 的 `-Dbuild-testsuite=false` 关着，两平台同形态，
  不是 Linux 被削弱）、libadwaita 68 个测试程序 / 430 个子用例全过。
- **图形测试依赖真实显示，这是无头 CI 的唯一硬缺口**（本轮更正上一轮的
  "只验证不依赖显示的部分"）：libadwaita 那 68 项是**在宿主的 X.Org 会话上
  真跑并且全过**的——框架把 `os.environ` 透传给子进程，于是继承 `DISPLAY`；
  证据是测试 stderr 里的 `MESA-EGL: warning: DRI3 error: Could not get DRI3
  device`，那只有连上真实 X server 才会出现。换到没有 `DISPLAY` 的 runner
  上它们不会通过。两条可选解法：装 `xorg-server-xvfb` 并用 `xvfb-run` 包住
  测试命令（需要框架加一个类似 `test.wrapper` 的声明），或让测试走自建
  broadway（要同时起 `gtk4-broadwayd`，且 server 与 app 共用同一个
  `XDG_RUNTIME_DIR`）。本轮**没有**引入该机制——现有证据全部来自真实会话，
  等做 CI 那一轮再按实跑结果定。

