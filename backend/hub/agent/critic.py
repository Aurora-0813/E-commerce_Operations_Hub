# -*- coding: utf-8 -*-
"""
Critic：第二个模型独立核对 agent 的结论（critic.py）—— 阶段 3（7.6）
===================================================================
**它和已有的 `E-VERIFY` 有什么不同**：

    `E-VERIFY`（handle.py 里）
        → 同一个模型，检查"**引用的编号存在吗**" ✅
        → 只查**形式**（编号在不在 ✅）

    Critic（本文件）
        → **第二次调用**，检查"**结论和证据是否自洽**" ✅
        → 查**语义**：证据说了什么？结论主张什么？两者接得上吗？
        → **独立成一次调用** → 不是"同一个模型在同一次生成里自我声明" ✅

实测（2026-10-31，`docs/开发指南/14_Agent规划能力实测.md` §9）给了它一个明确的靶子：
    无规则组 **22/22 全选 `listing.update_desc`** ✅
    而那 22 条的建议原文全是"更新商品描述，强调 XX" ✅
    → 其中**明显不合理**的（Critic 应该抓到）：
        · 「物流延迟」→ 改详情页描述（物流慢是履约问题，写描述没用 ✅）
        · 「客服态度差」→ 更新客服培训手册（培训手册不是商品描述 ✅）
        · 「漏发少赠品」→ 描述里说明赠品政策（漏发是履约问题 ✅）
        · 「掉色严重」→ 在详情页强调掉色（提前告诉买家会掉色，不解决问题 ✅）

所以 Critic 判三类问题：
    ① **无依据**：结论里的断言在证据里找不到支撑
    ② **证据与结论不匹配**：证据说 A，结论主张 B
    ③ **动作类型错**：用"改文案"去解"履约/质量问题"

用法：
    all/ 不自动跑（多一次 LLM 调用），由 `hub handle --critic` 显式开启 ✅
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from hub.agent.handle import HandleResult

CRITIC_SYSTEM = (
    "你是审核员。下面是一个运营助手对某个差评问题簇给出的**建议**，以及它引用的**证据**。\n"
    "你的任务：**核对这个建议站不站得住**，而不是评价它写得好不好。\n"
    "只输出一个 JSON，不要任何其它文字：\n"
    '{"verdict": "OK" 或 "WEAK" 或 "WRONG",\n'
    ' "problems": ["问题1", "问题2"],\n'
    ' "reason": "一句话说明你的判断"}\n'
    "三类问题（**任一类成立就要报**）：\n"
    "① 无依据：建议里的事实性断言（数字、比例、『多数/大部分』）在证据里找不到；\n"
    "② 不匹配：证据说的问题，和建议要做的事**不是一回事**；\n"
    "③ 动作类型错：用『改商品描述/文案』去解『履约问题（物流慢/漏发/客服态度）』"
    "或『真实质量缺陷（掉色/起球/开线）』—— 这类问题写文案解决不了，"
    "顶多是提前告知；真正的做法是回复差评 + 反馈内部 + 修履约。\n"
    "判定：\n"
    "  OK    = 三类问题都没有\n"
    "  WEAK  = 有可疑但不算硬错（比如证据偏少、表述含糊）\n"
    "  WRONG = 至少一类成立（尤其是 ③）\n"
    "**注意 ③ 的正确做法**：对于履约问题（物流慢/漏发/客服态度）和真实质量缺陷"
    "（掉色/起球/开线），**正确做法是「回复差评 + 反馈内部 + 修履约/修工艺」**；\n"
    "  如果建议的动作就是「生成回复草稿发给买家」或「反馈内部」，那**不算动作类型错** ✅；\n"
    "  只有建议的是「改商品描述/文案」时才算 ③ ✅。\n"
    "**不要因为『建议听起来合理』就给 OK** —— 你要看的是『证据支不支持它这么做』。"
)


@dataclass
class Critique:
    """一次 Critic 检查的结果。"""
    verdict: str = ""                      # OK / WEAK / WRONG
    problems: list[str] = field(default_factory=list)
    reason: str = ""
    raw: str = ""
    ok: bool = False                       # 解析成功且拿到 verdict
    error: str = ""


def critique(result: HandleResult, *, llm=None) -> Critique:
    """对一次 agent 结论做独立核对。

    :param result: `handle()` 的产出（用它的 evidence / recommendation / tool）
    :param llm:    可注入（测试用假模型）；None 则用配置里的
    """
    if llm is None:
        from hub.llm import get_text_llm
        llm = get_text_llm()

    if result.status != "DONE" or not result.recommendation:
        # 没有明确结论的（NEEDS_HUMAN / FAILED）不需要核对 ✅
        return Critique(verdict="SKIP", reason=f"agent 状态是 {result.status}",
                        ok=False)

    evidence_text = "\n".join(f"{e['ref']}：{e['text']}" for e in result.evidence
                              if e["source"] in ("query_cluster", "query_reviews",
                                                 "query_history"))

    # ⚠️ **必须给工具说明，不能只给工具名**（2026-10-31 实测踩到 ✅）
    #    第一版只写 `【建议的动作】review.draft_reply` →
    #    Critic 把"回复差评并反馈内部"（**正确做法**）判成 WRONG ✅ ✗
    #    因为 `review.draft_reply` 对模型是个不透明字符串 ✅
    #    → 加上 `spec.description` 之后，它才知道这个工具是"生成回复草稿" ✅
    tool_desc = ""
    try:
        from hub.tools.spec import get as _get_spec
        _spec = _get_spec(result.tool)
        if _spec is not None:
            tool_desc = f"（{_spec.description}）"
    except Exception:                         # noqa: BLE001
        pass

    prompt = (
        "【问题簇】" + next((e["text"] for e in result.evidence if e["ref"] == "E1"), "") + "\n\n"
        f"【建议】{result.recommendation}\n"
        f"【建议的动作】{result.tool}{tool_desc}\n\n"
        f"【证据】\n{evidence_text}\n\n"
        "请核对这个建议站不站得住。"
    )
    try:
        raw = llm.chat(prompt, system=CRITIC_SYSTEM, temperature=0.0, max_tokens=300)
    except Exception as e:                    # noqa: BLE001
        return Critique(error=f"{type(e).__name__}: {e}", raw="")

    obj = _parse(raw)
    if obj is None:
        return Critique(raw=raw, error=f"无法解析：{raw[:120]}")

    verdict = str(obj.get("verdict") or "").upper()
    if verdict not in ("OK", "WEAK", "WRONG"):
        return Critique(raw=raw, error=f"非法 verdict：{verdict!r}")

    probs = obj.get("problems") or []
    if not isinstance(probs, list):
        probs = [str(probs)]
    return Critique(verdict=verdict,
                    problems=[str(p)[:200] for p in probs[:5]],
                    reason=str(obj.get("reason") or "")[:300],
                    raw=raw, ok=True)


def _parse(text: str) -> dict | None:
    """从模型输出里抠 JSON（和 handle.py 同一套容错）。"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def verdict_blocks(verdict: str) -> bool:
    """Critic 判 WRONG 时，**要不要拦住这条结论**？

    当前策略：**不拦，只标记** ✅
    ------------
    为什么：
        Critic 也是模型 ✅ → 它会误判（把对的判成错的）✅
        如果 WRONG 就拦下 → **一个误判就能让 agent 完全失效** ✅ ✗
      正确的做法：
        标记为"审核不通过" + 把问题列出来 → **交给提交时的人看** ✅
        （`adopt` 会把 critique 的结论写进提案理由 ✅）
      **这一条是可测的**：`check_critic.py` 里有一条"WRONG 时结论**仍然存在**"✅
    """
    return False
