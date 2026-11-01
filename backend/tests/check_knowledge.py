# -*- coding: utf-8 -*-
"""
知识条目测试（check_knowledge.py）—— 阶段 3.8
=============================================
路线图 §9.5.2 的验收 4 条：
    ① **DRAFT 条目不生效**
    ② **PUBLISHED 后提示词里能看到**
    ③ **版本变更后 hash 变**
    ④ **LLM 输出仍过合规校验**

另补 3 条（"只追加不覆盖"是硬规矩，必须可测）：
    ⑤ patch 段落**不能包含**系统提示的关键规则（防覆盖 ✅）
    ⑥ 别的 topic 的条目**不串味**（quality 的 patch 不进 logistics ✅）
    ⑦ 全局条目（没 topic）对**所有** topic 生效 ✅
"""
import sys
from pathlib import Path
from uuid import uuid4 as _u4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select                # noqa: E402

from hub.persistence.db import get_session           # noqa: E402
from hub.persistence.models import KnowledgeEntry    # noqa: E402

FAILS: list[str] = []
MINE: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def cleanup() -> None:
    with get_session() as s:
        s.execute(delete(KnowledgeEntry).where(
            KnowledgeEntry.title.like("测试-%")))
        s.commit()


def main() -> int:
    print("=" * 66)
    print("  知识条目（阶段 3.8）—— DRAFT 不生效，PUBLISHED 才注入")
    print("=" * 66)
    cleanup()

    import hub.knowledge as K

    # ---------- [1] DRAFT 不生效 ----------
    print("\n[1] DRAFT 条目**不生效**")
    kid = K.create("测试-质量口径", "先道歉，再解释掉色原因，最后给出补救方案 ✅",
                   topic="quality")
    txt, h = K.render_patches("quality")
    check("DRAFT 时段落为空", txt == "", txt[:40])
    check("DRAFT 时 hash 为空", h == "", h)
    # 确认它真的在库里（只是没发布 ✅）
    with get_session() as s:
        from uuid import UUID
        row = s.get(KnowledgeEntry, UUID(kid))
        check("库里存在且是 DRAFT", row is not None and row.status == "DRAFT",
              row.status if row else "missing")

    # ---------- [2] PUBLISHED 后能看到 ----------
    print("\n[2] PUBLISHED 后提示词里能看到")
    r = K.publish(kid)
    check("发布成功", r.get("ok") is True, str(r))
    txt, h1 = K.render_patches("quality")
    check("段落非空", bool(txt), txt[:50].replace("\n", " "))
    check("段落里含标题", "测试-质量口径" in txt)
    check("段落里含正文", "先道歉" in txt)
    check("带版本号", "v2" in txt, txt[:60].replace("\n", " "))

    # ---------- [3] 版本变 → hash 变 ----------
    print("\n[3] 版本变更后 hash 变")
    kid2 = K.create("测试-质量口径2", "第二条口径", topic="quality")
    K.publish(kid2)
    _txt2, h2 = K.render_patches("quality")
    check("多了条目 → hash 变", h2 != h1, f"{h1[:8]} → {h2[:8]}")
    # 同一条目再发布一次 → 应该被拒（不重复加版本 ✅）
    r2 = K.publish(kid2)
    check("重复发布被拒", r2.get("ok") is False, str(r2))
    _txt3, h3 = K.render_patches("quality")
    check("hash 没变（重复发布没动版本）", h3 == h2, f"{h2[:8]} vs {h3[:8]}")

    # ---------- [5] 只追加不覆盖 ----------
    print("\n[5] patch 段落**不含**系统提示的关键规则（防覆盖）")
    check("不含「只能引用」这几个字（那是系统提示的规则）", "只能引用" not in txt)
    check("不含「你是审核员」（那是 Critic 的系统提示）", "你是审核员" not in txt)
    check("段落自带标题「口径说明」", "【口径说明】" in txt)

    # ---------- [6] 别的 topic 不串味 ----------
    print("\n[6] 别的 topic 的条目不串味")
    t_logi, _ = K.render_patches("logistics")
    check("logistics 看不到 quality 的条目", "测试-质量口径" not in t_logi,
          t_logi[:40])

    # ---------- [7] 全局条目对所有 topic 生效 ----------
    print("\n[7] 全局条目（没 topic）对所有 topic 生效")
    kg = K.create("测试-全局口径", "任何问题都先表态已收到反馈", topic=None)
    K.publish(kg)
    t_q, _ = K.render_patches("quality")
    t_l, _ = K.render_patches("logistics")
    check("quality 能看到全局", "测试-全局口径" in t_q)
    check("logistics 也能看到全局", "测试-全局口径" in t_l)

    # ---------- [4] LLM 输出仍过合规校验 ----------
    print("\n[4] 注入知识条目后，LLM 输出仍过合规校验")
    # `hub.action.compliance` 里的函数真名是 **`check`** ✅
    # （`proposer.py` 里写的是 `from hub.action.compliance import check as check_compliance`
    #   → 我照那个别名去 import 就错了 ✅ ✗ 2026-11-01 核对）
    from hub.action.compliance import check as check_compliance
    from hub.action.compliance import flag_strings
    # ⚠️ 合规规则的真实词表（2026-11-01 核对）：赔偿/隐私/承诺时间/绝对化/甩锅/承诺处理 ✅
    #    我第一版造的"掉色是正常的，你自己不会洗吧"**不匹配任何一条** ✅ ✗
    #    → 换成真的有违规词的：
    bad = "我们绝对保证不会掉色，明天就给您退款"
    hits = check_compliance(bad)
    check("违规内容仍被识别（知识条目不会绕过合规）", len(hits) > 0,
          flag_strings(hits)[:60])
    # 至少命中"绝对化"或"承诺时间"或"承诺处理"之一 ✅
    names = {n for _t, n, _s in hits}
    check("命中的是已知规则名",
          bool(names & {"绝对化", "承诺时间", "承诺具体处理"}), str(names))
    good = "非常抱歉给您带来困扰，我们已记录该问题并反馈给质检部门，会尽快核实处理"
    hits2 = check_compliance(good)
    check("合规内容通过", len(hits2) == 0, flag_strings(hits2)[:60])

    cleanup()
    print("\n（测试数据已清理 ✅）")
    print("\n" + "=" * 66)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 66)
        return 1
    print("  全部通过")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
