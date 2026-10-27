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
`msys2-mingw64` / `msys2-ucrt64` 里的 `64` 是 MSYS2 子系统名（mingw64/ucrt64）
本身，不是我们写的架构声明；Linux 原生工具链没有这样一个名字可用，故 target 叫
`linux-native` 而**不带架构**——架构由宿主编译器决定，同一份定义在 x86_64 与
aarch64 上都成立。真出现交叉工具链时（下表 `linux-mingw-x64` 之类）架构才回到
名字里，因为那时它属于工具链标识，而不是宿主属性。

| target                                   | 状态    | 说明                                                                       |
| ---------------------------------------- | ------- | -------------------------------------------------------------------------- |
| msys2-mingw64（MSYS2 MINGW64 原生）      | ✅ 完成 | 39 个 recipe 全链构建 + 自带测试                                           |
| msys2-ucrt64（MSYS2 UCRT64 原生）        | ✅ 完成 | 同 recipe 复用；UCRT 运行时，独立 sysroot（全链 39 recipe + 测试已复验）   |
| linux-native（Linux 原生，x86_64 + aarch64） | ✅ 全链完成 | 同 recipe 复用，工具链 `target_os: linux`；Windows 专属补丁/选项/依赖已门控出本目标，Linux 侧的必需依赖、X11 客户端栈与 Mesa（GL/EGL 实现）**反向**门控/新增。`build libadwaita` 闭包 62 recipe 全绿，含 libadwaita 430 个子用例。CI 在 `ubuntu-latest` 与 `ubuntu-26.04-arm` 两个容器上全量构建 |
| linux-musl-x64 / linux-mingw-x64（交叉） | 规划    | meson cross-file + exe_wrapper                                             |

> msys2-mingw64 与 msys2-ucrt64 同为 win64 输出平台（x86_64-w64-mingw32 三元组，
> toolchain 的 `platform: win64` 字段标注），区别在 CRT 运行时（msvcrt.dll vs
> ucrtbase.dll，后者 Windows 10+ 自带）：工具链分别来自 `mingw-w64-x86_64-*`
> 与 `mingw-w64-ucrt-x86_64-*` 系统包，PATH 中的子系统目录由 toolchain 的
> `msystem` 派生（框架无硬编码），产物隔离在 `out/msys2-mingw64` /
> `out/msys2-ucrt64`。两个 target 的已知失败**可以不同**：recipe 的
> `targets:` 块按目标覆盖（`Recipe.for_target`），因为 CRT 能力本身不同——
> 最典型的是 msvcrt 没有 `en_US.UTF-8` 这个 POSIX 区域名（见 [NOTES.md](NOTES.md)
> 的 libadwaita mingw64 条目）。

## recipe 的平台门控（targets: 选择器）

Windows 专属的补丁、构建选项、依赖、测试环境**不再全局启用**：recipe 的
`targets:` 块按**选择器**生效。选择器可以是工具链的 OS 家族名（取自
`toolchains/*.yaml` 的 `target_os`，即 `windows` / `linux`），也可以是精确
target 名；一个块用 `for:` 列多个选择器，多个 target 就共用同一套条目。

```yaml
targets:
  - for: [windows]                  # 两个 msys2 目标共用，Linux 不生效
    deps: [directx-headers]
    meson:
      options: [-Dc_link_args=-Wl,--undefined=_tls_used]
  - for: [msys2-mingw64]            # 同族里的单个例外（msvcrt 区域名）
    patches: [gtk-0001-fallback-to-windows-locale.patch]
  - for: [linux]
    deps: [libx11]
    meson:
      options: [-Dxlib=enabled]
```

合并语义（**base 只放平台中立项**）：`patches` / `deps` / `submodules` /
`meson.options` / `autotools.configure` / `test.known_failures` 一律**追加**到
base 之后——共享块只增不减，避免静默丢弃 base 条目；dict 深合并、叶子值按 key
覆盖（所以同一个 define 可按平台取不同值）；标量覆盖。家族块先应用、精确
target 块后应用。旧的映射写法 `targets: {msys2-mingw64: {...}}` 仍然可用。

## recipe 属于哪个平台（platforms:）

`targets:` 解决"同一个包在不同平台取不同值"；还有一个正交的问题是
"这个包在某个平台上**根本不存在**"。后者用 `platforms:` 声明，选择器语法与
`for:` 相同（OS 家族名或精确 target 名；不写 = 全平台）：

