# gtk-cross

从源码构建 GTK4 / libadwaita 及其全部依赖的可复现构建框架。

目标：除交叉编译器与宿主构建工具外，**所有 target 侧依赖库都从源码编译**，产出可复现的
sysroot（`prefix`/`lib`/`include`），供原生运行或交叉链接。

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

| 类别                  | 数量 | 内容                                                                                            |
| --------------------- | ---- | ----------------------------------------------------------------------------------------------- |
| 静态（仅 `.a`）       | 25   | zlib expat pcre2 libpng libjpeg-turbo libtiff curl libxml2 libxmlb libfyaml libiconv gettext xz … |
| 共享（`.dll` + import lib） | 14   | glib(-base) gobject-introspection libffi cairo fontconfig freetype harfbuzz(-base) pango gdk-pixbuf graphene gtk libadwaita |

`libffi` 保留动态的原因特殊：其 `ffi_type_*` 是**按地址比较**的数据符号，而 glib 与
gobject-introspection 两个 DLL 都会用；静态化会让各 DLL 各嵌一份副本，跨 DLL 指针
比较必然不等（girepository 的 callable-info 测试即对此断言）。
`cairo`/`pango` 都链接 `freetype`/`fontconfig`，静态化会使两者各持一份拷贝而
`FT_Face`/`FcPattern` 跨边界传递，故这三者一并保持动态。

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

| target                                   | 状态    | 说明                                                              |
| ---------------------------------------- | ------- | ----------------------------------------------------------------- |
| msys2-mingw64（MSYS2 MINGW64 原生）      | ✅ 完成 | 39 个 recipe 全链构建 + 自带测试                                  |
| msys2-ucrt64（MSYS2 UCRT64 原生）        | ✅ 完成 | 同 recipe 复用；UCRT 运行时，独立 sysroot（全链 39 recipe + 测试已复验） |
| linux-x64（Linux 原生）                  | 规划    | 同 recipe 复用                                                    |
| linux-musl-x64 / linux-mingw-x64（交叉） | 规划    | meson cross-file + exe_wrapper                                    |

