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
`share/gir-1.0` 与 `lib/girepository-1.0` 覆盖进来。Windows 的 UCRT 资产仍由
Windows 原生 job 产出；`linux-mingw-x64` 是 msvcrt，不替代 `msys2-ucrt64`。

导出只重定位 pkg-config 元数据；`lib/cmake/*.cmake` 与 libtool `.la` 中的
绝对路径不会改写，这个归档的消费面是 pkg-config（meson / cargo）。