```yaml
platforms: [linux]      # recipes/mesa.yaml：X/GL 供给链只在 Linux 侧自建
platforms: [windows]    # recipes/egl-headers.yaml：Linux 上 EGL 归 Mesa
```

框架在两处用它：`build` 的入口把不属于本 target 的请求跳过并打印名单
（`[skip] 不属于 …（recipe 的 platforms 声明）: …`）；`order()` 则在"本平台的包
依赖了对侧平台的包"时直接报 `ValueError`（`plan`/`graph` 会打成一行 `error:`）——
那是依赖边写错了，继续构建只会产出错的 sysroot。对侧平台自己的包，它的依赖边与
本平台无关，不做检查（否则 libdrm 在 Windows 上会因它自己的 linux 依赖而误报）。

这条让 CI 的两个 job 都能沿用"从 `gtkcross list` 全量枚举"而不必在工作流里各抄
一份排除名单（名单必然与 recipe 漂移）：Windows job 跳过 21 个 X/GL 包，Linux job
跳过 directx-headers / directxmath / egl-headers。归属由
`tests/test_platform_gating.py` 按集合钉住，并作为 `platforms` 字段进
`tests/windows-plan.golden.json` 快照。

逐项归属与判断依据见 [recipes/platform-notes.md](recipes/platform-notes.md)
与 [patches/README.md](patches/README.md)。

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

## 快速开始（linux-native，Linux 原生）

Linux 原生 = 宿主即目标，**不是交叉编译**：编译器与构建工具直接用发行版的，
target 侧依赖全部从源码装进独立 sysroot `out/linux-native`。recipe 集合与
Windows 共用，平台差异由 `targets:` 门控隔离（见上一节）。

target 名里**不写架构**：同一份 `toolchains/linux-native.yaml` 与同一批 recipe
在 x86_64 与 aarch64 上都成立，架构由宿主决定——不给 autotools 喂 `--host/--build`
三元组（让它自己探测），libvpx 之类需要 arch 的地方把值交给上游探测
（`gcc -dumpmachine`）。CI 用 `ubuntu-latest`（x86_64）与 `ubuntu-26.04-arm`
（aarch64）两个容器各跑一遍全量构建来证明这一点；
`tests/test_platform_gating.py` 的 `ArchNeutralLinuxTarget` 则守住"任何 recipe 在
linux 家族下解析出的参数里不许出现 arch 字面量"。

```bash
# 宿主需具备（本机 Fedora 44 实测清单）：
#   编译/构建：gcc g++ make cmake ninja meson pkg-config(pkgconf) patch perl git
#   框架本身  ：python3 + PyYAML
#   包专属构建期依赖：
#     nasm            libjpeg-turbo 的 WITH_SIMD=ON；libvpx 在 x86_64 上由 auto
#                     探测选它（Linux 侧不再硬写 --as=nasm，aarch64 用 gas）
#     itstool         shared-mime-info 的翻译合并（appstream 也要）
#     python3-devel   gobject-introspection 要 Python.h 与 libpython
#                     （实测报 "Run-time dependency python found: NO"，
#                      上游 src/meson.build:231 的 cc.check_header('Python.h')）
#     flex bison      glslang 的解析器生成（Vulkan 着色器链那一段）
#     gperf           Windows CI 装了它；Linux 侧目前未观察到必需，留着备用
#     xorg-x11-server-Xvfb  只在"无头环境跑测试"时需要（下面的 wrapper）
# Fedora 示例：
sudo dnf install nasm itstool python3-devel flex bison gperf
# 要在没有 X 会话的容器/终端里跑 gtk 与 libadwaita 的测试，还需要：
sudo dnf install xorg-x11-server-Xvfb

python -m gtkcross doctor -t linux-native        # 环境自检
python -m gtkcross build libadwaita -t linux-native -j"$(nproc)"   # 全链（62 recipe）
python -m gtkcross build pango -t linux-native   # 只到前段链（28 recipe，含 X11 栈——
                                              # cairo 在 Linux 上依赖它们）
python -m gtkcross graph libadwaita -t linux-native   # Linux 计划里没有 DirectX 包，
                                              # 但有 X11 栈与 Mesa（Windows 侧没有）
# 全量构建（CI 的做法）：把 list 的全部名字交给 build，只属于 Windows 的 3 个包
# 会被框架按 platforms 跳过并打印名单：
PYTHONPATH=. python3 -m gtkcross build $(python3 -m gtkcross list | tail -n +2 | awk '{print $1}') -t linux-native -j"$(nproc)"
```

