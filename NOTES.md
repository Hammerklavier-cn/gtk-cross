# gtk-cross 笔记

本文件收录从 [README.md](README.md) **迁出的调查记录与笔记**：验证结果、已知失败与
根因、运行时限制、版本升级说明、新增链与框架能力、CI 失败修复、各次验证范围。

划分原则：README 只保留**稳定的使用文档**（构建方式、目录结构、验证原则、版本清单、
已完成链）；本文件收纳**按日期累积的记录**——即"当时查到了什么、改了什么、怎么验的"。

- 各节**内容原文保留**（含日期与实测证据）；仅两处结构性调整：标题层级统一提升为
  顶层 `##`（原挂在 README 的「验证原则」/「当前已完成链」之下），以及原本写
  "见下/见文末"的少数指针改为指向仍在 README 的章节名。
- 文中出现的「静态优先」「为什么保留少量 DLL」「当前已完成链」「版本与来源说明」等
  章节名，指的是 [README.md](README.md) 中的对应章节；「见下」若指向已迁出的内容，
  即为本文件中的下一节。

---



## 当前验证结果（msys2-ucrt64，2026-09-26）

libadwaita 依赖闭包内的 **33 recipe** 构建 + 测试通过（仓库共 39 个 recipe——
libadwaita 1.10.0 起 appstream 链已脱离闭包，见下），产物 `bin/*.dll` 由静态化前
的 51 个降至 **28 个**（均为 introspection 链必需，见「静态优先」）。

> **2026-09-27 更新（msys2-mingw64）**：上表是 2026-09-26 的 ucrt64 记录，未同步
> 本次改动。本次在 msys2-mingw64 上并入媒体链（gstreamer/gst-plugins-* 等 9 个
> recipe）与两个运行期数据包，闭包 33 → **42**、仓库 recipe 39 → **52**；sysroot
> 的 `bin/*.dll` 28 → **65**（媒体链按上游设计必须动态：插件是运行期由 registry
> 加载的 DLL）。逐包复验范围见本文件「媒体链」与「本次验证范围」两节；新增的
> `meson.env` / `autotools.raw_configure` / `build: data` 三项框架能力见本文件
> 「框架侧新增能力」一节（README 的「当前已完成链」只保留依赖链与构建配置）。

| recipe                | 测试数 | 失败 | 备注                       |
| --------------------- | ------ | ---- | -------------------------- |
| glib                  | 309    | 0    |                            |
| libadwaita            | 430    | 0    |                            |
| pango                 | 348    | 4    | 均属已登记的 2 项（见下）  |
| gobject-introspection | 63     | 0    |                            |
| libpng（ctest）       | 37     | 0    | 经补丁恢复（原被静默跳过） |
| gdk-pixbuf            | 20     | 0    |                            |
| fribidi               | 8      | 0    |                            |
| expat（ctest）        | 1      | 0    |                            |

## 当前已知失败（均已在 recipe 中登记，构建不阻塞）

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
- **pango 3 项（非崩溃性失败，均已登记）**：cairo/pango 显式启用
  `-Dfontconfig=enabled -Dfreetype=enabled`（Windows 上游默认 auto→disabled，
  无 pangoft2/fc 后端）后，测试经 recipe `test.env` 注入
  `PANGOCAIRO_BACKEND=fc` 运行，原 5 项实测收缩为 3 项。这 3 项都是**宿主
  字体环境依赖**、非产物缺陷——同一份二进制换一台字体集不同的机器结论就变：
  - `pango:test-font`：`roundtrip` 的 small-caps/all-small-caps/unicase 子测试
    需系统级 Cantarell 变体字体（上游 Linux CI 预装），无系统 Cantarell 时
    回退到系统 Sans，describe 不含变体；`/pango/font/custom` 的路径分隔符
    比较 bug 已由 `pango-0001-*.patch` 修复。
  - `pango:test-fonts`：`fontsets/cantarell2` 对 DejaVu Sans/Mono 的 fontconfig
    排序平序（tie）随版本而异，纯平台差异。
  - `pango:test-font-data`：断言的是**派生态**度量（`pangofc-font.c` 用布局实测
    值相除/取最大，不是字体固有度量，故字体固有的 ascent/descent 等断言在 CI
    上照样通过）。`boxes.ttf` 只含 `0x20` 与 A–P/Z 共 18 个字形，而上游采样串
    是 `"The quick brown fox jumps over the lazy dog."`（44 字符）——其中 36 个
    字符必须回退到宿主字体，于是 `approximate_char_width` /
    `approximate_digit_width` 由宿主字体集决定。实测同一二进制：全量 Windows
    字体集→`49273/58368`（上游期望值，通过）；精简集→`53178/65536`（与 CI
    runner 逐字节一致）；pango 自带 `tests/fonts`→`52935/65536`。本机因字体集
    完整而通过，故登记为已知失败而非改代码。
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
  430 项全过；1.10.0 升级使 junit 由 408 增至 430）。
