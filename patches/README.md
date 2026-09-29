# patches/ 补丁的平台归属

每个补丁按"它的动机来自哪个平台"分类。**只有 Windows 专属的补丁才被 recipe 的
`targets: - for: [windows]` 家族块引用**；跨平台补丁写在 recipe 的 base
`patches:` 里，两个平台都打。

判断依据是补丁正文实际改了哪条上游分支（不是文件名里有没有 "windows"）：
补丁改了 `if(WIN32)` / `os_win32` / `host_system == 'windows'` 分支或 Windows
专有源文件 ⇒ 只在 Windows 上有作用；补丁改的是上游与平台无关的分支（典型是
"静态构建时不产出共享变体"）⇒ 跨平台。

## Windows 专属（9 个，只在 windows 家族块生效）

| 补丁 | 归属 | 上游分支 / 生效条件 |
| --- | --- | --- |
| `fontconfig-0001-install-conf-d-as-regular-files.patch` | windows | 改 `conf.d/link_confs.py` 里 `os.symlink` 的 `NotImplementedError` / `winerror 1314` 兜底分支 |
| `fontconfig-0002-absolute-confdir-in-fonts-conf.patch` | windows | 去掉上游"剥离 prefix 变相对路径"的逻辑；Linux 上编译内置 `FONTCONFIG_PATH` 即 `$SYSROOT/etc`，相对 include 本就能落地 |
| `gst-plugins-bad-0001-check-xaml-interop-header.patch` | windows | 给 WinRT d3d11 探测加头文件判定；Linux 不编译该分支 |
| `gtk-0001-fallback-to-windows-locale.patch` | **msys2-mingw64**（精确 target，不是整个 windows 家族） | 只在 msvcrt 上 `setlocale("en_US.UTF-8")` 返回 NULL；ucrt64 认得该 POSIX 名字，不打 |
| `pango-0001-test-font-compare-normalized-path.patch` | windows | 折叠 `g_test_build_filename` 在 Windows 产出的混合分隔符 |
| `pango-0002-tests-use-installed-fontconfig.patch` | windows | 补丁本体自带 `if host_system == 'windows'` 分支，Linux 分支就是上游原样 ⇒ 打了也无变化，故门掉 |
| `shared-mime-info-0001-tests-absolute-bash.patch` | windows | 绕开 Windows 上 meson `ExternalProgram._shebang_to_cmd()` 把 shebang 剥成裸名 `bash` 的搜索顺序问题；Linux 由内核识别 shebang |
| `vulkan-loader-0001-static-library-support.patch` | windows | 只改 `loader/CMakeLists.txt` 的 `if(WIN32)` 段、`loader_windows.c` 的 DllMain、`if(WIN32)` 里的 `VULKAN_LIB_SUFFIX`；上游非 Windows 分支是 `add_library(vulkan SHARED)`，不受影响 |
| `zlib-0001-drop-windows-static-suffix.patch` | windows | 改的是上游 `if(WIN32) set(zlib_static_suffix "s")` 那一行 |

## 跨平台（3 个，base `patches:`，两平台都打）

| 补丁 | 动机 |
| --- | --- |
| `libpng-0001-tests-against-static-library.patch` | 本框架 `default_library: static` → `PNG_SHARED=OFF`，上游把测试段门控在 `PNG_SHARED` 上会静默跳过 37 项自带测试。与平台无关，与"静态优先"有关 |
| `spirv-tools-0001-skip-shared-variant.patch` | 静态构建下不产出 `SPIRV-Tools-shared`（上游无条件 `add_library(... SHARED)`） |
| `shaderc-0001-skip-shared-variant.patch` | 同上：不产出 `shaderc_shared`，并把 `shaderc.pc` 的 `Libs` 改成 `-lshaderc` |

这三条在 Linux 上**同样必需**（2026-09-29 在 linux-x64 实测，闭包 62 recipe 全部构建通过）：

- `libpng-0001`：不打就没有任何自带测试——Linux 上打完实测 **37 项**全过，说明
  `PNG_SHARED=OFF` 那道门控在 ELF 上与 PE 上一样生效。
- `spirv-tools-0001` / `shaderc-0001`：`out/linux-x64/lib/` 里只有
  `libSPIRV-Tools*.a` 与 `libshaderc*.a`，**没有** `libSPIRV-Tools-shared.so` 或
  `libshaderc_shared.so`；上游那句 `add_library(... SHARED)` 是无条件的，
  不打补丁就会装出共享变体。

## 未被任何 recipe 引用（1 个）

- `libadwaita-0001-remove-appstream.patch`：libadwaita 1.10.0 起 upstream 把
  appstream 换成 vendored 的 ministream，该补丁彻底失去用途（见 README 的
  "版本与来源说明"）。保留在仓库里作为历史记录，不引用；**不要**因为它在
  `patches/` 里就把它挂回任何 target。

## 新增补丁时的约定

1. 先确认补丁改的是哪条上游分支，再决定写进 base 还是家族块——写错方向会让
   一个平台的修复静默污染另一个平台。
2. 只改 Windows 分支的补丁若被 Linux 也应用，通常不会报错（分支不命中），
   但会让"这条修复属于谁"在 recipe 里不可见；本项目要求可见，所以一律门控。
3. 若某补丁在两个平台都需要但成因不同（例如各自的测试门控），拆成两个补丁
   文件、各挂各的块，不要写"一个补丁伺候所有平台"的复合条件。