无头环境跑测试用 `GTKCROSS_TEST_WRAPPER`：它作为**整条测试命令的前缀**注入
（`gtkcross/engines.py` 的 `test_wrapper()`），CI 里设成 `xvfb-run -a`。为什么
是前缀而不是 meson 的 `--wrapper`：后者按测试程序逐个包，libadwaita 的 68 个测试
程序就会起 68 个 Xvfb；前缀每次 test 调用只起一个、并发测试共用，而且 ctest
那条路径根本没有 `--wrapper` 选项。不设该变量时命令与从前逐字节相同（本地有
桌面就不需要）。`doctor` 会检查前缀里的可执行文件存在，缺失时直接报出来。

与 Windows 目标的结构性差异（为什么 Linux 侧不需要那些补丁，又为什么需要别的）：

| 差异 | 处理位置 |
| --- | --- |
| 静态 `.a` 要链进 `.so`，必须 PIC | toolchain prelude 对非 Windows 目标注入 `CFLAGS/CXXFLAGS=-fPIC` |
| 原生构建不该传 `--host/--build` | `toolchains/linux-native.yaml` 不写 `host_triple`，autotools 走自动探测 |
| `XDG_DATA_DIRS` 不能独占 sysroot（否则丢 `/usr/share` 的主题） | prelude 按平台分别生成 |
| 构建/测试期不得读开发机桌面配置 | prelude 对非 Windows 目标设 `XDG_CONFIG_HOME=$SYSROOT/etc/xdg`（宿主 `~/.config/gtk-4.0/settings.ini` 的一行遗留 `gtk-modules` 在 `G_DEBUG=fatal-warnings` 下会让 libadwaita 68 项测试全 abort） |
| `MSYSTEM` 是 MSYS2 探测信号，Linux 上不能注入 | `Toolchain.run()` 仅在 `host: windows` 时设置 |
| `.pc` 静态判定要查 `libNAME.so` 而非 `.dll.a` | `Toolchain.shared_suffixes` |
| 产物要自带搜索路径（ELF 没有"exe 目录天然参与 DLL 搜索"） | Linux `LDFLAGS` 加 `-Wl,-rpath-link` 与 `-Wl,-rpath` |
| libm 属 C 运行时，不该被 hermetic 的 find-root 挡住 | Linux `LDFLAGS` 加 `-lm` |
| libtool 的 `multi_os_directory` 在 x86_64 Linux 返回 `../lib64` | libffi 的 `for: [linux]` 加 `--disable-multi-os-directory` |
| 架构假设不能写进 recipe（同一个 target 要跑两种 arch） | libvpx 的 `--target` 与 `--as=nasm` 只在 windows 家族块给，Linux 交给上游探测（`build/make/configure.sh:749` 的 `${CROSS}as`、1474-1484 的 nasm/yasm auto）；xcb-proto 靠 `AM_PATH_PYTHON` 按 PATH 找宿主 Python，不写 `/usr/lib64/pkgconfig`；`tests/test_platform_gating.py` 的 `ArchNeutralLinuxTarget` 守这条 |
| 上游"恰好能找到"的隐性依赖在干净 sysroot 里会失败 | Mesa 的 x11 分支无条件 `dependency('xcb')`/`('xcb-randr')`（`src/meson.build:2308-2310`）⇒ deps 补 `libxcb`，并 `-Dxlib-lease=disabled`（那是 `VK_EXT_acquire_xlib_display`，本闭包不建 Vulkan 驱动）；vulkan-loader 的 X11 WSI 三个 REQUIRED 探测 ⇒ linux 块补 `libxcb libx11 libxrandr` |
| `BUILD_WSI_X11_SUPPORT` 这个选项名上游不存在（整树 grep 0 命中），一直是空转 | 改用真名 `BUILD_WSI_XLIB_SUPPORT` / `BUILD_WSI_XLIB_XRANDR_SUPPORT`（`CMakeLists.txt:115-119` 只在 Linux/BSD 分支里 `option()`，所以 Windows 侧不补这两条，补了只会得到"变量未被使用"的警告） |
| GNU libiconv / gettext 仍在闭包内（与 Windows 同一 closure 的决定） | 见 [recipes/platform-notes.md](recipes/platform-notes.md) 第 8、9 条 |
| 上游按 `host_machine.system()` 分叉出的**必需依赖**（Windows 上不进那个分支） | recipe 的 `for: [linux]` 块：gdk-pixbuf 补 `shared-mime-info`、关 `glycin`；libxml2 纠 `Iconv_IS_BUILT_IN`；gst 补 `-Dx11=enabled` |
| 窗口后端与 GL 实现也必须自建 | 新增 X11 客户端栈 17 个 recipe + `mesa`（GL/EGL 实现，`softpipe` 路线**不需要 LLVM**），EGL 归 Mesa，`egl-headers` 收进 windows 块 |

