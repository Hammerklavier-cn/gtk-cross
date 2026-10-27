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

导出只重定位 pkg-config 元数据；`lib/cmake/*.cmake` 与 libtool `.la` 中的
绝对路径不会改写，这个归档的消费面是 pkg-config（meson / cargo）。