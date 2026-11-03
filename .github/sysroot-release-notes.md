## 使用方式

归档里的 `.pc` 前缀已改写为 `${pcfiledir}/../..`，解包到任意目录即可；
**不需要 `PKG_CONFIG_SYSROOT_DIR`**。

### Windows（交叉链接）

```bash
tar -xf gtk-cross-msys2-mingw64-<tag>.tar.zst
S=$PWD/gtk-cross-msys2-mingw64
export PKG_CONFIG_ALLOW_CROSS=1
export PKG_CONFIG_LIBDIR="$S/lib/pkgconfig:$S/share/pkgconfig"
export PKG_CONFIG_PATH=
export CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=x86_64-w64-mingw32-gcc
cargo build --target x86_64-pc-windows-gnu
```

- `msys2-mingw64` = msvcrt，配 `x86_64-pc-windows-gnu`
- `msys2-ucrt64` = UCRT，配 `x86_64-pc-windows-gnullvm` / llvm-mingw

运行期还需要 `bin/*.dll` 与 `share/`（glib schemas、icons、locale）、
`lib/gdk-pixbuf-2.0/2.10.0/loaders.cache`；它们必须随 exe 一起分发。

Windows 归档的 `bin/` 已包含 `gtkcross runtime`（CI 用 dep-pack 递归解析）从
**编译本 sysroot 的同一套 MSYS2 工具链**收集的运行库：`libstdc++-6.dll`、
`libgcc_s_seh-1.dll`、`libwinpthread-1.dll`、`libdl.dll` 等。直接拷贝
`bin/*.dll` 即可，**不要**再从 Linux 交叉链接器或其他 GCC 目录补运行库；版本
不一致会在加载 `libgstd3d12-1.0-0.dll` 等 C++ DLL 时报
`_ZSt15__get_once_callv` 入口点找不到。

`linux-mingw-x64` 资产由 Linux 侧的 mingw-w64 编译，其 `bin/` 不含这套运行库；
消费时应使用构建它时的同一发行版/同一版本的交叉工具链，并从该工具链取
`libstdc++-6.dll` 等运行库。

### Linux 原生

按宿主机架构选择 `gtk-cross-linux-native-x86_64-<tag>.tar.zst` 或
`gtk-cross-linux-native-aarch64-<tag>.tar.zst`：

```bash
tar -xf gtk-cross-linux-native-x86_64-<tag>.tar.zst
S=$PWD/gtk-cross-linux-native-x86_64
export PKG_CONFIG_LIBDIR="$S/lib/pkgconfig:$S/share/pkgconfig"
export PKG_CONFIG_PATH=
# 归档是重定位后的；.so 的 RUNPATH 仍指向构建机
export LD_LIBRARY_PATH="$S/lib"
```

Linux 运行期同样需要 `$S/share` 与
`lib/gdk-pixbuf-2.0/2.10.0/loaders.cache`。

### Linux 宿主交叉产物

- `gtk-cross-linux-mingw-x64`：Linux 宿主 -> Windows x86_64（mingw-w64 / msvcrt）
- `gtk-cross-linux-aarch64`：Linux x86_64 宿主 -> Linux aarch64（glibc）
- `gtk-cross-linux-native-x86_64-x86-64-v4`：Linux 宿主 -> x86-64-v4（cross）

这三份是**链接/运行用 sysroot**：库、头文件、`.pc` 与数据文件齐全，但
cross 构建默认不生成 GIR/typelib、不构建测试程序、不跑 post_install。需要
introspection 的消费者请使用同 OS/ABI 的原生基线资产，或把基线里的
`share/gir-1.0` 与 `lib/girepository-1.0` 覆盖进来。Windows 的 UCRT 基线与
v3 由 Windows 原生构建产出；x86-64-v4 由同一 Windows 宿主上的
`msys2-ucrt64 --force-cross` 产出（构建期不执行 AVX-512 目标码）。
`linux-mingw-x64` 是 msvcrt，不替代 `msys2-ucrt64`。

导出只重定位 pkg-config 元数据；`lib/cmake/*.cmake` 与 libtool `.la` 中的
绝对路径不会改写，这个归档的消费面是 pkg-config（meson / cargo）。