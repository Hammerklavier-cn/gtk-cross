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
- `autotools.raw_configure`：手工 configure（libvpx、openssl）不注入
  `-C/--host/--build` 与共享/静态开关。两类动机都要防：libvpx 的 configure 对
  未知参数直接 `die_unknown`（响亮）；openssl 的 `./config` 一律退出 0 而
  `--disable-shared` **根本不生效**（静默，实测生成的 Makefile 里仍有 236 处
  `libcrypto.so`），库形态只能用上游关键字 `no-shared` 由 recipe 写全。

### 2026-09-29 追加

- `autotools.script`：配置入口脚本名，默认 `./configure`（raw 与常规两支都用它）。
  OpenSSL 的入口是 `./config`（在 3.5.8 里就是 `exec "$THERE/Configure" "$@"`）。
- `autotools.install_target`：安装目标名，默认 `install`。OpenSSL 必须换成
  `install_sw` —— 默认 `install` 含 `install_ssldirs`，它会往运行期绝对路径
  `$(OPENSSLDIR)`（`/etc/ssl`）装 `openssl.cnf`。
  两个新键缺省时命令串逐字节不变（Windows 快照实测零差异）。
- 下载内容校验（`gtkcross/download.py` 的 `archive_kind`）：`fetch` 在新下载后先确认
  内容是可识别的 tar/zip 再比哈希。起因是一个 404 的 HTML 错误页顶着
  `openssl-3.5.8.tar.gz` 的文件名被 `lock` 写进了 versions.lock.yaml（当时没有预期
  哈希可比，"算出即接受"）。护栏测试 `tests/test_download_guard.py`；被拒绝的内容
  不会留在 `downloads/`，否则下次会走"缓存命中"绕过校验。
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

## linux-native 原生编译：门控机制与首跑记录（2026-09-28）

### 为什么必须先有门控

Linux 原生目标一上手就撞上两条硬事实（均为实测上游，非推测）：

1. **`-Wl,--undefined=_tls_used` 会让 Linux 全线链接失败**——`_tls_used` 是 PE
   的 TLS 目录锚点，ELF 没有这个符号。它原先在 glib / glib-base / cairo 三个
   recipe 的 base 里，属于"Windows 修复被全局启用"的典型。
2. **GTK 在 Linux 上必须至少开一个窗口后端**——`gtk/meson.build` 在非 win32
   宿主执行 `win32_enabled = false`，而 `gdk/meson.build` 有
   `if gdk_backends.length() == 0 error('No backends enabled')`。旧配方在 base
   里把 x11/wayland/broadway 全写 `false`，在 Linux 上必然 configure 失败。

同类项共 22 处（PE TLS 链接参数、DirectWrite、gl_winsys=win32/gl_platform=wgl、
d3d11/d3d12 + DirectX 依赖、directsound、schannel、libvpx 的
`--target=x86_64-win64-gcc`、Windows 专属补丁、盘符类 known_failures 等），
逐项依据见 [recipes/platform-notes.md](recipes/platform-notes.md) 与
[patches/README.md](patches/README.md)。

### 机制：targets: 选择器 + target_os

- `toolchains/*.yaml` 新增 `target_os`（windows | linux；缺省回退到 `host`）。
  家族名按**目标 OS** 匹配而不是按宿主，未来 linux→mingw 交叉目标仍会正确
  拿到 Windows 侧补丁。
- recipe 的 `targets:` 支持两种形态：旧的映射（键 = 选择器）继续可用；新增
  列表 + `for:`，一个块的 `for:` 可列**多个**选择器（家族名或精确 target 名），
  多个 target 共用一套条目，不必逐 target 抄写。
- 合并语义按"base 只放平台中立项"的约定定为：`patches` / `deps` /
  `submodules` / `meson.options` / `autotools.configure` /
  `test.known_failures` **一律追加且去重**（共享块只增不减）；dict 深合并、
  叶子按 key 覆盖；标量覆盖；家族块先应用、精确 target 块后应用。
- 语义缺陷由测试抓到并修正一处：最初把"精确 target 块"按旧的**替换**语义处理，
  于是 `for: [msys2-mingw64, msys2-ucrt64]` 会静默丢掉 base 的补丁/依赖——
  正是这次改造要消灭的问题类别。改为统一追加 + 去重。

### 三处必须先修的门控阻塞点（不修则机制不成立）

| 位置 | 症状（若不修） | 修法 |
| --- | --- | --- |
| `Builder.order()` | 依赖图用未解析的 base `deps` 建图：家族块裁不掉 directx-headers/directxmath，Linux 的 build plan 仍会带上 Windows 专属包 | 图改为 `r.for_target(target, target_os).deps`；`cmd_graph` 同步按目标解析 |
| `Toolchain.run()` | 无条件注入 `MSYSTEM`（缺省 MINGW64），Linux 宿主的上游脚本按 `test -n "$MSYSTEM"` 判定 MSYS2 而走错分支 | 仅 `host: windows` 时注入 |
| `Builder._pc_is_static()` | 硬编码 `libNAME.dll.a`：Linux 上共享包若同时有 `.a` 会被误判为静态并把 `Libs.private` 提升进 `Libs` | 改由 `Toolchain.shared_suffixes`（windows→`.dll.a`，linux→`.so`） |

另两处 Linux 侧结构性差异：`default_library: static` 下 `.a` 要链进 `.so`，
故 prelude 对非 Windows 目标注入 `CFLAGS/CXXFLAGS=-fPIC`；`XDG_DATA_DIRS` 在
Linux 上改为**前置** sysroot 而非独占（独占会丢掉 `/usr/share` 的图标主题）。
`toolchains/linux-native.yaml` 故意不写 `host_triple`，让 autotools 原生构建不传
`--host/--build`（避免 autoconf 误判交叉）。

### Windows 行为等价性怎么证的

本机是 Linux，无法实跑 msys2-* 目标，所以用**构建计划快照**：
`tests/windows-plan.golden.json` 记录 52 recipe × 2 Windows 目标的实际 configure
命令行、补丁/依赖列表、测试命令与环境、post_install、build plan 拓扑序；
`tests/test_windows_plan.py` 逐字段比对。生成器 `tests/gen_windows_plan.py`
复用测试里的 `capture_plan`（生产者与校验者必须是同一实现——此前独立写了一份
的生成器漏传 `target_os`，重新生成的基线里 Windows 选项全部消失，就是这个坑）。

门控改造那一轮对 `git archive HEAD` 导出的旧实现做 token 多重集比对：
**1238 个字段逐字节相同、22 处仅顺序不同、0 处真实差异**；`prelude` 与
`bash_argv` 完全一致。仅顺序的 22 处来源是家族块把选项追加到末尾、gtk 的
`deps` 里 directx-headers 移到尾部（拓扑序仍保证它在 gtk 之前：位置 37 vs 40），
以及由此引发的 build plan 平序调整。

### 首跑实测结论（Fedora 44，gcc 16.2.1 / meson 1.11.2 / cmake 4.3.0）

`build pango -t linux-native` 当时的计划是 18 recipe（**没有任何 DirectX 包**；
同一命令现在解析出 28 个，因为 cairo 在 Linux 上开始依赖 X11 栈）：
zlib → libffi → pcre2 → libiconv → gettext → glib-base → pixman → freetype →
expat → fontconfig → libpng → cairo → gobject-introspection → glib →
harfbuzz-base → harfbuzz → fribidi → pango。

已实测通过：

- 三种引擎都在 Linux 上跑通：cmake（zlib/pcre2/freetype/expat/libpng）、
  autotools（libffi/libiconv/gettext）、meson（glib-base/pixman/fontconfig/…）。
- **expat 自带测试 1/1 通过、libpng 37/37 通过**。这 37 项正是靠
  `libpng-0001-tests-against-static-library.patch` 才没被静默跳过——它在 ELF 上
  同样必要，实测支持"该补丁属跨平台、留在 base"的判断。
- libiconv / gettext 在 Linux 闭包内构建安装成功（本轮决定与 Windows 保持完全
  同一 closure，不因 glibc 自带 iconv/libintl 而裁剪）。

首跑撞出并修掉四类真实问题（都是实测，不是推测的风险）：

1. **glib-base 链接失败**：`libglib-2.0.so` 的 DT_NEEDED 出现 `libiconv.so.2`
   （glib 经 sysroot 的 `iconv.h` 用的就是 GNU libiconv），meson 链接
   `gtester` / `gobject-query` 时带 `-Wl,--no-undefined`，只有 `-L` 不够，报
   `ld: warning: libiconv.so.2 ... not found (try using -rpath or -rpath-link)`
   与 `undefined reference to libiconv_open`。
   修法：非 Windows 目标的 `LDFLAGS` 加 `-Wl,-rpath-link,$SYSROOT/lib`。
   这是"闭包保留 GNU libiconv"这一决定的直接代价；Windows 侧无此步。