- **libadwaita 全套 68 项在 mingw64 上曾集体 `ERROR exit status 3`（已真正修复）**：
  ucrt64 一直全过，mingw64 68/68 `ERROR exit status 3` 且 stderr 只有一行
  `Gtk-WARNING: Failed to set locale to en_US.UTF-8`。根因与产物无关，是 CRT
  差异：msvcrt 没有 `en_US.UTF-8` 这个 POSIX 区域名（只认
  `English_United States.1252` 之类），而 GTK 的 `gtk_test_init`
  （`gtk/gtktestutils.c`）**硬编码** `setlocale (LC_ALL, "en_US.UTF-8")` 并在失败
  时 `g_warning`；更关键的是 GLib 的 `g_test_init` **无条件**把 warning/critical
  设为致命（`glib/gtestutils.c`：*make warnings and criticals fatal for all test
  programs*）——**与 `G_DEBUG=fatal-warnings` 无关**（实测把它去掉毫无变化，这一
  点最初的归因是错的）。`_g_log_abort` 在无调试器时走 `g_abort()`，msvcrt 的
  abort 退出码即 3；挂 gdb 时 `IsDebuggerPresent()` 为真改走 `G_BREAKPOINT()`，
  实测 SIGTRAP，栈为 `g_log_writer_default ← g_log_structured_standard ←
  gtk_test_init`——直接实证。每个测试都过 `gtk_test_init`，故整套在初始化阶段就
  死（TAP 头之后没有任何测试结果）。
  上游 GTK `main` 与 4.24.0 一字未改，MSYS2 的 PKGBUILD 则直接
  `-Dtests=false`（libadwaita）/ `-Dbuild-tests=false -Dbuild-testsuite=false`
  （gtk4）从不跑这些测试，没有现成修复可抄。修复 = 新补丁
  `patches/gtk-0001-fallback-to-windows-locale.patch`：`setlocale` 失败时回退到
  Windows 区域名，不再产生致命 warning；补丁**只对 msvcrt 目标应用**（gtk
  recipe 的 `targets: msys2-mingw64: patches:` 覆盖），ucrt64 的源码与产物不变。
  本地实测两者：mingw64 `Ok: 68 / Fail: 0`，ucrt64 仍 `OK=68`。
- gvsbuild 对照：其用 MSVC（无此问题）且 glib 默认 `-Dtests=false`；我们不引入
  额外验证，仅如实记录失败集。

## 字体渲染（DirectWrite 链，2026-08-25 启用）

此前 cairo `-Ddwrite=disabled` 级联导致 pango 文本走 GDI 栅格化
（pangowin32 无 dwrite fontmap、harfbuzz 无 DirectWrite 整形），150% 缩放
（144dpi）下字体边缘发虚。现已对齐 MSYS2 官方包：

- cairo `-Ddwrite=enabled`（产出 cairo-dwrite-font.pc）
- harfbuzz / harfbuzz-base `-Ddirectwrite=enabled`
  （pango 的 `USE_HB_DWRITE` 依赖 `hb_directwrite_face_create`）