**当前状态：linux-native 全链已打通（`build libadwaita` 闭包 62 recipe），
自带测试与端到端冒烟都过；Wayland 仍未开。**
实测（Fedora 44，gcc 16.2.1 / meson 1.11.2 / cmake 4.3.0；本机有 clang 但
**没有** `llvm-config`/`llvm-devel`，Mesa 走 softpipe 路线因此不需要它们）
自带测试 0 失败：expat 1、libpng 37、gobject-introspection 65、glib 424 项
（418 通过 + 6 上游条件跳过）、fribidi 8、pango 29 项（27 通过 + 2 跳过）、
gdk-pixbuf 23、shared-mime-info 8、gtk 1、**libadwaita 68 个测试程序 =
430 个子用例（与 Windows 记录的 430 同一集合）**。
**Linux 侧没有登记任何 `known_failures`**——Windows 登记的 pango 三项字体测试
在 Linux 全绿，正是"不照搬登记"要的效果。

端到端：`tests/libadwaita-demo` 只用 `PKG_CONFIG_LIBDIR` 指向 sysroot 即可编译，
在 `env -i`（不设 `LD_LIBRARY_PATH`）下经**自建 `gtk4-broadwayd`** 跑通
`--smoke`，退出码 0；另用只链接 sysroot 头的 C 程序验证
`libX11`/`libXrandr`/`libXinerama`/Mesa `libGL` 的 GLX 通路（GLX 1.4）。

闭包对比要看两个方向：Windows 两份各 42 recipe（含 `directx-headers`/
`directxmath`、`egl-headers`），linux-native 是 62 recipe（含 X11 栈 17 个、
`mesa`+`libdrm`+`libpciaccess`+`libxshmfence`、`libxml2`+`shared-mime-info`），
两个方向的差集都已钉进 `tests/test_platform_gating.py`。

GTK 在 Linux 上必须有窗口后端（实测 `gdk/meson.build` 有
`if gdk_backends.length() == 0 error('No backends enabled')`，而 win32 后端在非
Windows 宿主被上游自动关闭）——旧配方在 base 里把 x11/wayland/broadway 全写
`false`，在 Linux 上必然 configure 失败，现已按平台分块：**x11=true、
broadway=true、wayland=false**。Wayland 仍关着的原因写死在上游代码里：
`gtk-4.24.0/meson.build:588` 的 `dependency('wayland-egl')` 既无
`required: false` 也没有对应 wrap，而 `wayland-egl.pc` 属 Mesa 的 wayland
侧——要开 Wayland 需要先补 `wayland`/`libxkbcommon`/`wayland-protocols`
并决定 Mesa 的 wayland 平台，清单见 [recipes/platform-notes.md](recipes/platform-notes.md) 末尾。

## 回归快照（Windows 行为等价性）

Linux 上无法实跑 msys2-* 目标，所以 Windows 侧用"构建计划快照"守住：
`tests/windows-plan.golden.json` 记录每个 recipe 在每个 Windows 目标下实际会
执行的 configure 命令行、补丁/依赖列表、测试命令与环境、post_install、`platforms`
归属、build plan 拓扑序；`tests/test_windows_plan.py` 逐字段比对（含拓扑序——
以前 `plans` 只写进快照却没人比对，等于没守）。

`tests/test_platform_gating.py` 守门控语义本身，两个方向都守：

- Windows 专属项不得泄漏到 linux-native（补丁、deps、`_tls_used`/`directwrite`/
  `gl_winsys=win32` 等标记、`known_failures` 登记）；