2. **libffi 装到 `$prefix/lib64`**：其 configure 里
   `` multi_os_directory=`$CC $CFLAGS -print-multi-os-directory` `` 在本机返回
   `../lib64`，命中 `toolexeclibdir=$toolexeclibdir/$multi_os_directory`，
   破坏"sysroot 只有一个 lib 目录"的前提（meson 传 `--libdir=lib`、cmake 传
   `CMAKE_INSTALL_LIBDIR=lib`，autotools 侧此前没有对等约束）。
   修法：给 libffi 的 **linux 家族块** 传上游开关
   `--disable-multi-os-directory`。重编后实测 `out/linux-native/lib/libffi.so.8.5.0`
   且 `libffi.pc` 的 `toolexeclibdir=${libdir}`，`lib64/` 不再出现。
3. **glib 自带测试 3 项 SIGABRT**：`glib:spawn-test`、`glib:gschema-compile`、
   `glib:gsubprocess`（这三项由 `gtkcross-events.log` 的 `tests-unexpected` 事件
   记录；当轮 meson 汇总未落盘，故不引用其总数。修复后复跑：424 个顶层测试
   退出码全 0，`Ok: 418 / Fail: 0 / Skipped: 6`）。逐项实测后定位到**两个不同
   根因**，都不是产物缺陷：
   - *空环境的子进程找不到自建库*。`gschema-compile.c` 用
     `execve(argv[0], argv, (gchar *[]) { NULL })` 跑 `glib-compile-schemas`，
     `gsubprocess` 的 `/env` 用 `g_subprocess_launcher_setenv` 整张替换环境表，
     两者都不继承 `LD_LIBRARY_PATH`。直接复刻即复现：
     `env -i ./build/gio/glib-compile-schemas --strict --dry-run
     --schema-file …/no-default.gschema.xml` 报
     `error while loading shared libraries: libiconv.so.2`（exit 127）；
     `gsubprocess-testprog` 同样报 libiconv 缺失，随后断言 `ONE == NULL`。
     修法：Linux 目标的 `LDFLAGS` 再加 `-Wl,-rpath,$SYSROOT/lib`，让闭包**自定位**。
     Windows 上这些测试本来就过——PE 的 DLL 搜索含可执行文件所在目录，不看环境
     变量。副作用是正向的：sysroot 产物不再依赖 `LD_LIBRARY_PATH` 才能运行。
   - *诊断文案被翻译*。glib 从源码构建会把自己 `po/` 的产物装进 sysroot 的
     `share/locale`（实测有 `zh_CN/LC_MESSAGES/glib20.mo`），宿主 LANG 是
     zh_CN.UTF-8 ⇒ glib 工具输出中文诊断，而 `spawn-test.c:303` 断言的是
     `strstr (erroutput, g_strerror (ENOENT))`。实测同一二进制：中文 locale 下
     `/spawn/basics` FAIL，`LC_ALL=C.UTF-8` 下 `ok 1 /spawn/basics`。
     修法：给 glib 的 **linux 家族块** 设 `test.env: LC_ALL: C.UTF-8`
     （本机 `locale -a` 提供 `C.utf8`，`LC_ALL=C.UTF-8 locale charmap` 得 UTF-8），
     而不是登记成 known_failure——环境钉住后回归信号才为真。
4. **libtiff 的 cmake configure 直接失败**（`find_package(CMath REQUIRED)` →
   `Configuring incomplete`）。读 `cmake/FindCMath.cmake` 得到确切链条：先
   `check_symbol_exists(pow "math.h" CMath_HAVE_LIBC_POW)`（不带 -lm ⇒ 失败），
   再 `find_library(CMath_LIBRARY NAMES m)`——被本框架的
   `CMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY` 挡在 sysroot 内（sysroot 里当然没有
   libm）⇒ 空，于是两次探测都失败。Windows 上同一包同一探测是过的：mingw 的 spec
   文件默认就把 `pow` 链上了（libmingwex）。
   修法：Linux 目标的 `LDFLAGS` 追加 `-lm`。定位上这等于承认"ELF 的 libm 与
   Windows 的 CRT 同类"——它不是 target 侧依赖包，不该被 hermetic 隔离挡住，
   但也不该因此把 `MODE_LIBRARY` 放宽成 BOTH（那会让 libtiff 嗅到发行版的
   libjpeg/libpng，真正的 hermetic 破口）。实测 libtiff 4.7.2 通过，
   `libtiff-4.pc` 的私有字段照常提升。

### 前段链收尾（2026-09-29 凌晨，同一轮首跑）

把计划推到 `build pango gdk-pixbuf -t linux-native`（当时 19 recipe，含 harfbuzz/fribidi/
pango/gdk-pixbuf 与 Linux 侧新增的两包）后，后半段又撞出三处**只在 Linux 成立**
的真实问题。全部按平台门控修，Windows 侧一条不受影响（快照 29 项测试仍全过）。

1. **gdk-pixbuf configure 失败**：`src/meson.build:209 ERROR: Dependency
   "shared-mime-info" not found`。读上游源码确认这不是可选特性——
   `if get_option('gio_sniffing') and host_system not in
   ['windows','darwin','android']` 分支里的 `dependency('shared-mime-info')`
   **没有** `required: false`，而 `use_gio_mime` 一旦为真就
   `GDK_PIXBUF_USE_GIO_MIME=1`。也就是说 Windows 目标根本不进这段，Linux 必须给。
   修法：`for: [linux] deps: [shared-mime-info]`（它的 `.pc` 装在
   `$SYSROOT/share/pkgconfig`，已在 `PKG_CONFIG_LIBDIR` 内），顺带把 libxml2
   拉进 Linux 闭包。它同时提供运行期 `share/mime/mime.cache`（实测 157560 字节），
   GIO 内容类型嗅探才有数据可查。
2. **gdk-pixbuf 第二个 Linux 硬依赖**：`src/meson.build:263 ERROR: Dependency
   "glycin-2" not found`。机制更隐蔽：`get_option('glycin')` 的声明默认值是
   `auto`，但上游包了一层
   `.enable_auto_if(host_machine.system() == 'linux')` ⇒ **只有 Linux** 把
   `auto` 提成 `enabled`，`required:` 随即变成真，缺包直接中止配置。Windows 上
   `auto` 保持 `auto`，找不到就静默不启用（第 9 条那套 loader 组合因此不变）。
   glycin 是 Rust 写的图像 loader daemon：上游 `subprojects/` 里没有它的 wrap
   回退（只有 gi-docgen/glib/libjpeg-turbo/libpng 四个），而本框架的引擎表只有
   meson/cmake/autotools（`gtkcross/builder.py`），没有 cargo 路径
   ⇒ 不可自建，`for: [linux]` 里显式 `-Dglycin=disabled`。
   副作用可控：`:289/:316/:360/:391` 的 `disable_auto_if(glycin_dep.found())`
   走反分支，png/jpeg/tiff/gif 仍用本闭包自建的 libpng/libjpeg-turbo/libtiff。
3. **libxml2 能 configure、却在链接工具时炸**：
   `libxml2.a(encoding.c.o): undefined reference to libiconv_open`（xmllint、
   xmlcatalog 两个可执行文件）。根因是 **CMake FindIconv 的探测不对称**：
   它判断"iconv 是否在 libc 里"用的是 `check_c_source_compiles`
   （cmake 4.3 `Modules/FindIconv.cmake:110`），那次编译只带环境里的
   `CFLAGS`/`LDFLAGS`——**CMake 不读环境的 `CPPFLAGS`**，所以 `-I$SYSROOT/include`
   不在里面，探测看到的是 glibc 的 `iconv.h`，判成
   `Found Iconv: built in to C library` ⇒ 不给任何库参数。而真正编译 `encoding.c`
   时走 target 的 include 目录，`find_path(Iconv_INCLUDE_DIR)` 受本项目
   `CMAKE_FIND_ROOT_PATH=$SYSROOT` + `MODE_INCLUDE=ONLY` 约束，命中的是**闭包内
   GNU libiconv** 的头（它把 `iconv_open` 宏重写成 `libiconv_open`）。两头不一致
   ⇒ 编译期要 libiconv_*，链接期不给 `-liconv`。
   修法：`for: [linux] cmake.defines: Iconv_IS_BUILT_IN=OFF`——FindIconv 的探测
   条件正是 `NOT DEFINED Iconv_IS_BUILT_IN`，显式定义即跳过误判，改走
   `find_path` + `find_library(NAMES iconv libiconv)`，两者都只在 sysroot 内搜，
   恰好拿到与被遮蔽同源的头和库。mingw 上不需要这条：msvcrt/ucrt 没有内置
   iconv，那次探测自然失败，结论本来就对。
   这是"闭包保留 GNU libiconv"（与 Windows 同一 closure 的决定）的**第二笔**
   直接代价，第一笔是 rpath-link/rpath。

**前段链最终实测**（当时 `out/linux-native` 23 recipe 有完整 stamp；全链完成后是 62，
见后面的 X11/Mesa 一节）：