- fontconfig 补丁 `fontconfig-0001-install-conf-d-as-regular-files.patch`：
  上游 link_confs.py 在 Windows 无符号链接权限（winerror 1314）时静默
  break，conf.d 只剩 README、hinting/lcdfilter 配置整体缺失；改为安装实体
  文件，conf.d 补齐 24 个默认片段。
- fontconfig 补丁 `fontconfig-0002-absolute-confdir-in-fonts-conf.patch`：
  安装的 `fonts.conf` 里 `<include ignore_missing="yes">conf.d</include>` 上游
  会被截成相对路径，改由 fontconfig 搜索路径解析。一旦落点不是本配置所在
  目录就静默解析为空（`ignore_missing="yes"` 不报错），conf.d 全部规则失效，
  字体回退与度量随之改变。写入绝对路径，使安装配置自足。

## GTK4 Win32 运行时已知限制（上游行为，非本框架构建缺陷）

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
- **拖动改变窗口大小卡顿（上游行为，4.22 / 4.24 均存在）**：与构建方式无关
  ——MSYS2 官方包、gvsbuild、本框架自建三者表现一致，故非本框架缺陷。根因是
  GDK 的 Win32 缩放路径**每一步都对整个窗口重排 + 重快照**（成本随窗口内容/
  面积增长），且 Windows 后端**没有 vblank 等待**：

  - `WM_MOUSEMOVE` → `gdk_win32_surface_do_move_resize_drag()`
    （`gdk/win32/gdksurface-win32.c`）→ `SetWindowPos()`，末尾
    `gdk_surface_request_layout()` 请求帧时钟的 layout 阶段。随后 GTK 侧
    **对整窗重新快照**：`gtk_widget_queue_draw()`（`gtk/gtkwidget.c`，置
    `draw_needed` 并丢弃 `render_node`）→ `gdk_surface_queue_render()`
    （`gdk/gdksurface.c`，传入**空** region）→ 帧时钟 paint 阶段
    `surface_render_cb()`（`gtk/gtknative.c`）里
    `gtk_widget_snapshot (widget, snapshot)` 对**根 widget（整个窗口）**
    重建快照，再交 `gsk_renderer_render()` 渲染。即：新露出的往往只是一条
    窄边带，但每一步都要按整窗内容重排 + 重快照，成本随窗口内容/面积增长。
  - 注意不要高估后端的脏区作用：`gsk_renderer_render()`
    （`gsk/gskrenderer.c`）确实有脏区判决——`region == NULL`、无
    `prev_node` 或 `GSK_DEBUG=full-redraw` 时取整窗，否则走
    `gsk_render_node_diff()` 只并上两次 render node 的差异区域。但该判决
    作用在**快照之后**：耗时的整窗 `gtk_widget_snapshot()` 已经先执行了，
    所以实测成本仍随面积线性增长（这正是下面表格所示）。
  - Windows 是 GDK 里**唯一没有 vsync 同步**的后端：帧时钟是纯定时器驱动的
    `_gdk_frame_clock_idle_new()`，全树无 `DwmFlush` / `WaitForVBlank` /
    `SetMaximumFrameLatency`，只用 `DwmGetCompositionTimingInfo` 事后估算。
    Wayland / X11 / macOS 均有各自的 vsync 驱动帧时钟。
  - CSD 的拖动/缩放**不经 Windows 模态循环**（`WM_ENTERSIZEMOVE`）：实测
    真实拖动全程 `GetGUIThreadInfo().hwndMoveSize` 恒为 0 而 `hwndCapture`
    非 0，即 GTK 自己 `SetCapture()` 后在 `WM_MOUSEMOVE` 里驱动。故
    `gdk/win32/gdkevents-win32.c` 里 `SetTimer(..., 10, modal_timer_proc)`
    的"模态定时器泵"**不在此路径上**（其注释"让 Windows 去缩放"是过时注释，
    与 `gdk_win32_toplevel_begin_resize()` 的实际实现不符）。
  - 实测：真实鼠标拖动注入（光标 15–20 万次/秒 ≫ 真实手速），显示器
    3840×2160 @160Hz → 帧预算 6.25ms。尺寸变化频率与 CPU：

    | 窗口宽 | 尺寸变化率 | 中位间隔 | p95    | CPU(cairo) | CPU(GL) |
    | ------ | ---------- | -------- | ------ | ---------- | ------- |
    | 320px  | 95 /s      | 6.3ms    | 12.1ms | 19%        | 33%     |
    | 760px  | 158–164 /s | 6.3ms    | 7.8ms  | 37%        | 38%     |
    | 2200px | 87 /s      | 14.1ms   | 16.4ms | **71%**    | 28%     |

    760px 时中位间隔 6.3ms ≈ 刷新间隔 6.25ms（已锁在刷新率上限）；窗口一放大
    就掉到 87/s、14.1ms ≈ 2 个刷新周期，即"重排+全窗重绘"超过 6.25ms 帧预算、
    只能隔帧更新。**同一时间光标移动几十万次而窗口仅更新几十次**，这就是
    拖动时窗口跟不上光标的直接原因。帧间隔分布落在刷新间隔整数倍上且抖动仅
    0.05–0.2ms（帧时钟与刷新对齐良好），排除"帧时钟乱序"这一解释。

  - **与渲染器无关**：`GDK_DEBUG=dcomp`（切到 GL）确实生效、CPU 由 71% 降至
    28%，但尺寸变化率**两边同为 87/s**——瓶颈在"每步重排 + 面积相关的全窗
    重绘"，不在光栅化。故用 GL 治不了卡顿，且会带回黑边（见上条）。
  - `GDK_DEBUG=no-vsync` 亦无效（小窗口仍 ~160/s，大窗口仍 ~88/s）。
  - 缓解：缩小窗口（成本随面积线性下降，87→158/s）；真正的修法在上游——
    把每步的整窗重快照/重渲染改为只重绘新露出的条带，并把每个
    `WM_MOUSEMOVE` 的 `SetWindowPos` 合并到帧时钟每帧一次。
  - 取证注意（两个易踩的坑）：① `GSK_RENDERER=gl` 时进程有**两个**可见顶层
    窗口（`gdkSurfaceToplevel` 与 GL 重定向窗口 `GdkWin32GL`），枚举顺序里
    后者在前；若按"第一个可见窗口"选取会拖错窗口（表现为 0 帧、看似极快）。
    必须按 class 名精确选取 `gdkSurfaceToplevel`，并置前台避免 DWM 对后台
    窗口节流。② 探针无法把窗口放大到超过屏幕（本机逻辑 2560×1440 / 150%
    缩放），>2593px 的测量会被钳制而失效。

