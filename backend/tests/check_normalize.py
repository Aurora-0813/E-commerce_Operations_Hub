# -*- coding: utf-8 -*-
"""
验证文本归一化。

运行（在 backend/ 目录下）：
    python tests/check_normalize.py
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.dqe.normalize import normalize, text_stats   # noqa: E402

# (原始, 期望, 说明)
CASES = [
    ("好\u200b评", "好评", "零宽空格被去掉"),
    ("好\u200d评", "好评", "零宽连接符被去掉"),
    ("ＡＢＣ", "ABC", "全角字母转半角"),
    ("<b>好评</b>", "好评", "HTML 标签被剥掉"),
    # ⚠️ 这条原先期望 "太!差"（连续标点被压缩），是**错的** ✅
    # `normalize._REPEAT` 只压缩【中文/字母/数字】，**故意不压标点** ——
    # 因为连续标点/符号是 R003（纯符号评价刷屏）的检测信号，压掉就抓不到了。
    # 2026-10-31 修：代码是对的，是这条期望（和 normalize 的 docstring）写错了。
    # 如果反过来"修代码"让它压标点，R003 会失效 → 降噪率变 → 整条链的数字全变 ✅ ✗
    ("太!!!!!!!!!差", "太!!!!!!!!!差", "连续【标点】不压缩（R003 要靠它抓刷屏）"),
    # 中文 5 连要被压成 1：这才是有意压缩的那类
    ("太啊啊啊啊啊差", "太啊差", "连续【中文】5 连压成 1"),
    ("哈哈哈", "哈哈哈", "3 个重复不压缩（正常语气）"),
    ("  好评  ", "好评", "首尾空白去掉"),
]


def main() -> None:
    print("=== 归一化用例 ===")
    all_ok = True
    for raw, expect, desc in CASES:
        got = normalize(raw)
        ok = got == expect
        all_ok = all_ok and ok
        print(f"{'✅' if ok else '❌'} {desc}")
        print(f"   输入 {raw!r}")
        print(f"   输出 {got!r}  期望 {expect!r}")

    print("\n=== 文本统计 ===")
    # 用"9 个感叹号"对比"压成 1 个"的标点占比 —— 直观说明为什么不能压标点：
    # 压了以后 punct_ratio 从 0.82 掉到 0.33，R003（纯符号刷屏）就抓不到了。
    for t in ("好评", "太!!!!!!!!!差", "太!差", "Great product", "！！！"):
        print(f"{t!r:20} -> {text_stats(t)}")

    print("\n" + ("✅ 全部通过" if all_ok else "❌ 有失败用例"))
    # 返回退出码：让 run_tests.py / CI 能判（老脚本只 print，退出码永远 0）
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