| 项 | 结果 |
| --- | --- |
| 自带测试 | expat 1/1、libpng 37/37、gobject-introspection 65/65、glib **424 项 = 418 通过 + 6 跳过、0 失败**、fribidi 8/8、pango **29 项 = 27 通过 + 2 跳过、0 失败**、gdk-pixbuf 23/23、shared-mime-info 8/8 |
| pango 的 Linux 结论 | Windows 登记的 `test-font / test-fonts / test-font-data` 在 Linux 上**全部通过**，故未新增任何 `known_failures` |
| pango 的 2 项跳过 | **与平台无关**，读源码可确认：`test-shape` 迭代 `tests/shape/` 目录，而 1.58.2 的 tarball 里没有该目录（`tests/` 下只有 breaks/fonts/fontsets/itemize/layouts/markup-parse/nofonts…），`main()` 遇 `G_FILE_ERROR_NOENT` 直接 `return 0` ⇒ 0 个用例；`cxx-test` 是 C++ 编译链接冒烟程序，本身不注册 g_test 用例。两者在 Windows 上同样不会产出用例 |
| pango 用例总数 | junit 汇总 348 个子测试、failures=0 errors=0 —— 与 Windows 记录的 348 一致，说明测试集合相同，只是 Windows 上那 4 项失败在 Linux 为绿 |
| 闭包对比（前段链时点） | 当时三个 target 都是 **42** recipe，但**不是同一组包**：Windows 有 `directx-headers`/`directxmath`，Linux 有 `libxml2`/`shared-mime-info`。两个方向的差集已钉进 `tests/test_platform_gating.py`。（X11 栈与 Mesa 落地后 linux-native 变成 **62**，见下一节） |
| 产物 | 静态 `.a` 12、共享库主版本 23、可执行 47、`.pc` 46、typelib 29、无 `lib64/` |
| 端到端（空环境） | `env -i out/linux-native/bin/gdk-pixbuf-csource <png/jpg/jpeg/tiff 各一>` 全部成功产出 pixdata；`lib/gdk-pixbuf-2.0/2.10.0/` 下 loader 模块 0 个（`builtin_loaders=all` 生效，只有 loaders.cache）。这同时验证了 RUNPATH 自定位与自建 libpng/libjpeg-turbo/libtiff 的真实解码路径 |

> **计数口径**：上表的测试数字是**框架按测试名去重后的通过项数**（解析
> `meson test` 的进度行，取 `"suite - name"` 的后半段并去重），与本文件上方
> Windows 表同一口径，可直接横向比较。meson 自己的汇总对 glib 是
> `Ok: 418 / Skipped: 6 / Fail: 0`。两个数字的差已按 meson 的
> `testlog.txt` 逐步核对：424 个顶层测试 → 短名去重后 391 个（13 个名字跨
> suite 重复，如 `glib:max-version`/`glib:cxx` 各出现 4 次）→ 再减去 6 个
> skipped = **385**，与框架报出的数字一致，不是漏跑。
> Linux 的 glib 数（385）高于 Windows（309）是上游在 ELF 上注册了更多测试
> 可执行文件，与本项目配置无关。
>
> glib 的 skipped 与本轮改动**无关**，是上游的运行期条件跳过：424 个顶层测试
> 退出码全为 0（无一项以 77 退出），其 TAP 输出里共 76 个 `# SKIP` 子测试，
> 分布在 28 个测试中。实测原因样例：`glib:g-file-info-filesystem-readonly`
> 的 `# SKIP 'bindfs' and 'fusermount' commands are needed to run this test`
> （本机未装 bindfs）、`Not running timing heavy test`、
> `Skipping slow >4GB file test`、`Environment variable expansion is only
> supported on Windows`（这条正说明它是 Windows 专属分支，在 ELF 上跳过是对的）、
> `Failed to reproduce race (…) skipping test`（并发探测类）。框架不把 skipped
> 计为失败，也不需要登记。
>
> 原先失败的 3 项已**单独复跑**确认是真通过而不是跳过：`glib:spawn-test` 在
> 宿主 `LANG=zh_CN.UTF-8` 下仍 SIGABRT（即根因可复现），带
> `LC_ALL=C.UTF-8` 则 `OK — 2 subtests passed`；`glib:gschema-compile`
> 82 个子测试通过、`glib:gsubprocess` 84 个子测试通过。

> 本机 GitHub 直连在这一轮多次卡死（`Remote end closed connection`、以及
> `urlopen` 挂进不通的连接不返回），临时以 `HTTPS_PROXY=http://127.0.0.1:10808`
> 启动构建即恢复（框架的下载器用 urllib，会读该环境变量）。**没有**为此改动
> 仓库任何配置——换网络环境时不需要代理，也不需要把它写进 recipe。

### X11 客户端栈 + 自建 Mesa + GTK/libadwaita（2026-09-29 同日推进）

前段链打通后接着推全链，结果 **`build libadwaita -t linux-native` 闭包 62 recipe
全部构建、安装、测试通过**（`exit 0`，重跑幂等）。过程里撞出的阻塞点全部
按"读上游源码定位 → 平台门控修"处理，没有一处用 known_failures 掩盖。

**新增 21 个 recipe**：X11 客户端栈 17 个（util-macros、xorgproto、xtrans、
libpthread-stubs、xcb-proto、libxau、libxcb、libx11、libxext、libxfixes、
libxrender、libxi、libxrandr、libxcursor、libxdamage、libxinerama、
libxxf86vm）+ GL/EGL 侧 4 个（mesa、libdrm、libpciaccess、libxshmfence）。
逐项依据见 `recipes/platform-notes.md` 的 X11 与 Mesa 两节。

按发生顺序记录的五个真实阻塞点：

1. **X.org 的 configure 运行时要找 `xorg-macros.m4`**：框架的
   `CPPFLAGS`/`PKG_CONFIG_LIBDIR` 覆盖不到 `$prefix/share/aclocal`，必须显式
   `autotools.env: ACLOCAL_PATH=$SYSROOT/share/aclocal`，并把 util-macros
   列进每个 X 包的 deps。（框架早已支持 `autotools.env`，无需改动。）
2. **`xtrans` 是 libX11 1.8.13 的真实 pkg-config 依赖**：
   `Package requirements (xproto >= 7.0.25 xextproto xtrans xcb >= 1.11.1
   kbproto inputproto)` → `Package 'xtrans' not found`。旧说法"Xtrans 自 1.6
   起 vendored 在 libX11 里"对 1.8.x 不成立。
3. **gst 的 GLX 需要两处补**：`-Dgl_winsys=x11` 之外还必须 `-Dx11=enabled`
   ——base 里的 `-Dauto_features=disabled` 把 `x11` 这个 `value:'auto'` 的
   feature 一并关了，`src/meson.build:336` 的 `dependency('x11',
   required: get_option('x11'))` 整条被跳过（日志原文
   `Dependency x11 for host machine skipped: feature x11 disabled`），
   于是在 `gst-libs/gst/gl/meson.build:741` 报
   `Could not find requested X11 libraries`。另一处是 GL 实现本身（见第 4 条）。
4. **决定自建 Mesa**（本轮的方向变更）。链条是硬的：gst 的
   `cc.find_library('GL')` + `cc.has_header('GL/gl.h')`（`gl_api=opengl` 时
   缺任一即 error）→ `gstreamer-gl-1.0` 又被 gtk 以
   `required: get_option('media-gstreamer')` 硬要。Linux 上
   `GL/gl.h`+`libGL.so` 属系统图形栈（`dnf repoquery` 实测只命中
   libglvnd-devel 与 mingw 交叉头包），不像 Windows 那样天然来自工具链，
   所以"用宿主"在这里等于交出 GL ABI。**softpipe 不需要 LLVM**
   （meson.build 里写着 "requires LLVM" 的只有 llvmpipe/i915/r300-IGP/
   radeonsi/lavapipe；本机也没有 llvm-config/llvm-devel），因此配置为
   `-Dglx=dri -Degl=enabled -Dllvm=disabled -Dgallium-drivers=softpipe
   -Dvulkan-drivers= -Dvideo-codecs= -Dplatforms=x11`，`-j8` 约 50 秒建完。
   顺带把 EGL 的归属从 egl-headers 换成 Mesa（`egl-headers` 因此收进
   libepoxy 的 windows 块）。
5. **libadwaita 68 项测试首跑全部 SIGABRT**，报错只有一行且与本项目无关：
   `Gtk-WARNING **: Unknown key gtk-modules in /home/jimmy/.config/gtk-4.0/settings.ini`
   ——libadwaita 的测试环境自带 `G_DEBUG=fatal-warnings`，宿主桌面上 GTK3 时代
   的遗留配置就足以让每个测试进程 abort。修在框架侧：prelude 对非 Windows 目标
   设 `XDG_CONFIG_HOME="$SYSROOT/etc/xdg"`（与 `PKG_CONFIG_LIBDIR` 整体替换、
   `XDG_DATA_DIRS` 前置 sysroot 同一类隔离）。修完 68 项全过。

**本轮推翻/改正的上一轮结论**（都写进了 platform-notes，避免下一个人再踩）：

- "GL/EGL 实现（mesa）不在自建范围" → 已自建，理由与代价见上。
- "libepoxy 在没有 X11 时能否编译通过尚未实测" → 已实测：能建；
  但 `-Degl=no` 在 Linux 上会导致 GTK x11 后端**编译不过**
  （`gdk/x11/gdkdisplay-x11.c:61` 无条件 `#include <epoxy/egl.h>`），
  已改为两平台分块。
