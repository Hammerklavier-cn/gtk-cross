"""meson/ctest 测试输出解析：超时必须被识别为失败。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import unittest

from gtkcross.builder import Builder


class ParseTestOutput(unittest.TestCase):
    def parse(self, out: str) -> dict:
        # _parse_test_output 只读取类属性上的两个正则，不需要真实 Builder
        return Builder._parse_test_output(Builder, out)

    def test_meson_timeout_is_a_failure(self):
        out = (
            " 355/416 gobject - glib:threadtests "
            "TIMEOUT         60.04s   killed by signal 15 SIGTERM\n"
            "\n"
            "Summary of Failures:\n"
            "\n"
            " 355/416 gobject - glib:threadtests "
            "TIMEOUT         60.04s   killed by signal 15 SIGTERM\n"
        )
        self.assertEqual(self.parse(out), {"ERROR": ["glib:threadtests"]})

    def test_ctest_timeout_is_a_failure(self):
        out = (
            "1/1 Test #1: slowtest .....................  "
            "***Timeout  60.00 sec\n"
        )
        self.assertEqual(self.parse(out), {"ERROR": ["slowtest"]})

    def test_regular_statuses_are_preserved(self):
        out = (
            "1/3 suite - name:ok   OK     0.01s\n"
            "2/3 suite - name:fail FAIL   0.01s\n"
            "3/3 suite - name:skip SKIP   0.01s\n"
        )
        self.assertEqual(
            self.parse(out),
            {"OK": ["name:ok"], "FAIL": ["name:fail"], "SKIP": ["name:skip"]},
        )


if __name__ == "__main__":
    unittest.main()
