"""平台门控机制的单元测试：targets: 选择器、合并语义、Linux 目标配置。

覆盖三类回归风险：
1. Windows 专属项泄漏到 linux-native（补丁/选项/依赖/测试环境）；
2. 家族块与精确 target 块的合并语义写错（追加 vs 替换）；
3. toolchain 的 OS 家族识别与 Linux 侧 prelude 缺项（-fPIC、XDG_DATA_DIRS）。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from gtkcross import engines
from gtkcross.builder import Builder, _ENGINE_CLS
from gtkcross.config import ProjectConfig
from gtkcross.recipe import Recipe, load_recipes
from gtkcross.toolchain import Toolchain

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_TARGETS = ("msys2-mingw64", "msys2-ucrt64")
LINUX_TARGET = "linux-native"


def recipes():
    return load_recipes(ROOT / "recipes")


class Selectors(unittest.TestCase):
    """两种 YAML 形态与家族/精确块的合并语义。"""

    def _resolve(self, targets, target, os_family):
        r = Recipe(
            name="x", version="1", source={"url": "u"}, build="meson",
            deps=["base-dep"], patches=["common.patch"],
            meson={"options": ["-Dneutral=true"]},
            targets=targets,
        )
        return r.for_target(target, os_family)

    def test_mapping_form_still_works(self):
        r = self._resolve({"t1": {"patches": ["only-t1.patch"]}}, "t1", "windows")
        # 映射形态（旧写法）同样按追加合并：base 条目不会被静默丢弃
        self.assertEqual(r.patches, ["common.patch", "only-t1.patch"])

    def test_family_block_appends(self):
        r = self._resolve(
            [{"for": ["windows"], "patches": ["win.patch"], "deps": ["win-dep"],
              "meson": {"options": ["-Dwin-only=1"]}}],
            "msys2-ucrt64", "windows",
        )
        self.assertEqual(r.patches, ["common.patch", "win.patch"])
        self.assertEqual(r.deps, ["base-dep", "win-dep"])
        self.assertEqual(r.meson["options"], ["-Dneutral=true", "-Dwin-only=1"])

    def test_family_block_not_applied_on_other_os(self):
        r = self._resolve(
            [{"for": ["windows"], "patches": ["win.patch"]}], "linux-native", "linux"
        )
        self.assertEqual(r.patches, ["common.patch"])

    def test_one_block_several_targets(self):
        body = [{"for": ["msys2-mingw64", "msys2-ucrt64"], "patches": ["both.patch"]}]
        for t in WINDOWS_TARGETS:
            with self.subTest(target=t):
                r = self._resolve(body, t, "windows")
                self.assertEqual(r.patches, ["common.patch", "both.patch"])
        r = self._resolve(body, "linux-native", "linux")
        self.assertEqual(r.patches, ["common.patch"])

    def test_exact_block_applies_after_family(self):
        """家族块先、精确块后：列表按序追加，dict 叶子由更具体的块决定取值。"""
        r = self._resolve(
            [
                {"for": ["windows"], "patches": ["fam-a.patch"],
                 "cmake": {"defines": {"GEN": "ON"}}},
                {"for": ["msys2-mingw64"], "patches": ["mingw-only.patch"],
                 "cmake": {"defines": {"GEN": "OFF"}}},
            ],
            "msys2-mingw64", "windows",
        )
        self.assertEqual(r.patches, ["common.patch", "fam-a.patch", "mingw-only.patch"])
        self.assertEqual(r.cmake["defines"], {"GEN": "OFF"})
        # 同一家族的另一个 target 不受精确块影响
        r2 = self._resolve(
            [
                {"for": ["windows"], "patches": ["fam-a.patch"]},
                {"for": ["msys2-mingw64"], "patches": ["mingw-only.patch"]},
            ],
            "msys2-ucrt64", "windows",
        )
        self.assertEqual(r2.patches, ["common.patch", "fam-a.patch"])

    def test_family_plus_exact_selector_is_idempotent(self):
        """一个块里同时写家族名和该家族的精确 target 名，条目不得翻倍。"""
        r = self._resolve(
            [{"for": ["windows", "msys2-mingw64"], "patches": ["w.patch"],
              "deps": ["wdep"]}],
            "msys2-mingw64", "windows",
        )
        self.assertEqual(r.patches, ["common.patch", "w.patch"])
        self.assertEqual(r.deps, ["base-dep", "wdep"])

    def test_cmake_define_differs_per_family(self):
        r = self._resolve(
            [{"for": ["linux"], "cmake": {"defines": {"TLS": "OFF"}}}],
            "linux-native", "linux",
        )
        self.assertEqual(r.cmake["defines"], {"TLS": "OFF"})

    def test_malformed_targets_rejected(self):
        bad = [
            (["not-a-mapping"], "列表项必须是映射"),
            ([{"patches": []}], "缺少 for:"),
            ("targets: str", "必须是映射或列表"),
        ]
        for value, _hint in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self._resolve(value, "t1", "windows")


class WindowsItemsDoNotLeak(unittest.TestCase):
    """linux-native 解析结果里不得出现任何 Windows 专属补丁/依赖/选项。"""

    WINDOWS_ONLY_PATCHES = {
        "fontconfig-0001-install-conf-d-as-regular-files.patch",
        "fontconfig-0002-absolute-confdir-in-fonts-conf.patch",
        "gst-plugins-bad-0001-check-xaml-interop-header.patch",
        "gtk-0001-fallback-to-windows-locale.patch",
        "pango-0001-test-font-compare-normalized-path.patch",
        "pango-0002-tests-use-installed-fontconfig.patch",
        "shared-mime-info-0001-tests-absolute-bash.patch",
        "zlib-0001-drop-windows-static-suffix.patch",
        "vulkan-loader-0001-static-library-support.patch",
    }
    # 这些补丁的动机是"静态优先"而非 Windows，Linux 同样需要
    CROSS_PLATFORM_PATCHES = {
        "libpng-0001-tests-against-static-library.patch",
        "spirv-tools-0001-skip-shared-variant.patch",
        "shaderc-0001-skip-shared-variant.patch",
    }
    WINDOWS_ONLY_DEPS = {"directx-headers", "directxmath"}
    WINDOWS_ONLY_MARKERS = (
        "_tls_used",
        "directwrite=enabled",
        "gl_winsys=win32",
        "gl_platform=wgl",
        "d3d11=enabled",
        "d3d12=enabled",
        "directsound=enabled",
        "x86_64-win64-gcc",
        "GETTEXTDATADIR",
        "GI_SCANNER_DISABLE_CACHE",
    )

    def test_no_windows_patches_on_linux(self):
        b = Builder(ProjectConfig.load(ROOT), LINUX_TARGET)
        for name in sorted(b.recipes):
            with self.subTest(recipe=name):
                r = b.recipes[name].for_target(b.target, b.target_os)
                leaked = self.WINDOWS_ONLY_PATCHES & set(r.patches)
                self.assertFalse(leaked, f"{name} 在 linux 上打了 Windows 补丁: {leaked}")

    def test_cross_platform_patches_stay_global(self):
        """静态优先带来的补丁在 Linux 上仍应生效（防止过度门控）。"""
        b = Builder(ProjectConfig.load(ROOT), LINUX_TARGET)
        for name in sorted(b.recipes):
            r = b.recipes[name].for_target(b.target, b.target_os)
            want = self.CROSS_PLATFORM_PATCHES & set(
                b.recipes[name].patches
            )
            with self.subTest(recipe=name):
                self.assertEqual(set(r.patches) & want, want)

    def test_no_windows_deps_on_linux(self):
        b = Builder(ProjectConfig.load(ROOT), LINUX_TARGET)
        for name in sorted(b.recipes):
            r = b.recipes[name].for_target(b.target, b.target_os)
            with self.subTest(recipe=name):
                self.assertFalse(
                    self.WINDOWS_ONLY_DEPS & set(r.deps),
                    f"{name} 在 linux 上依赖 Windows 专属包",
                )

    def test_windows_only_markers_absent_on_linux(self):
        b = Builder(ProjectConfig.load(ROOT), LINUX_TARGET)
        for name in sorted(b.recipes):
            r = b.recipes[name].for_target(b.target, b.target_os)
            blob = " ".join(
                (r.meson.get("options") or [])
                + [f"{k}={v}" for k, v in (r.cmake.get("defines") or {}).items()]
                + (r.autotools.get("configure") or [])
                + list((r.meson.get("env") or {}).keys())
                + list((r.test.get("env") or {}).keys())
            )
            with self.subTest(recipe=name):
                for marker in self.WINDOWS_ONLY_MARKERS:
                    self.assertNotIn(marker, blob, f"{name}: linux 上出现 {marker}")

    def test_windows_keeps_tls_and_directwrite(self):
        for target in WINDOWS_TARGETS:
            b = Builder(ProjectConfig.load(ROOT), target)
            for name in ("glib", "glib-base", "cairo"):
                opts = " ".join(
                    b.recipes[name].for_target(target, b.target_os).meson.get("options") or []
                )
                with self.subTest(target=target, recipe=name):
                    self.assertIn("_tls_used", opts)
            hb = " ".join(
                b.recipes["harfbuzz"].for_target(target, b.target_os).meson.get("options") or []
            )
            self.assertIn("directwrite=enabled", hb)

    def test_windows_keeps_directx_deps(self):
        for target in WINDOWS_TARGETS:
            b = Builder(ProjectConfig.load(ROOT), target)
            gtk = b.recipes["gtk"].for_target(target, b.target_os)
            bad = b.recipes["gst-plugins-bad"].for_target(target, b.target_os)
            with self.subTest(target=target):
                self.assertIn("directx-headers", gtk.deps)
                self.assertIn("directx-headers", bad.deps)
                self.assertIn("directxmath", bad.deps)

    def test_build_plan_excludes_windows_only_recipes_on_linux(self):
        """deps 门控必须作用到 build plan：Linux 计划里不该出现 DirectX 头包。"""
        b = Builder(ProjectConfig.load(ROOT), LINUX_TARGET)
        plan = b.order(["libadwaita"])
        self.assertNotIn("directx-headers", plan)
        self.assertNotIn("directxmath", plan)
        bw = Builder(ProjectConfig.load(ROOT), "msys2-ucrt64")
        planw = bw.order(["libadwaita"])
        self.assertIn("directx-headers", planw)
        self.assertIn("directxmath", planw)
        # Windows 专属：directx-headers/directxmath 是 gdk/win32 的 d3d12 路径要的；
        # egl-headers（Khronos EGL 头 + egl.pc）只在 Windows 需要，因为那里没有
        # 自建的 EGL 供给者，而 Linux 上 EGL 的头与库由本闭包自建的 Mesa 提供
        # （libepoxy 的 egl-headers 依赖因此收进 windows 家族块）。
        self.assertEqual(
            sorted(set(planw) - set(plan)),
            ["directx-headers", "directxmath", "egl-headers"],
        )
        # 反向差集按三类**实测触发原因**分组列出（每一组都能在对应 recipe 里找到
        # 那句上游强制要求的源码位置），这样将来闭包再变时必须说清属于哪一类，
        # 不会把"上游必需"和"我们的边界裁剪"混成一团。
        # 注：比的是集合——分组只是给读代码的人看原因，不代表顺序。
        self.assertEqual(
            set(plan) - set(planw),
            # 1) gdk-pixbuf 在非 Windows 宿主必需 shared-mime-info（连带其依赖）
            {"libxml2", "shared-mime-info"}
            # 2) X 客户端栈：GTK 的 x11 后端 + cairo 的 xlib/xcb 表面
            | {
                "util-macros", "xorgproto", "xtrans", "libpthread-stubs",
                "xcb-proto", "libxau", "libxcb", "libx11", "libxext",
                "libxfixes", "libxrender", "libxi", "libxrandr", "libxcursor",
                "libxdamage", "libxinerama",
            }
            # 3) GL 实现（自建 Mesa）及其前置
            | {"mesa", "libdrm", "libpciaccess", "libxshmfence", "libxxf86vm"},
        )


class LinuxToolchain(unittest.TestCase):
    """toolchains/linux-native.yaml 与框架的 Linux 分支。"""

    @classmethod
    def setUpClass(cls):
        cls.project = ProjectConfig.load(ROOT)
        cls.builder = Builder(cls.project, LINUX_TARGET)
        cls.tc = cls.builder.tc

    def test_target_os_family(self):
        self.assertEqual(self.tc.target_os, "linux")
        for t in WINDOWS_TARGETS:
            self.assertEqual(Builder(self.project, t).tc.target_os, "windows")

    def test_bash_and_path(self):
        self.assertEqual(self.tc.bash_argv(), ["bash", "-lc"])
        p = self.tc.base_path()
        self.assertTrue(p.startswith("$SYSROOT/bin:"))
        self.assertIn("/usr/local/bin", p)

    def test_prelude_has_pic_and_system_datadirs(self):
        prelude = "\n".join(self.tc.prelude())
        self.assertIn("-fPIC", prelude)
        self.assertIn('export CFLAGS=', prelude)
        self.assertIn('XDG_DATA_DIRS="$SYSROOT/share:/usr/local/share:/usr/share"', prelude)
        self.assertIn("$SYSROOT/lib/pkgconfig:$SYSROOT/share/pkgconfig", prelude)

    def test_windows_prelude_has_no_pic_and_exclusive_datadirs(self):
        for t in WINDOWS_TARGETS:
            tc = Builder(self.project, t).tc
            prelude = "\n".join(tc.prelude())
            with self.subTest(target=t):
                self.assertNotIn("-fPIC", prelude)
                self.assertIn('XDG_DATA_DIRS="$SYSROOT/share"', prelude)

    def test_sysroot_prefix_and_shared_suffixes(self):
        self.assertEqual(self.sysroot_name(), "linux-native")
        self.assertEqual(self.tc.shared_suffixes, [".so"])
        self.assertEqual(
            Builder(self.project, "msys2-ucrt64").tc.shared_suffixes, [".dll.a"]
        )

    def sysroot_name(self):
        return self.builder.sysroot.name


class GtkBackends(unittest.TestCase):
    """GTK 在 Linux 上必须有后端，否则 gdk/meson.build 直接 error。"""

    def opts(self, target):
        b = Builder(ProjectConfig.load(ROOT), target)
        r = b.recipes["gtk"].for_target(target, b.target_os)
        return " ".join(r.meson.get("options") or [])

    def test_linux_has_a_backend(self):
        opts = self.opts(LINUX_TARGET)
        enabled = [
            name for name in ("x11-backend", "wayland-backend", "broadway-backend")
            if f"-D{name}=true" in opts
        ]
        self.assertTrue(enabled, "linux-native 的 gtk 没有任何后端")

    def test_windows_disables_unix_backends(self):
        for target in WINDOWS_TARGETS:
            opts = self.opts(target)
            with self.subTest(target=target):
                self.assertIn("-Dx11-backend=false", opts)
                self.assertIn("-Dbroadway-backend=false", opts)


class LinuxOnlyItemsDoNotLeakToWindows(unittest.TestCase):
    """Linux 家族块的反方向守卫：为 ELF 补的条目不得出现在 msys2-* 计划里。

    这些条目都来自实测的 Linux 失败（不是预防性配置），门控写反方向同样会
    造成真实回归——例如 -Dglycin=disabled 到了 Windows 会改变 loader 组合。
    """

    def resolved(self, target, name):
        b = Builder(ProjectConfig.load(ROOT), target)
        return b.recipes[name].for_target(target, b.target_os)

    def test_libiconv_cmake_override_is_linux_only(self):
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                self.assertNotIn(
                    "Iconv_IS_BUILT_IN",
                    self.resolved(target, "libxml2").cmake.get("defines") or {},
                )
        self.assertEqual(
            self.resolved(LINUX_TARGET, "libxml2").cmake.get("defines", {}).get(
                "Iconv_IS_BUILT_IN"
            ),
            "OFF",
        )

    def test_gdk_pixbuf_glycin_and_shared_mime_info_are_linux_only(self):
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                r = self.resolved(target, "gdk-pixbuf")
                self.assertNotIn("-Dglycin=disabled", " ".join(r.meson.get("options") or []))
                self.assertNotIn("shared-mime-info", r.deps)
        r = self.resolved(LINUX_TARGET, "gdk-pixbuf")
        self.assertIn("-Dglycin=disabled", " ".join(r.meson.get("options") or []))
        self.assertIn("shared-mime-info", r.deps)

    def test_glib_test_locale_is_linux_only(self):
        env = self.resolved(LINUX_TARGET, "glib").test.get("env") or {}
        self.assertEqual(env.get("LC_ALL"), "C.UTF-8")
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                self.assertNotIn(
                    "LC_ALL", self.resolved(target, "glib").test.get("env") or {}
                )

    def test_libffi_no_multios_dir_is_linux_only(self):
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                self.assertNotIn(
                    "--disable-multi-os-directory",
                    " ".join(
                        self.resolved(target, "libffi").autotools.get("configure") or []
                    ),
                )
        self.assertIn(
            "--disable-multi-os-directory",
            " ".join(self.resolved(LINUX_TARGET, "libffi").autotools.get("configure") or []),
        )

    def test_cairo_x_backends_are_linux_only(self):
        """cairo 的 -Dxlib/-Dxcb=enabled 只能在 Linux 生效（Windows 侧仍是 disabled）。

        这条尤其重要：本轮把这两句从 base 挪进 windows 家族块、并在 linux 家族块
        里放开，解析后的 Windows 命令行顺序**恰好没变**（xlib/xcb 原本就是 base 的
        末两项，家族块也追加在末尾），所以计划快照守不住"挪错了家族"这种错误，
        必须有这条按平台的取值断言。
        """
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                opts = " ".join(self.resolved(target, "cairo").meson.get("options") or [])
                self.assertIn("-Dxlib=disabled", opts)
                self.assertIn("-Dxcb=disabled", opts)
                self.assertNotIn("-Dxlib=enabled", opts)
                self.assertNotIn("libx11", self.resolved(target, "cairo").deps)
        opts = " ".join(self.resolved(LINUX_TARGET, "cairo").meson.get("options") or [])
        self.assertIn("-Dxlib=enabled", opts)
        self.assertIn("-Dxcb=enabled", opts)
        self.assertNotIn("-Dxlib=disabled", opts)
        self.assertIn("libx11", self.resolved(LINUX_TARGET, "cairo").deps)

    def test_gtk_x11_backend_is_linux_only(self):
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                opts = " ".join(self.resolved(target, "gtk").meson.get("options") or [])
                self.assertIn("-Dx11-backend=false", opts)
                for xdep in ("libx11", "libxrandr", "libxxf86vm", "mesa"):
                    self.assertNotIn(xdep, self.resolved(target, "gtk").deps)
        opts = " ".join(self.resolved(LINUX_TARGET, "gtk").meson.get("options") or [])
        self.assertIn("-Dx11-backend=true", opts)
        # Wayland 仍必须显式关：dependency('wayland-egl')（gtk meson.build:588）
        # 无 required:false 也无 wrap 回退，其实现属 Mesa 的 EGL/wayland 侧。
        self.assertIn("-Dwayland-backend=false", opts)
        for xdep in ("libx11", "libxrandr", "libxinerama"):
            self.assertIn(xdep, self.resolved(LINUX_TARGET, "gtk").deps)

    def test_gst_gl_needs_mesa_on_linux(self):
        """gst 的 GLX 路径在本闭包里只能靠自建 Mesa（Windows 靠工具链的 opengl32）。"""
        r = self.resolved(LINUX_TARGET, "gst-plugins-base")
        self.assertIn("mesa", r.deps)
        self.assertIn("-Dgl_platform=glx", " ".join(r.meson.get("options") or []))
        for target in WINDOWS_TARGETS:
            w = self.resolved(target, "gst-plugins-base")
            self.assertNotIn("mesa", w.deps)
            self.assertIn("-Dgl_platform=wgl", " ".join(w.meson.get("options") or []))


class PlatformsApplicability(unittest.TestCase):
    """`platforms:` 归属——CI 的两个 job 都按 `list` 全量枚举，靠这条跳过对侧的包。

    没有它就必须在工作流里各抄一份排除名单，而名单必然与 recipe 漂移。
    """

    WINDOWS_ONLY = {"directx-headers", "directxmath", "egl-headers"}

    def setUp(self):
        self.project = ProjectConfig.load(ROOT)
        self.all = load_recipes(ROOT / "recipes")

    def names_with(self, family):
        return {n for n, r in self.all.items() if r.platforms == [family]}

    def test_windows_only_set_is_pinned(self):
        self.assertEqual(self.names_with("windows"), self.WINDOWS_ONLY)

    def test_linux_only_set_is_pinned(self):
        """X 客户端栈 + GL 供给链（21 个）；改动这里必须同时说明为什么。"""
        self.assertEqual(
            self.names_with("linux"),
            {
                "util-macros", "xorgproto", "xtrans", "libpthread-stubs",
                "xcb-proto", "libxau", "libxcb", "libx11", "libxext",
                "libxfixes", "libxrender", "libxi", "libxrandr", "libxcursor",
                "libxdamage", "libxinerama", "libxxf86vm",
                "libpciaccess", "libdrm", "libxshmfence", "mesa",
            },
        )

    def test_ci_enumeration_skips_exactly_the_other_side(self):
        """全量枚举时每个 target 跳过的集合，就是"只属于对侧平台"的那些。"""
        names = sorted(self.all)
        for target, want in (
            (LINUX_TARGET, self.WINDOWS_ONLY),
            ("msys2-ucrt64", self.names_with("linux")),
            ("msys2-mingw64", self.names_with("linux")),
        ):
            b = Builder(self.project, target)
            with self.subTest(target=target):
                self.assertEqual(set(b.not_applicable(names)), want)

    def test_no_plan_contains_an_inapplicable_recipe(self):
        """闭包里若混进对侧平台的包，order() 必须直接报错而不是产出错 sysroot。"""
        for target in (LINUX_TARGET, *WINDOWS_TARGETS):
            b = Builder(self.project, target)
            for root in ("libadwaita", "appstream", "gtk", "shared-mime-info",
                         "vulkan-loader", "gstreamer"):
                plan = b.order([root])
                for name in plan:
                    with self.subTest(target=target, recipe=name):
                        self.assertTrue(
                            b.recipes[name].applies_to(target, b.tc.target_os),
                            f"{root} 的闭包里混进了不属于 {target} 的 {name}",
                        )

    def test_dependency_on_foreign_platform_is_an_error(self):
        b = Builder(self.project, LINUX_TARGET)
        b.recipes["zlib"].deps = list(b.recipes["zlib"].deps) + ["egl-headers"]
        # ValueError 而不是 BuildError：这是 recipe 配置错误，`plan`/`graph` 也会
        # 走到这里，而 cli 的入口只把 (ValueError, KeyError) 打成一行 "error: …"。
        with self.assertRaises(ValueError) as ctx:
            b.order(["zlib"])
        self.assertIn("egl-headers", str(ctx.exception))

    def test_foreign_recipe_s_own_deps_are_not_checked(self):
        """反向护栏：对侧平台的包，它的依赖边与本平台无关，不能误报。

        libdrm/libpciaccess 都声明 `platforms: [linux]`；在 Windows 目标上检查
        "libdrm 依赖了一个 Windows 之外的包"毫无意义——libdrm 根本不在 Windows
        的构建集合里。检查只从**本平台要构建**的包出发。
        """
        b = Builder(self.project, "msys2-ucrt64")
        self.assertTrue(b.order(["libadwaita"]))

    def test_resolved_recipe_keeps_its_platforms(self):
        """for_target() 不能把归属信息弄丢（否则解析后的副本会被当成全平台包）。"""
        for target, os_family in ((LINUX_TARGET, "linux"), ("msys2-ucrt64", "windows")):
            b = Builder(self.project, target)
            with self.subTest(target=target):
                self.assertEqual(b.recipes["mesa"].for_target(target, os_family).platforms,
                                 ["linux"])


class ArchNeutralLinuxTarget(unittest.TestCase):
    """linux-native 一份定义要同时服务 x86_64 与 aarch64 宿主（CI 跑两个容器）。

    所以任何 recipe 在 linux 家族下解析出的构建参数里都不许出现 arch 字面量：
    arch 必须由**宿主**决定（autotools 自己探测三元组、libvpx 缺省 --target 时按
    `gcc -dumpmachine` 推、编译器 flag 交给上游），写进 recipe 就等于把 target
    钉死在一个架构上。历史上这里是两处硬编码：libvpx 的 `--target=x86_64-linux-gcc`
    与 xcb-proto 的 `PKG_CONFIG_PATH=/usr/lib64/pkgconfig`（后者还是 Fedora 专属布局，
    连 x86_64 的 Debian 都不成立）。
    """

    ARCH_LITERALS = (
        "x86_64", "aarch64", "amd64", "arm64", "armv7", "i386", "i686",
        "ppc64", "riscv", "loongarch", "sparc", "mips",
    )

    def test_no_arch_literal_in_any_linux_recipe(self):
        b = Builder(ProjectConfig.load(ROOT), LINUX_TARGET)
        for name in sorted(b.recipes):
            r = b.recipes[name].for_target(b.target, b.target_os)
            blob = " ".join(
                (r.meson.get("options") or [])
                + (r.autotools.get("configure") or [])
                + [f"{k}={v}" for k, v in (r.cmake.get("defines") or {}).items()]
                + [f"{k}={v}" for k, v in (r.meson.get("env") or {}).items()]
                + [f"{k}={v}" for k, v in (r.autotools.get("env") or {}).items()]
                + [f"{k}={v}" for k, v in (r.test.get("env") or {}).items()]
            )
            for token in self.ARCH_LITERALS:
                with self.subTest(recipe=name, token=token):
                    self.assertNotIn(token, blob, f"{name}: linux 上写死了 arch")

    def test_windows_may_still_name_arch(self):
        """反向护栏：别把这条规则误伤到 Windows（msys2 目标本来就只跑 x86_64）。"""
        b = Builder(ProjectConfig.load(ROOT), "msys2-ucrt64")
        opts = " ".join(
            b.recipes["libvpx"].for_target(b.target, b.target_os).autotools.get("configure") or []
        )
        self.assertIn("--target=x86_64-win64-gcc", opts)


class TestWrapperHook(unittest.TestCase):
    """无头容器用 GTKCROSS_TEST_WRAPPER 给测试命令加前缀（meson 与 ctest 两条路径）。"""

    def setUp(self):
        self.project = ProjectConfig.load(ROOT)
        self.builder = Builder(self.project, LINUX_TARGET)
        self.saved = os.environ.get("GTKCROSS_TEST_WRAPPER")

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("GTKCROSS_TEST_WRAPPER", None)
        else:
            os.environ["GTKCROSS_TEST_WRAPPER"] = self.saved

    def _cmd(self, recipe_name: str) -> str:
        b = self.builder
        r = b.recipes[recipe_name].for_target(b.target, b.target_os)
        engine = _ENGINE_CLS[r.build](
            r, b.tc, Path("/tmp/ws"), 4,
            default_library="static", prefer_static=False,
        )
        return engine.test_command()

    def _pick(self, build: str) -> str:
        """按构建系统挑一个**带测试**的 recipe（两条引擎路径各测一次）。"""
        b = self.builder
        for name in sorted(b.recipes):
            r = b.recipes[name].for_target(b.target, b.target_os)
            if r.build == build and r.test.get("enabled"):
                return name
        self.skipTest(f"没有 {build} 工程带测试可测")

    def test_no_wrapper_by_default_leaves_command_untouched(self):
        os.environ.pop("GTKCROSS_TEST_WRAPPER", None)
        self.assertEqual(engines.test_wrapper(), "")
        self.assertTrue(self._cmd(self._pick("meson")).startswith("meson test"))
        self.assertTrue(self._cmd(self._pick("cmake")).startswith("ctest"))

    def test_wrapper_prefixes_both_test_engines(self):
        meson_r, cmake_r = self._pick("meson"), self._pick("cmake")
        os.environ.pop("GTKCROSS_TEST_WRAPPER", None)
        bare = {n: self._cmd(n) for n in (meson_r, cmake_r)}
        os.environ["GTKCROSS_TEST_WRAPPER"] = "xvfb-run -a"
        self.assertEqual(engines.test_wrapper(), "xvfb-run -a ")
        for name, before in bare.items():
            with self.subTest(recipe=name):
                self.assertEqual(self._cmd(name), "xvfb-run -a " + before)

    def test_wrapper_sits_outside_recipe_test_env(self):
        """recipe 的 test.env 由 `env K=V` 注入，包装器必须在它**外面**。"""
        b = self.builder
        named = [n for n in sorted(b.recipes)
                 if b.recipes[n].for_target(b.target, b.target_os).test.get("env")]
        self.assertTrue(named, "没有带 test.env 的 recipe，这条断言失去对象")
        os.environ["GTKCROSS_TEST_WRAPPER"] = "xvfb-run -a"
        cmd = self._cmd(named[0])
        self.assertTrue(cmd.startswith("xvfb-run -a env "), f"{named[0]}: {cmd}")


if __name__ == "__main__":
    unittest.main()