> msys2-mingw64 与 msys2-ucrt64 同为 win64 输出平台（x86_64-w64-mingw32 三元组，
> toolchain 的 `platform: win64` 字段标注），区别在 CRT 运行时（msvcrt.dll vs
> ucrtbase.dll，后者 Windows 10+ 自带）：工具链分别来自 `mingw-w64-x86_64-*`
> 与 `mingw-w64-ucrt-x86_64-*` 系统包，PATH 中的子系统目录由 toolchain 的
> `msystem` 派生（框架无硬编码），产物隔离在 `out/msys2-mingw64` /
> `out/msys2-ucrt64`。msys2-ucrt64 的 known_failures 尚未全链验证。

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
  export PKG_CONFIG_LIBDIR="$PWD/../../out/msys2-ucrt64/lib/pkgconfig;$PWD/../../out/msys2-ucrt64/share/pkgconfig"
  meson setup builddir && meson compile -C builddir
  PATH=../../out/msys2-ucrt64/bin:$PATH XDG_DATA_DIRS=../../out/msys2-ucrt64/share \
      ./builddir/libadwaita-demo.exe --smoke
  ```

### 当前验证结果（msys2-ucrt64，2026-09-26）

全链 39 recipe 构建 + 测试通过，产物 `bin/*.dll` 由静态化前的 51 个降至 **28 个**
（均为 introspection 链必需，见「静态优先」）。

| recipe               | 测试数 | 失败 | 备注                        |
| -------------------- | ------ | ---- | --------------------------- |
| glib                 | 309    | 0    |                             |
| libadwaita           | 408    | 0    |                             |
| pango                | 348    | 4    | 均属已登记的 2 项（见下）   |
| gobject-introspection| 63     | 0    |                             |
| libpng（ctest）      | 37     | 0    | 经补丁恢复（原被静默跳过）  |
| gdk-pixbuf           | 20     | 0    |                             |
| fribidi              | 8      | 0    |                             |
| expat（ctest）       | 1      | 0    |                             |

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

### 当前已知失败（均已在 recipe 中登记，构建不阻塞）

**Windows TLS callback 家族 bug（GCC/bfd 特有，同式修复）**：GCC 分支的
`G_DEFINE_TLS_CALLBACK`（glib）/ `DEFINE_TLS_CALLBACK`（cairo）把回调放进
`.CRT$XL*` 段，但缺少 MSVC 分支 `/INCLUDE:_tls_used` 的等价物；GNU bfd ld
仅在 `_tls_used` 被引用时才生成 PE TLS directory 并收集回调，否则 Loader
不调用回调。统一修复：recipe 加 `-Dc_link_args=-Wl,--undefined=_tls_used`。

- **glib（已修复并复验）**："TLS callback not invoked" 消失，309 项测试
  全过，原 12 项已知失败全部移除。glib 2.90.0 仍含 `G_DEFINE_TLS_CALLBACK`，
  故该修复在升级后必须保留。
- **cairo（已修复并复验，2026-08-25）**：`cairo_win32_tls_callback`
  （`.CRT$XLD`，负责初始化 Win32 静态互斥体/CRITICAL_SECTION）不被调用，
  互斥体全零，字体度量路径 `EnterCriticalSection` 即 0xc0000005——崩溃栈
  `gtk_label_measure → pango_context_get_metrics →
cairo_win32_font_face_create_for_logfontw_hfont`；二进制实证：自建
  libcairo-2.dll 的 PE TLS Directory 为 0，MSYS2 官方包非 0。重建后
  pango 原 0xc0000005 崩溃消失，test-ellipsize/testiter 转好。
- **pango 2 项（非崩溃性失败，已登记）**：cairo/pango 显式启用
  `-Dfontconfig=enabled -Dfreetype=enabled`（Windows 上游默认 auto→disabled，
  无 pangoft2/fc 后端）后，测试经 recipe `test.env` 注入
  `PANGOCAIRO_BACKEND=fc` 运行，原 5 项实测收缩为 2 项：
  - `pango:test-font`：`roundtrip` 的 small-caps/all-small-caps/unicase 子测试
    需系统级 Cantarell 变体字体（上游 Linux CI 预装），本机回退到系统 Sans 后
    describe 不含变体；`/pango/font/custom` 的路径分隔符比较 bug 已由
    `pango-0001-*.patch` 修复。
  - `pango:test-fonts`：`fontsets/cantarell2` 对 DejaVu Sans/Mono 的 fontconfig
    排序平序（tie）随版本而异，纯平台差异。
- **libpng 测试覆盖（已修复，2026-09-26）**：上游把整个测试段门控在
  `if(PNG_TESTS AND PNG_SHARED)` 上、测试程序硬链 `png_shared`，纯静态构建会
  **静默跳过全部 37 项**测试。补丁 `libpng-0001-tests-against-static-library.patch`
  改为门控 `PNG_LIBRARY_TARGETS` 并按实际变体选择链接目标，37 项测试已恢复全过。
- gvsbuild 对照：其用 MSVC（无此问题）且 glib 默认 `-Dtests=false`；我们不引入
  额外验证，仅如实记录失败集。
- **libadwaita（已修复并复验）**：原 67 项失败根因是测试环境
  而非产物缺陷——`meson test` 继承登录 shell 的 `XDG_DATA_DIRS`，schema
  source 为 NULL（libadwaita 上游本不携带 gschema，非安装缺失）；框架注入
  `XDG_DATA_DIRS=$SYSROOT/share` 后全部通过，登记已清空（2026-09-26 复验
  408 项全过）。
- gvsbuild 对照：其用 MSVC（无此问题）且 glib 默认 `-Dtests=false`；我们不引入
  额外验证，仅如实记录失败集。

### 字体渲染（DirectWrite 链，2026-08-25 启用）

此前 cairo `-Ddwrite=disabled` 级联导致 pango 文本走 GDI 栅格化
（pangowin32 无 dwrite fontmap、harfbuzz 无 DirectWrite 整形），150% 缩放
（144dpi）下字体边缘发虚。现已对齐 MSYS2 官方包：

- cairo `-Ddwrite=enabled`（产出 cairo-dwrite-font.pc）
- harfbuzz / harfbuzz-base `-Ddirectwrite=enabled`
  （pango 的 `USE_HB_DWRITE` 依赖 `hb_directwrite_face_create`）
- fontconfig 补丁 `fontconfig-0001-link-confs-copy-fallback.patch`：
  上游 link_confs.py 在 Windows 无符号链接权限（winerror 1314）时静默
  break，conf.d 只剩 README、hinting/lcdfilter 配置整体缺失；改为回退复制
  文件，conf.d 补齐 24 个默认片段。

### GTK4 Win32 运行时已知限制（上游行为，非本框架构建缺陷）

- **分数缩放**：Win32 后端 scale 仅取整数（`dpix / 96` 整除），150% 缩放下
  scale=1、字体按 144dpi 渲染（`gtk-xft-dpi=147456`），UI 与字体密度不匹配。
  可选 `GDK_WIN32_PER_MONITOR_HIDPI=1` 开 per-monitor 感知，但整数 scale
  逻辑不变。MSYS2 官方包行为相同。
- **GL/Vulkan 渲染器窗口四周黑边（上游 #7567）**：根因是
  **DirectComposition (DComp)**——GL/Vulkan 渲染器把画面经
  `CreateWindowEx(WS_POPUP)` 子窗口 + `IDCompositionDevice_CreateSurfaceFromHwnd`
  提交给 DComp，而由 HWND 生成的表面**没有 alpha 通道**，CSD 的阴影边距与
  圆角之外那些"本该透明/半透明"的像素被当作**不透明黑**输出，看起来就是
  一圈巨大黑边。cairo 渲染器不走这条路（自己建
  `CreateSwapChainForComposition` + `DXGI_ALPHA_MODE_PREMULTIPLIED` 的
  带 alpha swapchain），所以正常。
  GTK **4.24.0 已修复**：DComp 由"默认开"改为"`GDK_DEBUG=dcomp` 才开"
  （commit `914cb8d`，NEWS 记 `#7567`），GL/Vulkan 因此不再被默认选用、
  一律回退 cairo——MSYS2 官方包即此行为。本框架自 2026-09-26 起构建
  4.24.0，默认无黑边。
  - 反向验证：`GDK_DEBUG=dcomp` 会让 GL 重新被选中（黑边复现），证明门控生效。
  - 此时 GL：`GskGLRenderer`；默认：`GskCairoRenderer`（`GSK_DEBUG=renderer` 可观察）。
  - 4.22.4 及更早版本无此修复（逐 tag 核对：4.20/4.22 系列均无该门控），
    只能运行时限 `GDK_DISABLE=dcomp` 或 `GSK_RENDERER=cairo` 规避。
  - 上游指出待 D3D12 渲染器落地并成为 Win32 默认后才考虑重新默认开启 DComp；
    4.24.0 仍无 D3D12 渲染器，故 GPU 渲染目前需显式 `GDK_DEBUG=dcomp` 且仍有黑边。
- **强制 Vulkan 失败回退 GL**：GTK 非 Wayland 平台从不自动选 Vulkan
  （`Not using Vulkan: platform is not Wayland`；4.24 下措辞为
  `GdkWin32Display prefers OpenGL`）。强制 `GSK_RENDERER=vulkan` 时，
  DComp 未启用（默认）会直接报 `Vulkan requires Direct Composition` 并回退
  cairo；若同时 `GDK_DEBUG=dcomp`，Vulkan 经 cloaked 子窗口 + DComp 呈现，
  AMD 驱动（RX 7700 XT，ICD 经显卡适配器注册表键注册）
  `vkCreateSwapchainKHR` 返回 VK_ERROR_UNKNOWN，GTK 再回退——均属预期行为。

## 版本与来源说明

- 版本对齐 MSYS2 当前包（`pacman -Si mingw-w64-x86_64-<pkg>`）与 gvsbuild，
  定期以 MSYS2 仓库为参照升级；`versions.lock.yaml` 锁定每个 recipe 的 sha256。
- 上游归档不可达时的替代源（已注明在 recipe 注释）：
  gitlab.freedesktop.org 归档有登录墙 → 改用 Debian pool orig 包
  （pixman/cairo/libepoxy/appstream）；cairographics.org 不可达 → Debian pool。
- appstream 依赖链（libadwaita 的 about dialog 未删除 appdata API）：libxml2 →
  libxmlb → libfyaml → curl（schannel 后端，避开 openssl/libssh2 大链）→ appstream，
  全部从源码构建。
- 变更 recipe 版本后需同步 `versions.lock.yaml`（sha256），并清空对应
  `build/<target>/<recipe>` 重建（stamp 不感知版本变化）。

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
| libadwaita     | 1.9.3     |     | gobject-introspection | 1.86.0    |
| vulkan-headers | 1.4.357.0 |     | vulkan-loader         | 1.4.357.0 |
| spirv-headers  | 1.4.357.0 |     | spirv-tools           | 1.4.357.0 |
| glslang        | 1.4.357.0 |     | shaderc               | 2026.3    |
| libxml2        | 2.15.3    |     | libxmlb               | 0.3.28    |
| libfyaml       | 0.9.5     |     | curl                  | 8.21.0    |
| appstream      | 1.1.6     |     | directx-headers       | 1.611.0   |
| xz (liblzma)   | 5.8.1     |     |                       |           |

> 升级说明：本轮新增 appstream 链（libxml2/libxmlb/libfyaml/curl/appstream），
> libadwaita 不再采用移除 appstream 的补丁（保留 `adw_*_new_from_appdata`
> 等 appdata API，供 libadwaita-rs 绑定链接）；其完整测试套件 408 项全过。
> 另修复 Windows TLS callback 家族 bug：glib 与 cairo 均加
> `-Dc_link_args=-Wl,--undefined=_tls_used`（均已重建复验），glib 测试
> 309 项全过、原 12 项已知失败全部移除；框架 prelude 统一注入
> `XDG_DATA_DIRS=$SYSROOT/share`，修复 GSettings schema 查找。
> glib 与 gobject-introspection 存在循环依赖（glib 开 introspection 需要
> g-ir-scanner，而 GI 又依赖 glib）：参照 gvsbuild 拆为 glib-base（GI 关，
> 引导阶段）→ gobject-introspection → glib（GI 开）三段，同一 tarball，
> 从头构建可一次打通；cairo 亦改依赖 glib-base 以断开
> glib → GI → cairo → glib 环。gtk 显式依赖 vulkan-loader（vulkan=enabled）。
> autotools 工程 configure 已启用 `-C`（config.cache 实时落盘）：单步命令超时
> 中断后重跑会跳过已完成的检测，无需从零开始。
>
> 静态化（2026-09-26）：`default_library: static` 全链只出 `.a`，DLL 由 51 降至
> 28（均为 introspection 链必需，见「静态优先」）。新增 `directx-headers`
> recipe（gdk/win32 的 d3d12 路径依赖）、`libpng-0001-*` 补丁（恢复被
> `PNG_SHARED` 门控掉的 37 项测试）、`vulkan-loader-0001-*` 补丁（Windows
> 静态 loader，见下）；libffi/libiconv/gettext 保持动态（理由见「为什么保留
> 少量 DLL」）。
>
> 静态 vulkan loader 的坑：上游 Windows 下只给共享库，且互斥体（`loader_lock`
> / `loader_preload_icd_lock` / `global_loader_settings_lock`）只在 `DllMain`
> 里创建。静态库不能带 `DllMain`（它会变成宿主 DLL 的入口点），编译掉之后
> 三个 `CRITICAL_SECTION` 就再无人初始化——首次 `vkEnumerateInstance*` 便锁
> 未初始化对象而 SIGSEGV（栈：`RtlEnterCriticalSection →
> update_global_loader_settings`）。表现是 `GSK_RENDERER=vulkan` 或打开
> **GTK Inspector**（`GTK_DEBUG=interactive`，其 `init_vulkan()` 也枚举实例
> 扩展）直接闪退。补丁把互斥体创建移到 `loader_initialize()`
> （经 `LOADER_PLATFORM_THREAD_ONCE` 恰好执行一次）。
>
> 升级到 GTK 4.24.0（2026-09-26）：为解决 Win32 黑边（上游 #7567，根因见
> 「GTK4 Win32 运行时已知限制」），glib 2.88.3 → 2.90.0（4.24.0 要求
> `>= 2.89.3`，此为唯一未满足项）、gtk 4.22.4 → 4.24.0，两者均与 MSYS2
> 当前包一致。**依赖与构建选项零改动**：glib 的 11 个选项、gtk 的 18 个选项
> 在两个版本中均存在（glib 仅把 `meson_options.txt` 改名 `meson.options`，
> 不影响 `-D` 传参）；glib 2.90.0 仍含 `G_DEFINE_TLS_CALLBACK` 与
> `girepository` 子目录，故 TLS 修复与两段式引导结构不变；gtk 4.24.0 未引入
> 新必需依赖（`accesskit` 默认 disabled）。升级后 glib 测试数 306 → 309。

## 当前已完成链（msys2-mingw64 / msys2-ucrt64，39 recipes）

zlib → libffi → pcre2 → libiconv → gettext → glib-base
└→ expat / freetype → fontconfig → harfbuzz(-base) → fribidi → pixman → libpng →
libjpeg-turbo → libtiff → cairo → gobject-introspection → glib（两段式）→
pango → gdk-pixbuf → graphene → json-glib → libepoxy → directx-headers →
vulkan-loader → gtk → libadwaita
├→ vulkan-headers → vulkan-loader；spirv-headers → spirv-tools → glslang → shaderc
└→ libxml2 → xz(liblzma) → libxmlb → libfyaml → curl（schannel）→ appstream → libadwaita

（GTK4 构建配置：win32 后端；vulkan=enabled、introspection=enabled；禁
gstreamer/x11/wayland/demos。SPIRV-Tools/shaderc 的 tag 归档不含 git
submodule，由框架 `submodules` 字段从已构建依赖源码树自动填充
`external/`、`third_party/` 目录。`directx-headers` 供 gdk/win32 的 d3d12
纹理路径 `dependency('DirectX-Headers')`；其 wrap 是 `[wrap-git]`，在
github.com 不可达的网络下会失败，装进 sysroot 后 meson 直接命中 .pc。）