- "GTK4 在 Linux 上还会拉 at-spi2-core" → 证伪：gtk-4.24.0 根 meson.build 里
  grep `at-spi|dbus|accessibility` 零命中，4.24 的 AT-SPI 在树内实现。
- "本轮只验证不依赖显示的部分" → 不准确：libadwaita 的 68 项是**在宿主
  X.Org 会话上真跑并全过**的（证据：测试 stderr 里的
  `MESA-EGL: warning: DRI3 error`）。无头 runner 上不会通过，这是 CI 的
  唯一硬缺口。

**端到端验证**（都实测过）：`tests/libadwaita-demo` 仅靠
`PKG_CONFIG_LIBDIR` 指向 sysroot 就能配置编译；在 `env -i`（不设
`LD_LIBRARY_PATH`）下经**自建 gtk4-broadwayd** 跑 `--smoke` 退出码 0
——坑是 broadway 的 socket 在 `XDG_RUNTIME_DIR` 里，server 与 app 必须共用
同一个目录，否则 `Failed to open display`。另用只链接 sysroot 头/库的 C
程序验证 X11 与 GLX：`XRRQueryVersion` 得 1.6、`XineramaQueryExtension`
为真、`glXQueryVersion` 得 **1.4（来自自建 libGL）**。

**Windows 等价性**：本轮把 cairo 的 `-Dxlib/-Dxcb` 从 base 挪进 windows
家族块、把 libepoxy 的 `-Degl` 拆成分平台、并新增 21 个 recipe，快照两次
刷新（先证明原有 52 个 recipe **0 真实差异且 0 顺序差异**、4 个 build plan
拓扑序一致、prelude/bash_argv 一致，再重生成基线）。闭包差集已改成按
三类原因分组的集合断言；门控单测从 29 增至 32 项（新增
`test_cairo_x_backends_are_linux_only` 等），全过。

### 改名 linux-native、双架构 CI、platforms 归属（2026-09-29 同日第二轮）

用户裁决两件相关的事：target 名去掉架构（`linux-x64` → `linux-native`），CI 增加
`ubuntu-latest`（x86_64）与 `ubuntu-26.04-arm`（aarch64）两个容器。这两条合起来
是一个**断言**：同一份工具链与 recipe 定义在多种架构的原生宿主上都成立。要让它
不是空话，架构相关的取值必须交给宿主。

改名本身的机械部分：`linux-x64` 字面量共 51 处（README 19、NOTES 9、
platform-notes 9、gtk-cross.yaml 4、toolchain.py 1、门控测试 9）+ 文件名与
`name:`。真正的影响是 **sysroot 前缀变了**：`out/linux-x64` 里的 `.pc`、RUNPATH、
schemas 都嵌了绝对路径，`mv` 目录会造出一个自洽性被破坏的 sysroot，所以改名必然
要求全新重建（这也顺带成了"全量干净构建"的契机，见下面的隐性依赖）。

三处架构取值改造（依据全部来自上游源码，逐条见 platform-notes 的"架构中立"一节）：

| 位置 | 改法 | 判据 |
| --- | --- | --- |
| libvpx `--target` | Linux 不传 | `build/make/configure.sh:789` 用 `${CHOST:-$(gcc -dumpmachine)}` 推 `tgt_isa`，case 表覆盖 `aarch64*→arm64` |
| libvpx `--as=nasm` | 移进 windows 家族块 | 那段 nasm/yasm 探测只在 x86 分支；aarch64 的 `AS` 来自 `${CROSS}as`（gas），硬塞 nasm 会把 ARM `.s` 交给错误汇编器 |
| xcb-proto `PKG_CONFIG_PATH=/usr/lib64/pkgconfig` | 删除 | 本包 configure 用 `AM_PATH_PYTHON` 按 PATH 找解释器，`$PKG_CONFIG` 调用数 grep 为 0；而 `/usr/lib64` 连 x86_64 的 Debian 都不成立 |

新增 `platforms:` 归属键（`gtkcross/recipe.py` + `builder.py`）。动机是 CI 的
"全量枚举"在**两个方向**都会出问题：Linux job 若构建 egl-headers，它会与 Mesa
争抢 sysroot 里的 `include/EGL` 与 `egl.pc`；Windows job 若构建 libx11/mesa 则是
无意义产物。行为：请求级跳过并打印名单，`order()` 里"本平台的包依赖对侧平台的
包"直接 `ValueError`（`plan`/`graph` 会打成一行 `error:`）；而**对侧平台自己的包
的依赖边不检查**（第一版在这里误报过：
libdrm 在 Windows 上因它自己的 `platforms: [linux]` 依赖而炸，实际上 libdrm 根本
不在 Windows 的构建集合里），这条反向语义也有测试。

测试命令包装点 `GTKCROSS_TEST_WRAPPER`（`gtkcross/engines.py` 的 `test_wrapper()`）：
无头容器里 gtk 的 1 项与 libadwaita 的 68 个测试程序需要 DISPLAY。选"整条命令前缀"
而不是 meson 的 `--wrapper`：后者按测试程序逐个包 ⇒ libadwaita 起 68 个 Xvfb；
且 ctest 没有 `--wrapper`。本机**未装** `xorg-x11-server-Xvfb`，所以真实 Xvfb 路径
还没跑过：用 `GTKCROSS_TEST_WRAPPER="env"`（一个无副作用的前缀）实测了管道本身
——expat（ctest 路径）OK=1、fribidi（meson 路径）OK=8，前缀确实落到命令上；
`doctor` 会在前缀命令不存在时直接报出。要本机复现 CI 的显示条件：
`sudo dnf install xorg-x11-server-Xvfb`。

**全量干净构建抓到三个隐性依赖**（本机以前只跑 `build libadwaita` 的增量链，
sysroot 里有上次留下的 .pc，所以从未暴露；CI 的干净构建是这类问题的兜底）：

| recipe | 症状 | 根因与修法 |
| --- | --- | --- |
| mesa | `Dependency "xrandr" not found`（`src/meson.build:2375`） | `-Dxlib-lease` 默认 auto 命中 `VK_EXT_acquire_xlib_display`，而本闭包不建 Vulkan 驱动 ⇒ 显式 `disabled`；同处还发现 `dependency('xcb')`/`('xcb-randr')`（2308-2310）无 `required:false` 却没声明 ⇒ deps 补 `libxcb` |
| vulkan-loader | `FindPkgConfig.cmake:1093: required packages were not found: - xrandr` | base 里写的 `BUILD_WSI_X11_SUPPORT` **上游不存在**（整树 grep 0 命中），一直是空转 -D，真名 `BUILD_WSI_XLIB_SUPPORT`/`..._XLIB_XRANDR_SUPPORT` 保持默认 ON，而其下 XRANDR 是 REQUIRED ⇒ 改用真名，Linux 打开三个 WSI 开关并补 `libxcb libx11 libxrandr`；Windows 不补（WIN32 分支不读这些，传了只得到"变量未被使用"警告） |
| libxi | `configure: error: Package requirements (xfixes >= 5) were not met` | `configure.ac` 的 `PKG_CHECK_MODULES(XFIXES, xfixes >= 5)` 是必需探测，以前靠 GTK 的 deps 里恰好有 libxfixes 且拓扑平序排在前面 ⇒ deps 补 `libxfixes` |

顺带把 X11 WSI 打开做了实测确认：`nm -D out/linux-native/lib/libvulkan.so.1` 导出
`vkCreateXlibSurfaceKHR` 与 `vkCreateXcbSurfaceKHR`（GTK 的 Vulkan 渲染器在 X11 上
需要它们）。注意这仍不等于"Vulkan 可用"：`-Dvulkan-drivers=` 为空，没有 ICD，
运行期取不到物理设备。

快照与测试的加强：`platforms` 进 `capture_plan` 的记录字段；**新增
`test_build_plans_unchanged`** ——`plans`（4 个入口的闭包拓扑序）以前只写进快照
却没人比对，等于没守；现在比对，所以"谁先装进 sysroot"这类变化会显式暴露
（正是隐性依赖那一类问题的探测器）。测试数从 32 增到 45。

本轮的 Windows 等价性证明（按字段分类，逐条可复核）：
`platforms` 字段 146 处新增；`libvpx` 与 `vulkan-loader` 的 Windows 命令行差异分别是
"选项移到家族块后的仅顺序变化（token 多重集相同）"与"消失了一个上游不认识的
空转 -D"；`mesa`（deps + 命令行）、`xcb-proto`（命令行）、`libxi`（deps 补
libxfixes）是真实变化，但这三个包都声明了 `platforms: [linux]`，实测不在
4 个 Windows build plan 的任何 plan 里；prelude、bash_argv、以及 4 个 plan 的
拓扑序逐字节相同。

### 待办

- ~~前段链后半的测试结论与 known_failures 需按 Linux 实测重新登记~~ 已完成。
- ~~GTK/libadwaita：先以 broadway 后端把 GTK 编出来，再补 X11/Wayland 客户端栈~~
  已完成：X11 后端 + broadway 都开，GTK/libadwaita 全链通过（见上一节）。