## 版本升级说明（按时间累积）

> 升级说明：本轮新增 appstream 链（libxml2/libxmlb/libfyaml/curl/appstream），
> libadwaita 不再采用移除 appstream 的补丁（保留 `adw_*_new_from_appdata`
> 等 appdata API，供 libadwaita-rs 绑定链接）；其完整测试套件当时 408 项全过
> （1.10.0 起该链已脱离闭包，见下）。
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
update_global_loader_settings`）。表现是 `GSK_RENDERER=vulkan` 或打开
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
>
> 升级到 libadwaita 1.10.0（2026-09-26）：与 MSYS2 包一致（要求
> glib `>= 2.89.3`、gtk `>= 4.23.1`，二者均已满足）。变化有三：
> ①**options 去掉 `gtk_doc`**（1.10.0 已删除该选项，继续传会让 meson 直接报错），
> 其余 5 个（tests/introspection/documentation/vapi/examples）均保留；
> ②**appstream 换成 vendored ministream**：1.10.0 用 `dependency('ministream')`
> 取代 appstream，tarball 内含 `subprojects/ministream` 完整源码，依赖以
> `install-profile=vendored-no-excludelibs` + `default_library=static` 内置静态
> 链接、不安装不产出 DLL，故 recipe 的 appstream 依赖与补丁一并移除，
> 依赖闭包由 39 降为 33；③**stylesheet 补丁上游化**：
> `libadwaita-0001-stylesheet-tarball-css-check.patch` 已删——1.10.0 的
> `src/stylesheet/meson.build` 判定条件本就是 `if not fs.exists('gtk.css')`，
> 与我们补丁改后的内容完全一致。测试数 408 → 430，全过。
>
> libadwaita 版本升级的一个坑（本地已踩到）：`g-ir-scanner` 在 **compile
> 阶段**就要链接 `-ladwaita-1`，此时 install 尚未执行，命中的是 sysroot 里
> **上一版的旧导入库**。1.10.0 新增符号 `adw_css_class_binding_get_type` 不在
> 1.9.3 的 `libadwaita-1.dll.a` 中，于是 `Adw-1.gir` 生成失败（undefined
> reference）。全量重建（`out/` 为空）不暴露此问题，**增量升级必须先删除
> sysroot 中的旧产物**（`lib/libadwaita-1.dll.a`、`bin/libadwaita-1-0.dll`、
> `include/libadwaita-1/`、`lib/pkgconfig/libadwaita-1.pc`、`share/gir-1.0/Adw-1.gir`、
> `lib/girepository-1.0/Adw-1.typelib`）再构建。

## 媒体链（2026-09-27 新增）

GTK 的 media 后端（GtkMediaFile / GtkVideo / GtkMediaControls）在 Windows 上
只有 gstreamer 一种实现（`gtk/media/meson.build`），关掉后
`gtk_media_file_new()` 会直接 `g_error`（"GTK was run without any GtkMediaFile
extension…"），即用即崩。GTK 4.24 的 meson 硬性要求 `gstreamer >= 1.28.0`，
并需要四个 .pc：play / d3d12（gst-plugins-bad）、gl / allocators（gst-plugins-base）。

| 组件 | recipe | 关键开关与说明 |
| --- | --- | --- |
| 核心 | gstreamer | tools=enabled；tests/examples/benchmarks/doc/introspection 关 |
| base | gst-plugins-base | `-Dgl=enabled -Dgl_winsys=win32 -Dgl_api=opengl -Dgl_platform=wgl`；playback/typefind/videoconvertscale/audioconvert/audioresample/ogg/opus/vorbis/pango |
| bad | gst-plugins-bad | `-Dd3d12=enabled -Dd3d11=enabled`；gstplay/gstplayer 为 gst-libs 常驻库（gstreamer-play-1.0 由此提供）；需 directxmath + directx-headers |
| good（运行期） | gst-plugins-good | matroska / vpx / autodetect / directsound / isomp4 / wavparse / audioparsers / deinterlace；不参与 libgtk 编译，故不列为 GTK 的构建依赖 |
| 编解码依赖 | libogg / libopus / libvorbis / libvpx | libvpx 用手工 configure（框架 `autotools.raw_configure`）静态构建，x86_64 上必须 nasm；libopus 走 `-Dasm=disabled` |

gst 全链一律 `default_library: shared`——插件是运行期由 registry 扫描、
`g_module_open` 加载的 DLL，核心库若静态化则每个插件各持一份 GObject
类型/单例；这是"静态优先"的明确例外（与 introspection 链同级）。

实测（msys2-mingw64）：`gst-inspect-1.0` 25 插件 / 352 feature；
`videotestsrc ! vp9enc ! webmmux ! filesink` → `filesrc ! matroskademux ! vp9dec
! fakesink` 闭环通过；`vp8dec`/`vp9dec`/`vp9enc` 均已注册。
（2026-09-27 已解决）曾出现 **`vp8enc` 未注册**：gst-plugins-good 的 vpx 插件
对 libvpx 做链接期探测时报缺 `vp8_encode_value` / `vp8_start_encode` /
`vp8_prob_cost`（同式的 VP9 探测通过）。根因不在构建选项，而在 libvpx 的
**截断对象**——本机第一次构建 libvpx 时被手工超时打断，
`vp8/encoder/boolhuff.c.o` 被留成 **0 字节**；之后 make 按时间戳认为它已最新，
把这个空对象归档进 `libvpx.a`，VP8 编码器符号于是全部悬空（`nm` 只见 `U`）。
彻底删掉 `build/<target>/libvpx/` 重编后，meson 探测日志显示四个接口
（VP8/VP9 编解码）全部提供，`gst-inspect-1.0 vp8enc` 已注册
（25 插件 / 353 feature），`videotestsrc ! vp8enc ! webmmux ! filesink` →
`filesrc ! matroskademux ! vp8dec ! fakesink` 闭环通过——VP8 与 VP9 双向均可用。

> 经验：构建被中断（进程被杀 / 超时）后，make 的增量判定会误信截断产物；
> 此时应删除该包的 `build/<target>/<pkg>/` 重编，而不是只清框架的 stamp。

## EGL（2026-09-27 新增，当前**关闭**）

为不依赖 MSYS2 的 `mingw-w64-egl-headers` 包，新增 `egl-headers` recipe
（Khronos EGL-Registry 按 commit 固定，用框架的 `build: data` 声明式安装）：
EGL 头与 `egl.pc` 进 sysroot，实测 `pkg-config --modversion egl` = 1.5。

但 libepoxy 的 `-Degl=yes` **实测不可开**：本机没有任何 EGL 运行期实现
（Windows 上即 ANGLE 的 libEGL.dll），而 epoxy 在 ENABLE_EGL 下会在 GL 上下文
检查里无条件走 EGL 查询，此时分发指针为 NULL：

```
epoxy_is_desktop_gl() → epoxy_eglGetCurrentContext → egl_provider_resolver
→ epoxy_conservative_egl_version → eglGetCurrentDisplay() → 跳到 0x0（SIGSEGV）
```

触发点是 GTK 的 **WGL** 路径（`gdk_win32_gl_context_wgl_realize` →
`epoxy_has_gl_extension`），即"只用 WGL"的正常场景也会崩：`libadwaita-demo
--smoke` 退出码 139（gdb 实测栈）。故 epoxy 保持 `-Degl=no`，GTK 的 config.h
无 `HAVE_EGL`。要打开需二选一：给 sysroot 加 libEGL 实现（Windows 上现实选择
是 ANGLE，需先验证能否用 mingw-gcc 构建），或给 epoxy 打"EGL 库未加载时 EGL
查询安全返回"的补丁。

## 框架侧新增能力（2026-09-27）

- `meson.env`：构建期环境注入（值支持 `$SRC`/`$BUILD`/`$WS` 占位符）。首个用例
  是 shared-mime-info——meson 把 `--datadirs=<源码>/data/.` 以 Windows 形式
  （`C:/…`）写进 GETTEXTDATADIRS，而 gettext 的搜索路径按 `:` 切分
  （`gettext-tools/src/search-path.c:52` 的 foreach_elements），盘符冒号把路径
  切成 "C" 与 "/msys64/…" 两个无效项，msgfmt 报 "cannot locate ITS rules"；
  改用单数、不参与切分的 `GETTEXTDATADIR` 指向源码 its/ 目录即解。
- `autotools.raw_configure`：手工 configure（libvpx）不注入
  `-C/--host/--build` 与共享/静态开关——libvpx 的 configure 对未知参数直接
  `die_unknown`。
- `build: data`（DataEngine）：纯数据/头文件包按 `data.install_files` /
  `data.text_files`（`@PREFIX@` 展开为 sysroot 路径）安装，用于 egl-headers。
  `install_files` 的**目标路径一律是目录**（同 meson 的
  `install_headers(subdir: …)`）：单文件按原名落入该目录，目录则递归复制其
  内容。

## CI 失败修复：egl-headers 把 include/KHR 装成了文件（2026-09-27）

CI（msys2-ucrt64，干净 sysroot）在 `gl-headers` 的 `meson install` 处失败：

```
FileExistsError: [WinError 183] Cannot create a file when that file already
exists: '…/out/msys2-ucrt64/include/KHR/'
  mesonbuild/minstall.py install_headers -> do_copyfile -> makedirs(outdir, exist_ok=True)
