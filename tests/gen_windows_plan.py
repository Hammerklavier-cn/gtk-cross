"""Regenerate tests/windows-plan.golden.json.

复用 test_windows_plan.capture_plan —— 快照的唯一生产者与校验者必须是同一份
实现，否则两边字段一漂移就会出现"假差异"（本项目踩过：生成器少传了 target_os，
重新生成的基线里 Windows 专属选项全部消失）。

用法（在项目根）：
    PYTHONPATH=. python3 tests/gen_windows_plan.py

改 recipe / toolchain / 框架后若本测试失配：先看差异是不是"只是位置"
（命令行按 token 多重集比对、build plan 按集合比对），确认无实质差异后才更新
基线；否则修代码而不是刷基线。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_windows_plan import GOLDEN, WINDOWS_TARGETS, capture_plan


def main() -> int:
    out = {t: capture_plan(t) for t in WINDOWS_TARGETS}
    GOLDEN.write_text(
        json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    n = sum(len(v["recipes"]) for v in out.values())
    print(f"wrote {GOLDEN} ({n} recipe/target entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