- 跨平台的静态补丁不得被误门掉（libpng/spirv-tools/shaderc 在 Linux 上照样要打）；
- **Linux 家族块不得泄漏到 Windows**（`-Dglycin=disabled`、
  `Iconv_IS_BUILT_IN=OFF`、gdk-pixbuf 的 `shared-mime-info` dep、glib 的
  `LC_ALL` 钉法、libffi 的 `--disable-multi-os-directory`）——最后这组是
  为实测失败补的，写反方向同样会造成真实回归；
- `platforms:` 归属：只属于对侧平台的集合按名字钉住，全量枚举时每个 target
  恰好跳过它们，闭包里不许混进对侧平台的包，"本平台的包依赖对侧平台的包"必须
  报错（而**对侧平台自己的包的依赖边不检查**，这条反向护栏也测）；
- `ArchNeutralLinuxTarget`：任何 recipe 在 linux 家族下解析出的参数里不得出现
  arch 字面量（x86_64/aarch64/…），因为同一个 target 要在两种架构的宿主上成立；
- `TestWrapperHook`：`GTKCROSS_TEST_WRAPPER` 作为前缀包住 meson 与 ctest 两条
  测试命令，且位于 recipe `test.env` 的 `env K=V` 之外；不设时命令逐字节不变。
- `OpensslTlsBackend`：OpenSSL 走自己的入口脚本与 `install_sw`（默认 `install` 会
  往 `/etc/ssl` 写文件）、`--libdir=lib` 必须显式给（否则 x86_64 的 multilib 把它
  变成 `lib64`）、curl 两平台各选各的后端，以及 `autotools.script` /
  `install_target` 两个新键缺省时命令串逐字节不变。
- `tests/test_download_guard.py`：新下载的内容必须是可识别的 tar/zip 才接受
  （文件名不是证据——404 错误页也顶着 `*.tar.gz`），拒绝后不得留在缓存里。

```bash
PYTHONPATH=. python3 -m unittest discover -s tests   # 全部通过即 Windows 行为未变
```

改 recipe/toolchain 时若快照失配，先确认差异"只是选项位置/平序变化"再更新基线
（方法写在 `tests/test_windows_plan.py` 的模块 docstring 里）。

## CI

`.github/workflows/ci.yml` 有三个 job 组合，每个都是"全量枚举 recipe → 构建 →
跑自带测试"，并且先跑上面那套单测：

| job | runner | target | 说明 |
| --- | --- | --- | --- |
| build | windows-latest × {msys2-ucrt64, msys2-mingw64} | Windows | MSYS2 必须落在 `C:\msys64`（`release: false`，理由写在工作流注释里） |
| linux | ubuntu-latest | linux-native @ x86_64 | 与下一行跑**同一份**配置 |
| linux | ubuntu-26.04-arm | linux-native @ aarch64 | 这一行是"target 名不带架构"这句话的证据；两个容器任一失败都说明有架构假设漏进了 recipe 或框架 |

Linux job 只装宿主构建工具（apt 名单逐项注明给哪个包用），**不装任何
`lib*-dev`**：target 侧依赖全部自建，装了也不会被 `PKG_CONFIG_LIBDIR` 的隔离看到，
却会掩盖 recipe 缺依赖的事实。测试命令通过 `GTKCROSS_TEST_WRAPPER=xvfb-run -a`
获得显示（无头容器没有 X）。产物统计步骤除了静态优先的计数，还会 `readelf -h`
抽查 `.so` 的 ELF Machine 是否等于本 runner 的架构。

## 结构