```

根因不在 gl-headers，而在 `DataEngine.install()` 的旧语义：`src` 是文件时
把 `dest` 当**文件名**直接 `copy2`。于是 `api/KHR/khrplatform.h: include/KHR`
这条声明把 `include/KHR` 建成了名为 `KHR` 的**普通文件**。gl-headers 的
meson install 要往 `include/KHR/` 放同名头文件（上游 `install_headers(...,
subdir: 'KHR')`），`os.makedirs` 在目标已存在且不是目录时即抛 WinError 183
（CI exit 17）。

```
include/ 内容:  [..., 'KHR']        # 文件，不是目录
os.path.isdir('…/include/KHR/') -> False
os.makedirs('…/include/KHR/', exist_ok=True) -> FileExistsError(183)
```

**为何本机从未暴露**：`include/KHR` 早已是目录（旧 sysroot 里 gl-headers 先
装过）。此处 `copy2(file, 已存在的目录)` 恰好把文件写进该目录、**不报错**
（实测），与"复制到文件路径"在行为上无法区分；且 install 有 stamp 跳过机制，
`egl-headers` 一旦装过就不再重跑。只有干净 sysroot + egl-headers 先于
gl-headers 执行（build plan 中 `egl-headers` 恒在前，它是 libepoxy 的依赖，
而 gl-headers 挂在 gst-plugins-base 下）才必现。

修复：`install_files` 的 dest 统一按目录处理（`dest.mkdir(parents=True,
exist_ok=True)` + `copy2(src, dest / src.name)`），并在 dest 已存在且**不是
目录**时显式报错，避免同类静默误装。已在干净 sysroot 上端到端复验：
egl-headers(data) 与 gl-headers(meson setup/compile/install) 连续两轮全过，
`include/KHR` 始终是目录、内含 `khrplatform.h`，`include/GL` 2 个、
`include/EGL` 3 个头文件到位。

## CI 失败修复：gst-plugins-bad 的 WinRT 探测假阳性（2026-09-27）

CI（msys2-ucrt64）在 `gst-plugins-bad` 的 `meson compile` 处失败：

```
../src/sys/d3d11/gstd3d11window_corewindow.cpp:32:10: fatal error:
  windows.ui.xaml.media.dxinterop.h: No such file or directory
