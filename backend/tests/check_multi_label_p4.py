# -*- coding: utf-8 -*-
"""
验证 P4 格式：一条一行，同时给出"问题列表 + 严重度"。

格式设计：
    R1: quality,size | 3
    ↑ 问题（最多 2 个）  ↑ 严重度 1~5

为什么要测：
    P2（只有问题）成功 80%，但没有 severity；
    而 M4 的优先级打分要用 cluster 的平均严重度。
    如果 P4 能同时给出两者，M3 就可以一次调用搞定。

运行（在 backend/ 目录下）：
    python tests/check_multi_label_p4.py
"""
import re
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.llm import get_text_llm   # noqa: E402

CASES = [
    ("衣服掉色严重，尺码还偏小", {"quality", "size"}),
    ("物流太慢，包装也压破了", {"logistics"}),
    ("开线了，纽扣也掉了两颗", {"quality"}),
    ("客服态度差，退款也不给退", {"service"}),
    ("发错尺码了，而且面料扎人", {"misship", "quality"}),
    ("色差太大，客服还不理人", {"desc_mismatch", "service"}),
    ("尺码偏小，面料起球", {"size", "quality"}),
    ("物流慢，客服态度也差", {"logistics", "service"}),
    ("衣服开线，掉色严重，还缩水", {"quality"}),
    ("发错货了，物流还慢", {"misship", "logistics"}),
]

PROMPT = """你是电商评价分类助手。下面有 10 条差评。

请判断每条提到了哪些问题（**最多 2 个**，只从下面选），以及严重度：
- quality        质量：开线/起球/掉色/缩水/线头/变形/面料差
- size           尺码：偏大/偏小/版型不符
- logistics      物流：慢/丢件/包装破损
- service        服务：客服态度/响应慢/退款纠纷
- desc_mismatch  描述不符：颜色/面料/厚薄和页面不一致
- misship        错发漏发

按这个格式输出，每条一行，不要其他内容：
R1: quality,size | 3
R2: logistics | 2

说明：竖线前是问题（逗号分隔，最多 2 个），竖线后是严重度（1~5 的整数）。

评价列表：
{listing}
"""


def main() -> None:
    n = len(CASES)
    listing = "\n".join(f"[R{i + 1}] {text}" for i, (text, _) in enumerate(CASES))
    llm = get_text_llm()
    print(f"模型：{llm.name}｜{n} 条｜测试 P4（问题 + 严重度）\n")

    t0 = time.time()
    raw = llm.chat(PROMPT.format(listing=listing), max_tokens=800)
    elapsed = time.time() - t0
    print(f"耗时 {elapsed:.1f}s\n原始输出：\n{raw}\n")

    # 解析
    # 注意：不能用 `([^|]+?) + 可选组` —— 非贪婪 + 可选会让它只匹配 1 个字符。
    # 这里用"贪婪 + 竖线必须有"，再对没有竖线的情况做兜底。
    VALID = {"quality", "size", "logistics", "service", "desc_mismatch", "misship"}
    parsed: dict[str, tuple[set, int | None]] = {}
    for line in raw.splitlines():
        m = re.match(r"\s*(R\d+)\s*[:：]\s*(.+)", line)
        if not m:
            continue
        rid, rest = m.group(1), m.group(2).strip()

        sev = None
        if "|" in rest:
            left, right = rest.split("|", 1)
            rest = left.strip()
            dm = re.search(r"(\d)", right)
            if dm:
                sev = int(dm.group(1))
        else:
            # 兜底：没竖线时，取末尾单个数字当严重度
            dm = re.search(r"(\d)\s*$", rest)
            if dm:
                sev = int(dm.group(1))
                rest = rest[:dm.start()].strip()

        topics = {t.strip() for t in re.split(r"[,，/、\s]+", rest) if t.strip()}
        parsed[rid] = (topics & VALID, sev)

    exact = partial = wrong = 0
    sev_ok = 0
    print(f"{'评价':<24}{'期望':<22}{'得到':<22}{'严重度':<8}{'结果'}")
    print("-" * 92)
    for i, (text, expected) in enumerate(CASES):
        rid = f"R{i + 1}"
        got, sev = parsed.get(rid, (set(), None))
        if got == expected:
            exact += 1
            mark = "✅"
        elif got & expected:
            partial += 1
            mark = "⚠️"
        else:
            wrong += 1
            mark = "❌"
        if sev is not None and 1 <= sev <= 5:
            sev_ok += 1
        print(f"{text[:22]:<24}{'/'.join(sorted(expected)):<22}"
              f"{'/'.join(sorted(got)) or '(空)':<22}{str(sev):<8}{mark}")

    print(f"\n=== 结果 ===")
    print(f"  问题完全正确 {exact}/{n}（{exact / n:.0%}）｜部分 {partial}｜全错 {wrong}")
    print(f"  严重度解析成功 {sev_ok}/{n}（{sev_ok / n:.0%}）")
    print(f"  耗时 {elapsed:.1f}s")

    print("\n=== 结论 ===")
    if exact / n >= 0.7 and sev_ok / n >= 0.8:
        print("  ✅ P4 可用（问题 + 严重度一次拿到），可以改多标签")
    elif exact / n >= 0.7:
        print("  ⚠️ 问题识别 OK，但严重度不稳 → 建议严重度用默认值 3")
    else:
        print("  ❌ P4 不行，退回 P2（只取问题，严重度默认）")


if __name__ == "__main__":
    main()