```
gtk-cross.yaml              项目配置
toolchains/*.yaml           各目标工具链描述（host/target_os、MSYSTEM、host_triple、工具映射）
recipes/*.yaml              依赖 recipe（声明式：源、构建引擎、选项、测试；targets: 平台门控）
recipes/platform-notes.md   每项按平台归属的判断依据（门控决策记录）
patches/*.patch             recipe 源码补丁（解包后自动应用）
patches/README.md           补丁的平台归属清单（哪些只挂 windows 家族块）
gtkcross/                   Python 框架（config/recipe/resolver/download/toolchain/engines/builder/events/cli）
tests/                      C 冒烟程序 + libadwaita-demo + Windows 计划快照与门控单测
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
# platforms: [linux]  # 可选：本包只在这些平台构建（不写 = 全平台）。CI 全量
#                     # 枚举 recipe 时靠它跳过对侧平台的包，见上一节。
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
- 没有 X 会话的环境里跑测试：`GTKCROSS_TEST_WRAPPER='xvfb-run -a' gtkcross build <pkg>`。
  该前缀作用于整条测试命令，meson 与 ctest 两条路径都生效；不设时命令与从前
  逐字节相同（见"快速开始（linux-native）"末尾）。
- 端到端冒烟：`tests/libadwaita-demo` 经 `pkg-config` 链接 sysroot 产物构建并跑
  `--smoke`（GTK+libadwaita 窗口 2 秒后自动退出，退出码 0 即通过）。
  ```bash
  cd tests/libadwaita-demo
  export PKG_CONFIG_LIBDIR="$PWD/../../out/msys2-ucrt64/lib/pkgconfig:$PWD/../../out/msys2-ucrt64/share/pkgconfig"
  meson setup builddir && meson compile -C builddir
  PATH=../../out/msys2-ucrt64/bin:$PATH XDG_DATA_DIRS=../../out/msys2-ucrt64/share \
      ./builddir/libadwaita-demo.exe --smoke
  ```
  linux-native 上的等价做法（实测 2026-09-29）：用**自建的 broadway** 后端，
  不依赖宿主桌面会话；`XDG_RUNTIME_DIR` 必须给 server 和 app 同一个目录
  （broadway 的 socket 在那里，`env -i` 清掉它就会出现
  `Failed to open display`）：
  ```bash
  S=$PWD/out/linux-native
  cd tests/libadwaita-demo
  PKG_CONFIG_LIBDIR="$S/lib/pkgconfig:$S/share/pkgconfig" PKG_CONFIG_PATH= \
      meson setup builddir-linux && meson compile -C builddir-linux
  mkdir -p /tmp/bw && XDG_RUNTIME_DIR=/tmp/bw "$S/bin/gtk4-broadwayd" :7 &
  env -i XDG_RUNTIME_DIR=/tmp/bw GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 \
      XDG_DATA_DIRS="$S/share:/usr/local/share:/usr/share" \
      ./builddir-linux/libadwaita-demo --smoke   # → 退出码 0
  ```
  注意这里不需要 `LD_LIBRARY_PATH`：产物自带指向 sysroot 的 RUNPATH。

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

### 在 Linux 上运行/测试 sysroot 产物的环境要求

ELF 侧的等价物是 **RUNPATH**（框架已代劳，见下），`LD_LIBRARY_PATH` 只在跑
构建目录里的中间产物时才有用：

- **RUNPATH（框架已代劳，无需手动设置）**：非 Windows 目标的 `LDFLAGS` 带
  `-Wl,-rpath,$SYSROOT/lib`，产物自带 sysroot 搜索路径。实测完全空环境下也能
  运行自建工具：`env -i out/linux-native/bin/glib-compile-schemas --version` →
  `2.90.0`；`env -i out/linux-native/bin/gio queryinfo …` 正常输出；
  `readelf -d libglib-2.0.so` 的 RUNPATH 即 `$SYSROOT/lib`。
  这条不是便利性的点缀：glib 自带测试里有两处用**清洗过的环境**启动子进程
  （`gschema-compile.c` 的 `execve(..., (gchar *[]){NULL})`、`gsubprocess` 的
  `/env` 整张替换环境表），不设 RUNPATH 时子进程报
  `error while loading shared libraries: libiconv.so.2`，连带 3 项测试失败。
  Windows 上同样的测试是过的——PE 的 DLL 搜索含可执行文件所在目录，不看环境变量。
- **为什么会有 libiconv 这条依赖**：闭包里保留了 GNU libiconv（与 Windows 同一
  closure 的决定），实测 sysroot 的 `iconv.h` 遮蔽 glibc 同名头，`iconv_open`
  被宏重写成 `libiconv_open`，于是 `libglib-2.0.so` 的 DT_NEEDED 带着
  `libiconv.so.2`。链接期还需要 `-Wl,-rpath-link,$SYSROOT/lib`（meson 给可执行
  文件加 `-Wl,--no-undefined`，只有 `-L` 会报 `not found (try using -rpath or
  -rpath-link)`）。
- **`-lm`**：Linux 目标的 `LDFLAGS` 另带 `-lm`。libm 属 C 运行时（与 Windows 的
  msvcrt/ucrt + mingw libmingwex 同类），不是 target 侧依赖包，因此不该被
  `CMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY` 挡住——实测 libtiff 的
  `find_package(CMath REQUIRED)` 就因此失败。放宽成 BOTH 则是另一个方向的错误
  （会让包嗅到发行版的 libjpeg/libpng）。
- **XDG_DATA_DIRS**：Linux 上是**前置** sysroot 而非独占
  （`$SYSROOT/share:/usr/local/share:/usr/share`），否则会丢掉宿主的图标主题等
  系统数据目录。Windows 上仍是独占（原因见上一小节）。
- 手动运行产物（例如 `tests/libadwaita-demo`）时的等价命令：

  ```bash
  S=$PWD/out/linux-native
  export PKG_CONFIG_LIBDIR="$S/lib/pkgconfig:$S/share/pkgconfig" PKG_CONFIG_PATH=
  export XDG_DATA_DIRS="$S/share:/usr/local/share:/usr/share"
  # 不需要 LD_LIBRARY_PATH：产物 RUNPATH 已指向 $S/lib
  pkg-config --cflags --libs gio-2.0
  ```

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

### linux-native 额外的包（2026-09-29 新增，版本对齐 x.org / Mesa 上游）

MSYS2 的 X11 是走 Win32 API 的移植版，不适合作为 Linux 侧版本基准，所以这组
直接取上游最新发布；sha256 全部已进 `versions.lock.yaml`（这些 recipe 里不写
内联 sha256，与仓库多数包一致）。

| recipe            | 版本    |     | recipe        | 版本    |
| ----------------- | ------- | --- | ------------- | ------- |
| util-macros       | 1.20.2  |     | libxrender    | 0.9.12  |
| xorgproto         | 2025.1  |     | libxi         | 1.8.3   |
| xtrans            | 1.6.0   |     | libxrandr     | 1.5.5   |
| libpthread-stubs  | 0.5     |     | libxcursor    | 1.2.3   |
| xcb-proto         | 1.17.0  |     | libxdamage    | 1.1.7   |
| libxau            | 1.0.12  |     | libxinerama   | 1.1.6   |
| libxcb            | 1.17.0  |     | libxxf86vm    | 1.1.7   |
| libx11            | 1.8.13  |     | libpciaccess  | 0.19    |
| libxext           | 1.3.7   |     | libdrm        | 2.4.134 |
| libxfixes         | 6.0.2   |     | libxshmfence  | 1.3.3   |
|                   |         |     | **mesa**      | 26.2.3  |

## 当前已完成链（msys2-mingw64 / msys2-ucrt64，闭包 42 recipe）

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

## linux-native 的依赖链（63 recipes，多出的 22 个都是 Linux 侧专属）

Windows 那 42 个 recipe 里，除 `directx-headers`/`directxmath`/`egl-headers`
外全部复用；linux-native 的闭包在此基础上增加：

```
X11 客户端栈（GTK x11 后端 + cairo xlib/xcb 表面 + gst glx + Mesa 的 GLX 都要）
  util-macros → xorgproto → xtrans → libpthread-stubs → xcb-proto → libxau
    → libxcb → libx11 → {libxext, libxfixes, libxrender}
    → {libxi, libxrandr, libxcursor, libxdamage, libxinerama, libxxf86vm}