../src/sys/d3d11/gstd3d11window_swapchainpanel.cpp:32:10: fatal error:
  windows.ui.xaml.media.dxinterop.h: No such file or directory
```

`<windows.ui.xaml.media.dxinterop.h>` 是 Windows SDK 的 XAML/DirectX interop
头，mingw-w64 头文件里**没有**（实测 ucrt64 / mingw64 两个前缀都没有）。
上游 `gst-libs/gst/d3d11/meson.build` 的 `d3d11_winapi_app` 探测只判"这是不是
Windows 10+ 的 UWP 目标"，不判这两个 WinRT 窗口源能否真编译：

```meson
d3d11_winapi_app = cxx.compiles('''#include <winapifamily.h>
    #include <windows.applicationmodel.core.h>
    ...
    #if (WINVER < 0x0A00)
    #error "Windows 10 API is not guaranteed"
    #endif''', ...)
```

探测通过 → `d3d11_sources += winapi_app_sources` → 编 corewindow /
swapchainpanel → 缺头 fatal error。

**为何只有 ucrt64 挂**：该探测的成败取决于**前缀的 `_WIN32_WINNT` 默认值**，
同一份 `_mingw.h` 在两个前缀里不同：

```
ucrt64/include/_mingw.h : #define _WIN32_WINNT 0xA00   → WINVER >= 0x0A00，探测 YES
mingw64/include/_mingw.h: #define _WIN32_WINNT 0x601   → 探测 NO，跳过 WinRT 源
```

即 ucrt64 因默认目标已是 Windows 10 而"误判"自己支持 WinRT，mingw64 因默认
目标较低而侥幸绕过——与 gl-headers 那次一样，是"本机/另一目标过、CI 挂"的
同族问题。（`gst-libs/gst/winrt`、`sys/mediafoundation` 也各有 winapi 探测，
但它们都额外要求 MSVC 或 `memorybuffer.h` 等缺失头，故未在本链触发。）

修复：新增 `patches/gst-plugins-bad-0001-check-xaml-interop-header.patch`，把
缺失头加进探测，使探测回归其真实语义（"这两个 WinRT 源能否编译"）：

```diff
   d3d11_winapi_app = cxx.compiles('''#include <winapifamily.h>
       #include <windows.applicationmodel.core.h>
+      #include <windows.ui.xaml.media.dxinterop.h>
       #include <wrl.h>
```

MinGW 下探测转为 NO（头不存在）、MSVC 下仍为 YES（SDK 带该头），故只编 Win32
桌面窗口路径。GCC 无损失：UWP 的 CoreWindow/SwapChainPanel sink 在 Win32 桌面
程序里本就不可用。做法与 MSYS2 的 `0004-check-d3d11-header.patch` 一致。

验证（msys2-ucrt64，删除工作区后完整重跑 `gst-plugins-bad`）：`meson` 探测输出
由 `building for WinRT ... YES` 变为 **NO**，编译 260 个目标零错误、`install`
完成，产物 `libgstd3d11.dll` / `libgstd3d12.dll` 与 `gstreamer-d3d12-1.0.pc`
（GTK media-gstreamer 的硬依赖）到位，日志中不再出现 dxinterop 报错。

## 运行期数据包（2026-09-27 新增）

- **shared-mime-info 2.4**：`-Dupdate-mimedb=true` 在安装后生成
  `share/mime/mime.cache`。此前 sysroot 里没有任何 mime 库，GIO 的
  `g_content_type_guess()` 基本失效（文件选择器的 MIME 过滤器、拖放类型判定、
  默认应用查找都受影响）。自带测试 8 项全过；实测 `gio info -a
  standard::content-type` 的结果与 MSYS2 系统 mime 库逐项一致。
- **adwaita-icon-theme 49.0**：`share/icons/Adwaita` 共 799 个文件（713 SVG），
  post_install 用 gtk4-update-icon-cache 生成 `icon-theme.cache`。归档里的 2 个
  symlink（如 `scalable/status/folder-open.svg`）在本机无符号链接权限时由
  CPython tarfile 自身退化为"复制目标内容"（`TarFile.makelink_with_filter`），
  无需额外处理，两个图标实测到位且内容与目标一致。

## 本次验证范围（2026-09-27，msys2-mingw64）

- 逐包重跑并通过：gdk-pixbuf 20/20、libogg / libopus / libvorbis（编译+安装）、
  libvpx、directxmath、gl-headers、shared-mime-info 8/8、
  adwaita-icon-theme、gstreamer / gst-plugins-base / gst-plugins-bad /
  gst-plugins-good（以 gst-inspect 校验插件与元素）、gtk（自带测试 +
  `libadwaita-demo --smoke` 退出码 0）。
- gdk-pixbuf 的 `-Dothers=enabled` 是**负结果**：产物侧确实生效（BMP/ICO/XPM/
  TGA 等 loader 进入 libgdk_pixbuf），但自带测试 `pixbuf-randomly-modified`
  出现上游断言（`gdk_pixbuf_animation_get_height: assertion
  'GDK_IS_PIXBUF_ANIMATION'`）且同一批变异输入里 24/27 变成 >8s 的病态慢解析，
  未达测试门槛，已回退；完整证据写在 `recipes/gdk-pixbuf.yaml` 注释里。
- sysroot DLL 数由静态化后的 28 增至 **65**（其中 gst 相关 37、gst 插件 24），
  原因是媒体链按上游设计必须动态；非媒体链的包仍是"仅 .a"。
- **未做**：清空 `out/` 的从零全链重建（时间不允许），上列结果均为增量重建 +
  项目自带测试的实测；msys2-ucrt64 未同步本次改动。