- ~~CI 的 linux job~~ **已加**：`ubuntu-latest` + `ubuntu-26.04-arm` 两个容器，
  测试显示靠 `GTKCROSS_TEST_WRAPPER=xvfb-run -a`。待 CI 首跑确认的点：arm64 上
  全部 recipe 是否真的架构中立（本机只有 x86_64，无法验证）、apt 名单是否有缺项
  （本机是 Fedora，装过的包看不出漏了哪个）。
- ~~vulkan-loader 的 XCB/X11 WSI~~ **已放开并实测到符号**（见上一节表格）。
- **Wayland**：唯一还没做的后端。硬卡点是 `gtk-4.24.0/meson.build:588` 的
  `dependency('wayland-egl')`（无 `required: false`，GTK 自带 wrap 里也没有
  wayland-egl），它属 Mesa 的 wayland 平台 ⇒ 顺序必须是
  `wayland → mesa(-Dplatforms=x11,wayland) → libxkbcommon(+xkeyboard-config)
  → wayland-protocols → gtk`（详见 platform-notes 末尾）。开了它，
  vulkan-loader 的 `BUILD_WSI_WAYLAND_SUPPORT` 才有意义。
- **本机验证 Xvfb 路径**：装 `xorg-x11-server-Xvfb` 后
  `GTKCROSS_TEST_WRAPPER='xvfb-run -a' gtkcross build libadwaita -t linux-native`
  应该与有桌面时同样 68 项全过。
- ~~curl 的 TLS 后端（Linux）~~ **本轮裁决并落地**：自建 OpenSSL 3.5.8（改动清单
  与两个引擎旋钮见下一节）。此前实测的"产物 `Enabled SSL backends:` 为空"是
  上一版 recipe 的结论，OpenSSL 并入后要重新确认那一行：构建日志里 curl 的
  `Enabled SSL backends:`（`CMakeLists.txt:2101` 的摘要输出）应为 `OpenSSL`，
  且 `pkg-config --static --libs libcurl` 应带出 `-lssl -lcrypto`（本 recipe
  `BUILD_CURL_EXE=OFF`，sysroot 里没有 curl 可执行文件，别指望 `curl -V`）。
- **Vulkan 可用性的另一半**：WSI 已通（导出 xlib/xcb surface），但
  `-Dvulkan-drivers=` 为空 ⇒ 没有 ICD，运行期取不到物理设备。要真正跑 Vulkan
  需要选自建驱动路径（panvk/virtio 都要 LLVM；lvp 也要），这是另一个边界判断。

## OpenSSL：linux-native 的 TLS 后端（2026-09-29）

用户裁决"Linux 上 curl 自建哪个 TLS 栈"→ OpenSSL。否决 mbedTLS（接入最省事，
但只服务 curl 一个消费者）与 GnuTLS（要为它放 gmp+nettle+libtasn1+gnutls 四个
包）；依据带行号记在 `recipes/platform-notes.md` 的「OpenSSL：curl 的 TLS 后端」。

**框架侧**：autotools 引擎加两个字段，都是 OpenSSL 的构建入口逼出来的 ——
`autotools.script`（入口脚本名，默认 `./configure`，raw 与常规两支都用）与
`autotools.install_target`（默认 `install`）。缺省时命令串逐字节不变，实测
Windows 快照的 `configure_cmd` 字段零差异。

**recipe 侧**：新增 `recipes/openssl.yaml`（3.5.8，`platforms: [linux]`）与
`versions.lock.yaml` 的对应锁；`recipes/curl.yaml` 的 linux 块改成
`CURL_USE_OPENSSL=ON` + dep `openssl`，并删掉那条在本平台不会被读取的
`CURL_USE_SCHANNEL=OFF`。windows 块与 base 未动 ⇒ Windows 闭包与命令行不变。

**上游实测的两个陷阱**（不写就出事）：
- 默认 libdir 是 `lib$target{multilib}`：x86_64 得 `lib64`、aarch64 得 `lib`
  ⇒ 同一份 target 定义在两个 CI 容器里产出不同布局。本机实测：把 `--libdir=lib`
  从 configure 命令里去掉重跑，生成的 Makefile 从 `LIBDIR=lib` 变 `LIBDIR=lib64`。
- 默认 install 目标含 `install_ssldirs`，它往运行期绝对路径 `/etc/ssl` 装
  `openssl.cnf`（破 hermetic，无 root 即失败）⇒ 必须 `install_target: install_sw`。
- 引擎常规支补的 `--disable-shared --enable-static` 会被 `./config` **静默忽略**
  （退出 0、不生效；实测生成的 Makefile 里仍有 236 处 `libcrypto.so`，只有上游
  关键字 `no-shared` 才是 0）⇒ 必须 `raw_configure` + 自己写全库形态。这类
  "声明与产物不符且无声"的变形比响亮失败危险得多。

**过程错误（记下防再犯）**：`openssl-library.org/source/binaries/…tar.gz` 用
`curl -sIL` 探返回 200、带 Range 的 GET 返回 206，真下载下来是 31,669 字节的
HTML 错误页；而框架的 `lock` 在没有预期 sha256 时"算出即接受"，于是那个 404 页
被当源码包写进了 `versions.lock.yaml`（现场发现并改回）。最终锁 GitHub 的 release
资产（53,213,818 字节，sha256 `a8f84a39…`）；`www.openssl.org/source/…` 实测 301
后落的也是同一资产，没有独立冗余价值。**待办**：给 `fetch()` 加一道"是不是可识别
的 tar/zip"的最低校验，否则 404 页会被静默锁进版本文件。

**验证状态**：`./config` 的整条参数在本机实测通过（仅 configure 阶段，回显
`Configuring OpenSSL version 3.5.8 for target linux-x86_64`）；**编译、安装、以及
OpenSSL 并进 curl 之后的链接都还没跑** —— 本轮按约定停在改动，构建由用户执行。
单测 45 → 50（新增 `OpensslTlsBackend`：入口脚本、install 目标、libdir、curl 两
平台各自的后端选择、两个新键的缺省行为）。快照差异按字段分类：`new-recipe` 2 处
（openssl 在两个 Windows target 的条目）、`test_cmd` 20 处（`-j` 随宿主核数漂移，
`tests/test_windows_plan.py` 现已把并行度与 `GTKCROSS_TEST_WRAPPER` 钉死，注释
归因于 CI 首跑的 20 项失败）、`configure_cmd` / `prelude` / `bash_argv` / `plans`
零变化。

## 环境变量透传与指纹失效（2026-10-28）

**需求**：`CC/CXX/CFLAGS/CXXFLAGS/CPPFLAGS/LDFLAGS` 等要能**有效**作用于
cmake 与 meson 的构建过程（原来"只设置"不生效，逐层原因见下）。配套文档：
README「自定义编译参数」一节；单测 `tests/test_env_flags.py`（16 项）。

**改前失效的三个层次**（每层都独立地让用户设置落空）：

1. **prelude 覆盖**：`export CC='gcc'` 等把调用方的 CC/CXX 整体盖掉；
   `export CPPFLAGS="-I$SYSROOT/include"`、`LDFLAGS=...`、`LIBRARY_PATH=...`
   也是覆盖式——用户加的参数全被丢弃（只是"传进了构建环境"而已，值已被换）。
2. **cmake 不读 CPPFLAGS**：CMake 从环境只取 CFLAGS/CXXFLAGS/LDFLAGS（且只在
   首次 configure 落缓存），CPPFLAGS 完全不看——用户放 CPPFLAGS 里的参数对
   cmake 包无效（libxml2 FindIconv 探测不对称同根，见 platform-notes 第 9 条）。
3. **configure 后固化 + stamp 跳过**：meson 只在**首次 setup** 读环境
   （`_set_default_options_from_env` 的 `if self.first_invocation:`，之后
   reconfigure 也忽略 env 变化），参数固化进 build 目录；而框架的
   `done/*.stamp` 让已配好的包直接跳过 configure——改了环境变量重跑 build，
   既不会重配也不会用新参数。

**改法**（都在框架层，recipe 零改动）：

1. **prelude 加性导出**（toolchain.py）：tools 循环改 `${VAR:-默认}`（只有未设置
   才落默认值）；CPPFLAGS/LDFLAGS/LIBRARY_PATH 改 `${VAR:+ $VAR}` 追加，sysroot
   参数保持在最前（hermetic 优先，用户的 -I/-L 排后）。
2. **cmake 显式注入**（engines.py CmakeEngine）：调用方设置了任一相关变量时，
   在 configure 命令里注入 `-DCMAKE_C_FLAGS="$CFLAGS $CPPFLAGS"`、
   `-DCMAKE_CXX_FLAGS="$CXXFLAGS $CPPFLAGS"`、EXE/SHARED/MODULE 三个
   `LINKER_FLAGS="$LDFLAGS"`（bash 内展开，含 prelude 合并进的 sysroot 参数）。
   **未设置任何变量时注入为空，命令与旧版逐字节相同**（这点由 golden 守住）。