GL/EGL 实现（自建 Mesa，softpipe 路线因而不需要 LLVM）
  libpciaccess → libdrm → libxshmfence → mesa  （mesa 同时是 libepoxy 的前置，
  EGL 的头与 egl.pc 由它接管，所以 egl-headers 只在 Windows 侧进入闭包）
上游在非 Windows 宿主强制要求的功能数据/依赖
  libxml2 → shared-mime-info  （gdk-pixbuf 的 src/meson.build:209 硬要
  shared-mime-info.pc；Windows 不进那段）
TLS 后端（Windows 侧是系统自带的 schannel，不占 recipe；这里只有 curl 消费）
  openssl → curl 的 CURL_USE_OPENSSL=ON
```

逐项判断依据（版本为何对齐 x.org 而非 MSYS2、构建引擎如何实测选定、
缺 xmlto/fop 为何不用加开关等）见
[recipes/platform-notes.md](recipes/platform-notes.md)。

CI 跑的不是这 63 个而是**全量**：`list` 的全部 74 个 recipe 交给 build，其中
3 个只属于 Windows 的被 `platforms` 跳过。加入 openssl 之前的实测是
**70 recipe 构建完成、`exit 0`**（2026-09-29 干净 sysroot 复验）；本轮加了
TLS 后端，待复验的期望值是 71/74。尚未打通与已知缺口（Wayland、Vulkan 无驱动
ICD）记录在该文件的末尾两节。