3. **环境指纹失效**（builder.py）：`_ENV_TRACKED` 11 个变量算 sha256 指纹，
   存 `done/env-fp.stamp`；每次 build_recipe 先比对，变了就删 `build/` 目录
   （meson 的 coredata / cmake 的 CMakeCache 必须连目录删，光删 stamp 无效——
   记忆里反复踩过的坑）与全部阶段 stamp，autotools 另删 src 里
   `config.cache/config.status/config.log`（configure 带 -C，缓存会复用旧探测）。
   旧框架建的无指纹 workspace 首次构建也强制重配（保守：不知道当时用的什么参数）。
   data 引擎跳过（纯拷贝无编译参数）。

meson 侧**不需要**引擎改动：meson 首配本来就读环境变量（CFLAGS/CXXFLAGS/
CPPFLAGS → c_args、LDFLAGS → link_args），指纹失效保证 env 变化后 setup 会重跑，
就把这条链路补完整了。

**连带修的一个快照问题**：`capture_plan` 原来不清 `_ENV_FLAG_VARS`，宿主残留的
CFLAGS 会让 golden 失配；另外 `bash_argv` 里的 msys2 bash 路径 `str(Path(...))`
在 Windows 宿主生成反斜杠、Linux 生成正斜杠，golden 随生成平台漂移——`_rel()`
现在把 `\` 归一为 `/`。重新生成后 golden 差异**只有** prelude 22 行
（11 行 × 2 个 Windows 目标），configure_cmd/plans/bash_argv 零变化（环境未设置
时的命令逐字节保持——这正是"默认行为不变"的证明）。

**验证**：单测 77 → 93（`test_env_flags.py` 16 项：指纹稳定性/变化、prelude 加性、
cmake 注入三态、指纹失效四态含 data 跳过与 autotools 缓存清理）。

**真机冒烟（msys2-ucrt64，zlib 1.3.2，cmake 引擎）**，正好踩中"旧框架构建的
无指纹 workspace"迁移路径：
1. 旧 workspace（有 configure/compile/install stamp、无 env-fp.stamp）带上
   `CFLAGS="-O2 -march=x86-64-v2"` 首跑 → 报 `[env] zlib: 编译参数环境变化，
   废弃缓存重新 configure`，重新 configure/install；
2. `CMakeCache.txt` 里 `CMAKE_C_FLAGS=-O2 -march=x86-64-v2
   -I…/out/msys2-ucrt64/include` —— 用户参数与合并进来的 sysroot -I 都进了
   CMake 缓存，非仅打印；
3. 同环境重跑 → configure/compile/install 全部 `[skip]`（指纹匹配，幂等）；
4. 撤销 CFLAGS 重跑 → 再次 `[env]` 触发重配，`CMAKE_C_FLAGS` 恢复为空
   （回到默认），sysroot 不残留自定义参数。

## CI 编译参数变体与 --no-test（2026-10-28）

**需求**：发布时想给外部消费者**基线 / x86-64-v3 / x86-64-v4 三份** sysroot
资产，同时 CI 不能因 runner CPU 缺指令级而跳过 v4 的**构建**。

**关键事实**（决定设计的依据）：
- `-march=x86-64-v4` 含 **AVX-512**；gcc **编码**这些指令不需要目标 CPU 支持
  （-march 只影响指令选择），但**运行** v4 编译的程序会 SIGILL。
- GitHub 托管 runner **不承诺** AVX-512（actions/runner#1069 官方口径
  "DS2V2 Azure VM doesn't support AVX512"；community#183550 长期跟踪）。
  x86-64-v3（AVX2/BMI/FMA）则全线满足——社区甚至专门维护了
  "GitHub-Actions-X86-64-v3" 这类仓库证明 v3 可用。
- 本项目的"编译检查"隐含跑自带测试（configure 后自动 meson test/ctest），
  而 v4 测试程序在无 AVX-512 的 CPU 上必然 SIGILL——所以**测试**不能硬跑。

**设计**（`.github/workflows/ci.yml`）：
> 2026-10-30 更新：Windows 的 v4 已改为 `msys2-ucrt64 --force-cross`。
> 探针方案只处理测试执行，管不到 Meson `native: true` 的构建期生成器；
> 以下 Windows 原生 v4 部分保留为历史记录，现行设计见下一节。

- 矩阵变体：基线（无 CFLAGS/CXXFLAGS）+ v3 + v4，只挂 x86_64 可跑的
  target（Windows 侧 msys2-ucrt64、Linux 侧 linux-native/x86_64；mingw64 与
  aarch64 保持基线——CRT 差异/架构与指令级别正交，避免 CI 时长翻倍）。
  cflags 经构建步骤的 `env: CFLAGS/CXXFLAGS` 透传，基线空串与未设置等价。
- **AVX-512 探针步骤**（仅 v4 变体，`if: matrix.cflags == '-march=x86-64-v4'`）：
  编译并**实际运行**一个强制 AVX-512 指令的小程序，`supported=yes/no` 写
  `$GITHUB_OUTPUT`。用真实执行而不是读 /proc/cpuinfo：探针程序本身就是
  "v4 测试程序能不能跑"的最小复现，判定与测试同构。
- **构建照常、测试条件化**：v4 且探针 no → 构建命令追加 `--no-test`（资产
  照常导出发布，"未跑测试的 v4 sysroot"）；v4 且探针 yes / v3 / 基线 →
  完整构建 + 测试。探针不过时打 `::warning` 明示，不把环境差异伪装成失败。
- 发布资产带变体后缀：文件名与 `--archive-root` 都带（基线空串 → 与旧行为
  逐字节一致），三份同类资产解包互不覆盖；release job 的完整性检查从"通配
  符匹配任意一份"改为**逐份精确**验证 8 个文件（基线名是变体名的前缀，通配
  会漏检基线缺失）。

**框架新增 `--no-test`**（`gtkcross/cli.py` + `builder.py`）：
`Builder(skip_tests=True)` 时 `_apply_test` 直接返回，configure/compile/install
照常。与既有 `--ignore-test-failures` 的区别：后者**仍执行**测试命令只是失败
不中断（无 AVX-512 的机器上会得到满屏 SIGILL 噪音，且耗时）；前者连命令都不
发。单测 93 → 96（`tests/test_no_test.py`）。

**验证**：YAML 解析通过（矩阵 4+5+release 条目齐全）；96 项单测全绿（golden
不受影响：capture 本就走清空环境变量的路径）；真机 zlib 冒烟 `--no-test`：
configure/compile/install 照常 `[skip]` + `[skip] test（--no-test）`。

## 2026-10-28 交叉编译支持

新增 `linux-mingw-x64`、`linux-aarch64`、`linux-x64-v4` 三个 cross 目标，以及
仅给 cross 提供构建工具的 `linux-tools`；`linux-native` 增加
`--force-cross`（同架构强制 cross 语义，产物落 `out/linux-native-cross`）。

**机制**：
- Builder 生成 Meson cross-file 与 CMake toolchain file；autotools 用
  `--host/--build` 不同三元组。target flags 只进 target 编译，native 工具
  来自 `out/linux-tools`。
- Meson cross **不读环境 CPPFLAGS**：必须把 `-I<target-sysroot>/include`
  写进 cross-file `c_args/cpp_args`；CMake 同理。首次 smoke 的失败签名正是
  glib 的 `undefined reference to libiconv_open`（探测命中了宿主
  `/usr/include/iconv.h`）。
- 只给 cross-file 不够：GTK 用 `dependency('gio-2.0', native: true)` 取
  glib-compile-resources / glib-mkenums，native pkg-config 默认仍命中 target
  sysroot，于是解析到 target 二进制。现在额外生成 Meson native-file，把
  native pkg-config 指向 `linux-tools` 的 `.pc`；GTK/libadwaita/pango/
  gstreamer/gdk-pixbuf 的构建日志已复查为 `out/linux-tools/bin/...`。
- Meson 的 `cc.has_function()` / `cc.find_library()` 链接检查**既不用**
  `c_link_args`，也会过滤 `c_args` 里的 `-L`；Windows glib 的 intl 探测因此
  报 `cannot find -liconv`，最终触发 `libintl.type_name() == 'internal'`
  assert。修复是把 `-L<target-sysroot>/lib` 放进 cross-file 的 `c`/`cpp`
  编译器 argv（`c = ['x86_64-w64-mingw32-gcc', '-L…']`），这样 checker 的
  每条编译/链接命令都带搜索路径。
- cross 默认关 GIR/typelib、测试程序构建和 post_install；需要这些的消费者
  用同 OS/ABI 的基线资产，或提供能执行目标二进制的 exe_wrapper。
- target 侧 `gobject-introspection` 在 cross 中整体跳过：上游无条件用目标
  编译器编 `_giscanner` Python 扩展，却拿宿主 `Python.h`，aarch64/Windows
  必然失败（同 arch 的 v4 只是碰巧 pyconfig 路径存在）。cross 资产本来不含
  GIR，native tools 前缀仍有原生 g-i，故直接 `_CROSS_SKIP_RECIPES` 剔除。
- `shared-mime-info -Dupdate-mimedb=true` 会执行目标 `update-mime-database`；
  cross 下改成 false，并在 install 后用 native tools 里的同名工具生成
  `mime.cache`（数据与架构无关）。
- `adwaita-icon-theme` 的 Meson install script 也会执行 target
  `gtk4-update-icon-cache`（框架的 post_install 跳过管不到它）；cross-only
  补丁在 `meson.is_cross_build()` 时跳过缓存生成，GTK 运行期自行扫目录。
- `appstream` 只是全量枚举带进来的独立包（libadwaita 1.10 已 vendored
  ministream），cross setup 要求 native appstream；cross 资产没有消费者，
  放进 `_CROSS_SKIP_RECIPES` 跳过。
- CMake cross 的 `try_run` 用固定失败的 `cross-no-run.sh` 作为
  `CMAKE_CROSSCOMPILING_EMULATOR`，避免 configure 直接 exec 目标码。

**Linux v4 资产改由 cross 产出**：native v4 的 `g-ir-scanner` dumper 会在
无 AVX-512 的 runner 上 SIGILL；`linux-x64-v4` 用 `needs_exe_wrapper=true`
的 cross 构建，构建期不执行 v4。Windows UCRT v4 起初仍走原生 job；该路线
在 fribidi 构建期生成器上失败，已于 2026-10-30 改为
`msys2-ucrt64 --force-cross`（见下一节）。

**宿主工具缺口**：`linux-tools` 的 native `gobject-introspection` 在容器里
首次失败于 `Program 'flex win_flex' not found`；CI 的 Linux / linux-tools /
linux-cross 宿主清单已显式补 `bison flex`，不再依赖 runner 镜像的隐式包。

**本地验证**：`linux-tools` 33 recipe 通过；`linux-x64-v4` 的 `glib-base`
（6 recipe）与 `gobject-introspection` 闭包（23 recipe）通过，产出 ELF
x86-64 且编译命令带 `-march=x86-64-v4`；完整 `libadwaita` 闭包通过，并
复查所有 Meson 日志不再解析 target 的 glib tools。g-i 的 target 侧在
cross 中已整体跳过。

`linux-aarch64` 在 Ubuntu 24.04 容器里 **69 recipe 全链通过**（跳过 target
g-i/appstream），`libgtk-4.so` 实测 `ELF64 AArch64`，`libvpx.a`/`libssl.a`
也由 target 编译。`linux-mingw-x64` 在同一容器跑到 `gst-plugins-bad` 通过；
GTK 阶段被 mingw-w64 11 的 `rpcndr.h`/DirectX-Headers 版本不匹配挡住，
不是代码问题：CI cross job 已固定 `ubuntu-26.04`（mingw-w64 13），并给
`doctor` 加了 `d2d1_3.h` 预检，旧头文件会直接报可操作错误。


## 2026-10-30 Windows x86-64-v4 用 `--force-cross`（修正 10-28 的设计）

**失败现象**：CI `build (msys2-ucrt64 [x86-64-v4])` 在 fribidi 1.0.16 的
`gen-bidi-type-tab.exe` / `gen-joining-type-tab.exe` 上 exit 3221225501
（`0xC000001D` STATUS_ILLEGAL_INSTRUCTION）。`--no-test --no-introspection
--no-test-build` 只关测试执行、测试程序构建与 GIR，管不到 Meson
`native: true` 的构建期生成器；原生 Windows 构建的 CFLAGS 是全局的，
`-march=x86-64-v4` 也编进了这些生成器，无 AVX-512 的 runner 一运行就 SIGILL。

**修复**：复用 `msys2-ucrt64`，把 `--force-cross` 扩展到 Windows 宿主：

- `Builder` 去掉 Linux-only 限制；Windows 上仍要求 target/build 同 arch；
- `host_triple` 用工具链已有的 `x86_64-w64-mingw32`，`build_triple`
  内部补 `x86_64-pc-msys`（不暴露 triple/arch 配置）；
- Meson native file 显式写 `c = gcc` / `cpp = g++`（MSYS2 没有 cc 别名）；
- target flags 继续由 `CFLAGS`/`CXXFLAGS` 传入，只进 cross file；
  `native: true` 生成器用 build machine 的 baseline 编译器；
- recipe 新增 `for: [cross]` 选择器，只匹配 cross（含 `--force-cross`）；
  adwaita 的 icon-cache 补丁与 expat 的 `EXPAT_BUILD_TESTS=OFF` 不再按
  三个精确 cross target 名枚举；
- target 产物落 `out/msys2-ucrt64-cross`；同架构 `--force-cross` 默认把
  baseline `out/msys2-ucrt64` 当作 build machine 工具前缀（可用
  `--native-sysroot` 覆盖）。CI v4 job 先编 `glib-base gdk-pixbuf
  shared-mime-info shaderc`（23 recipe），再编 v4 全链；
- `doctor`/`env` 也接受 `--force-cross --native-sysroot`；`export
  --force-cross` 导出 `out/msys2-ucrt64-cross`；
- machine file 在 Windows 宿主里用 `C:/...`（Meson/CMake 是原生程序，不认
  bash 的 `/c/...`）；native pkg-config 的 `PKG_CONFIG_LIBDIR` 用分号；
- 发布资产名仍是 `gtk-cross-msys2-ucrt64-x86-64-v4-<tag>.tar.zst`。

**验证**：用最小 Meson 工程实测（本机 Linux，cross file hostcc / native
file buildcc）：`native: true` 目标只调用 buildcc，host 目标才调用 hostcc；
`CFLAGS=-DPROBE_V4` 只出现在 hostcc 命令行，buildcc 完全没有。`test_cross.py`
增加 Windows `--force-cross` 的 cross file / native file / triple 断言，
单测全绿；Windows CI 尚未跑，等首次 job 结果确认。

## 2026-10-31 libvpx 汇编事故与 Linux v4 统一到 `--force-cross`

**事故**：CI `cross (linux-x64 -> linux-x64-v4 (forced cross))` 构建 libvpx
1.17.0 失败：`vpx_ports/emms_mmx.asm.o` 报 `elf64: can't open elf64 for
reading` + 一串 `no such instruction`。签名即答案——实际执行的汇编命令是
`as -f elf64 …`：GNU as 把 `-f` 当自己的 fast 选项，`elf64` 成了输入文件名。

**根因**：`toolchains/linux-x64-v4.yaml` 的 `tools:` 声明了 `as: as`，prelude
把每个 tools 键大写导出（toolchain.py），即每条命令前都有
`export AS="${AS:-as}"`。libvpx 的手工 configure 只在 `AS` 为空/`auto` 时才
探测 nasm→yasm（build/make/configure.sh 的 x86 分支），环境里已有 `AS` 就
直接采用并跳过探测；Linux 分支随后 `add_asflags -f elf64`，于是 GNU as 拿
yasm 的参数格式去编 nasm 语法的 .asm。三个条件同时成立才炸：x86 目标 +
Linux 侧未传 `--as` + 环境预设了 `AS`——linux-native（不导出 AS → auto 选
nasm）、linux-aarch64（`--target=arm64-linux-gcc` 走 ARM gas 分支）、
windows 家族（recipe 钉死 `--as=nasm`）都不满足。CI 其实装了 nasm
（ci.yml 安装清单）。

**修复**：从 linux-x64-v4 的 tools 删除 `as: as`——同架构下宿主 as 即目标
as，导出纯冗余，删除即恢复 auto 探测；单测
`test_prelude_as_export_follows_arch_relation` 把"同架构不导出 / 异架构必须
导出 target binutils"两头钉死。

**统一**：事故的深层原因是 `linux-x64-v4.yaml` 整份 tools/env 是
linux-native 的复制品——复制品就会漂移。逐项比对确认 `linux-native
--force-cross` 在 x86_64 宿主上与专用 target 等价：合成的
`x86_64-unknown-linux-gnu`/`x86_64-pc-linux-gnu` 与静态值逐字节相同、native
前缀同为 `out/linux-tools`（gtk-cross.yaml 的 `cross_prefix`/`native_target`
早已备好）、`_CROSS_SKIP_RECIPES` 与 recipe 选择器归属一致、CFLAGS 在环境
指纹里（builder._ENV_TRACKED）。故删除专用 toolchain 文件与 target 注册，
Linux v4 与 Windows v4 统一为 `--force-cross` + CFLAGS 机制：CI linux-cross
矩阵条目改 `target: linux-native` + `cflags: "-march=x86-64-v4"` +
`cross: true`，doctor/build/export 三步都带条件 `--force-cross`（export
不带会去找不存在的 `out/linux-native` 直接报错），产物落
`out/linux-native-cross`；归档顶层与资产名不变，release 校验零改动。

**取舍**：专用 target 的好处是不带环境变量也能编出 v4；统一后 `-march`
完全依赖 CFLAGS，漏设会**静默**产出 baseline ISA（指纹不会报错）。兜底：
linux-cross 的产物检查步骤断言 `build/linux-native-cross/meson-cross.ini`
含 `-march=x86-64-v4`（cross file c_args 与 autotools configure 前缀同源于
`_target_cflags()`）。`linux-aarch64`/`linux-mingw-x64` 是真交叉（异架构/
异 OS），受 builder 的同架构守卫约束，专用文件保留。


## CI 修复：pngvalid 在 x86-64-v3 变体下随机 SIGSEGV（2026-11-01）

### 现象

`build (msys2-ucrt64 [x86-64-v3])` job（PR feat/flags-support，run 38 =
41111804771）：libpng 构建与 install 全部成功后，37 项 ctest 里
`pngvalid-progressive-interlace-standard`（`--standard --progressive-read
--interlace`）报 `(Failed)`，"97% tests passed, 1 tests failed"。同一个
run 里凡是真正跑了测试的 job 全绿：基线 msys2-ucrt64、msys2-mingw64、
linux-native 双架构、linux x86-64-v3；x86-64-v4 走 `--force-cross` 不执行
测试，没有对照样本。

日志里另一条当时无法定性的线索：pngvalid.c 有 12 条
`-Wstringop-overflow`（"writing 32 bytes into a region of size 17"，
`char message[256]`、偏移区间 `[239,256]`）。后文证实它与失败同源。

### 定位

**本机复现**：本机与 CI 同版本 gcc 16.2.0（UCRT64），同一套 cmake configure
与 defines，同一源码树，`CFLAGS=-march=x86-64-v3` 重编 pngvalid 后：同一
二进制连跑 60 次有 18 次 SIGSEGV、**零输出**（summary 行都来不及打印），
其余 42 次 PASS。参数逐字节相同、去掉 `-march` 的基线构建 65/65 全过。
问题因此收窄到 `-march=x86-64-v3` 进入 **pngvalid 测试程序自身**的代码
生成，而不是 CI runner 环境或库本体。

**崩溃指纹（gdb）**：

- SIGSEGV 落在 `ntdll!RtlUnwindEx → RtlVirtualUnwind2`，由
  `ucrtbase!longjmp` 调出；`bt` 溯源到 `make_error.constprop` ← `main`。
  pngvalid 的错误路径是**故意**触发的（`make_error` 验证 png_error 的
  行为），其 Try/Catch 用的是 `contrib/visupng/cexcept.h`，jmp_buf 就
  放在**栈上局部数组** `exception__env` 里。
- 在 `longjmp` 入口断点收集：通过的几次 `rcx`（jmp_buf 指针）都是栈地址
  （`0x5fef30`、`0x5fec00`）；崩溃那一次 `rcx = 0x7ffadeae0161` ——是
  **ntdll 的代码地址**，不是任何曾经初始化过的 jmp_buf。
- 解栈从当前 rsp 往上走，越过栈区顶端（StackBase ≈ `0x600000`）读取
  → 无映射 → 段错误。
- 同一份 jmp_buf 里仍有合法 `.text` 地址（如 `jb[10] = 0x7ff7a4c13ba1`），
  说明目标是**被篡改过的栈内容**，不是纯野指针。

这些都指向：pngvalid 自己的栈帧内存被写坏，jmp_buf（或异常链指针）被
覆盖 → longjmp 以非法目标 → NT 解栈越界 → SIGSEGV。非确定性来自"写坏的
是哪个相邻对象"，它随 ASLR / 栈布局 / 页边界变化——测试本身是固定种子
（`make_random_bytes` 的静态 seed，不含 `time()`），逻辑是确定性的。

**隔离矩阵**（Release 构建；崩溃数/运行数）：

| 测试程序 flags                              | libpng 静态库 | 崩溃率   |
| ------------------------------------------- | ------------- | -------- |
| 基线（无 `-march`）                         | 基线          | 0/40、0/60 |
| `-march=x86-64-v3`                          | v3            | **18/60** |
| `-march=x86-64-v3`                          | 基线          | 5/40     |
| 基线                                        | v3            | 0/40     |
| `-march=x86-64-v3` `-O2`                    | v3            | 0/40     |
| `-march=x86-64-v3` `-fno-tree-vectorize`    | v3            | 0/40、**0/60** |
| `-march=x86-64-v3` `-fno-tree-slp-vectorize`| v3            | 19/60    |
| `-march=x86-64-v3` `-fno-tree-loop-distribute-patterns` | v3 | 18/60 |
| `-march=x86-64-v3` `-ffp-contract=off`      | v3            | 12/40（排除 FMA 假说） |
| `-march=x86-64-v4`                          | v3            | 0/40（AVX-512 带掩码存储，未触发） |

**根因结论**：只有 `-O3` + AVX2 目标 ISA + **循环向量化**的组合会崩；SLP
向量化、loop-distribute-patterns、FMA 均无关（各自单独禁用后照样崩）。
肇事者是 pngvalid.c 里**某个**被循环向量化后的循环——不是 `safecat()`：
单独给 safecat 一家加函数级 `-fno-tree-vectorize`、其余循环保持 v3 向量化
时，同参数仍 21/80 崩溃；只有全测试单元关掉循环向量化才归零。具体是哪
一条循环在指令粒度上未被钉死，但两个体检事实是一致的：

- GCC 对这段代码的 `-Wstringop-overflow`（12 条）是"分析层"产物：把
  safecat/调用点形状单独提到探针里（`noinline` + 栈 canary，可复现到
  警告），告警声称的 32 字节越界写在运行时**不发生**（canary 完好，
  `pos = 255` 正常收口），且同样的告警在 `-O2`/`-march=x86-64-v4` 下
  不出现。警告与崩溃同消同长只是"都由循环向量化开关触发"，不能把警告
  直接当成肇事写。
- 崩溃现场（gdb）：ST 的 `pm.this.exception_context`（全局 png_store）
  的 penv 指针槽在崩溃前的 477 次全部为正常 Try/Catch 流动值
  （watchpoint 跟踪），故真实的错写发生在**帧内 jmp_buf 内容**或与
  解栈相关的相邻栈域。签名指向"某条向量化发射的写走到
  cexcept jmp_buf 所在的栈区"或对 setjmp 作用域变量的错误处理——
  后者是 GCC 自古以来（bug 66447，"all versions of GCC seem
  broken"）的家族。

**为什么 Linux 全绿不是侥幸**：pngvalid 的 `longjmp` 在 glibc 下只还原
寄存器，不经过 Windows 独有的 `RtlUnwind`/异常目录解栈；同样的内存篡改在
Linux 上即使发生也**不可能**在解栈器里崩——而 Windows 侧恰恰在解栈路径上
（上述崩溃现场），所以"签名只在 PE 上可见"。Linux x86-64-v3 job 37/37
仍然全过，据此不能反推 Windows v3 也该过。

### 修复

`pngvalid.c` 是 libpng 全树**唯一**使用 cexcept 的测试源（grep 证实）。
框架的 CFLAGS 管线把 CI 的全局 CFLAGS 喂给 cmake 的 `-DCMAKE_C_FLAGS`，
这里不碰它：在 libpng 自身 CMakeLists.txt 的测试段（补丁内）加一个
GNU 判定，只对 `${pngvalid_sources}` 关闭循环向量化（注意"单元"而非
单个函数——函数级 `__attribute__((optimize(...)))` 实测不足以根除，
且 GCC 16 已弃用 per-function 属性这条路）：

- 新补丁：`libpng-0002-pngvalid-drop-loop-vectorization.patch`
- 接线：`recipes/libpng.yaml`（base `patches:`，两个平台同打）
- 归属说明：`patches/README.md`「跨平台」表

MSVC 不经过 GNU 分支，构造不受影响。库的 28 个编译单元与 CI 的 CFLAGS
逐字节不变（仍 `-march=x86-64-v3 -O3 -DNDEBUG`），其余 6 个测试程序
同样不动。

### 验证（全新源码树 + CI 等价参数）

- 两个补丁 + `-DCMAKE_C_FLAGS="-march=x86-64-v3"`：崩溃测试连跑
  **200/200 全过**（修复前同条件 5/40–18/60）；
- `ctest -j4` 两轮：**37/37**，其余 36 项对 `-fno-tree-vectorize` 无回归；
- 构建输出 `-Wstringop-overflow` 从 12 条降为 **0**；
- build.ninja 里 FLAGS 分布：28 个文件保持
  `-march=x86-64-v3 -O3 -DNDEBUG`，仅 pngvalid 一家多出
  `-fno-tree-vectorize`。

### 备考

- 不把该测试登记进 `known_failures`：它不是环境瞬时问题，本地同参数必现
  （崩溃率 30% 量级），基线构建同样零失败，没有豁免的理由。
- pngvalid.c 自带 GCC bug 66447（setjmp 作用域变量误优化）的
  `gnu_volatile` 兼容修法——这一族代码对 GCC 的优化间隙天然敏感，回归
  任一优化点最先在它上面现形。上游（pnggroup/GCC）此次未另行提交；
  `-fno-tree-vectorize` 只是 scope 很窄的测试侧编译选项，GCC 后续修好
  或换更稳的 16.3 之后，该补丁最多变成无害冗余。
- 与本仓库其它"Windows 工具链 + PE/ABI 特有行为"的先例（gcc/bfd 的 TLS
  callback 家族 bug、cairo_win32_tls_callback 事故）同属一类：在同一份
  上游代码上，工具链差异足以改变可执行代码的稳定性，修复点必须落在"让
  目标组合下的代码生成保持确定性"，而不是换环境。
