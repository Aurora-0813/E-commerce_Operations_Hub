# -*- coding: utf-8 -*-
"""
扰动用例执行器（runner.py）—— `hub eval`
========================================
读 `suites/perturbation_v1.yaml`，逐条跑，比较 expect（子集匹配）。

**这个工具测的是"防线"，不是"模型"**：
    action 以 classify / attribute 开头的用例，用的是**假 LLM**（返回预置的坏输出）。
    因为要验证的是"模型输出坏东西之后系统能不能兜住"——
    用真 LLM 会让用例变成"测模型这次乖不乖"，那是运气，不是防线。

**五种 action**：
    dqe_rule             单条文本过 DQE 规则（R001~R008），纯函数，不碰库
    invoke               过一次网关（会写 tool_execution）
    invoke_twice         同一动作连调两次，验幂等
    attribute_fabricated 模型编造引用 → 必须被丢弃（宁可没归因，也不编造）
    classify_garbage     模型只会说废话 → 必须走关键词兜底，不能崩
    classify_malformed   模型返回非法行 → 同上
"""
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

SUITE_DIR = Path(__file__).resolve().parents[2] / "suites"


# ==================== 数据结构 ====================

@dataclass
class CaseResult:
    id: str
    category: str
    gate: str            # hard / soft
    title: str
    passed: bool
    expect: dict
    actual: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class EvalReport:
    suite: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def hard(self) -> list[CaseResult]:
        return [r for r in self.results if r.gate == "hard"]

    @property
    def soft(self) -> list[CaseResult]:
        return [r for r in self.results if r.gate == "soft"]

    @property
    def hard_rate(self) -> float:
        h = self.hard
        return sum(1 for r in h if r.passed) / len(h) if h else 1.0

    @property
    def soft_rate(self) -> float:
        s = self.soft
        return sum(1 for r in s if r.passed) / len(s) if s else 1.0

    @property
    def exit_code(self) -> int:
        """门禁：硬门禁必须 100%（任一失败 → 非零退出码）；软门禁 ≥ 95%。"""
        if self.hard_rate < 1.0:
            return 1
        if self.soft_rate < 0.95:
            return 2
        return 0


# ==================== 假 LLM（喂坏输出）====================

class _FakeLLM:
    name = "fake-eval-v1"

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.i = 0

    def chat(self, prompt, *, system=None, temperature=0.0, max_tokens=None) -> str:
        r = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return r


# ==================== 环境（占位符取值）====================

def _build_env() -> dict:
    """从库里取真实数据，替换 YAML 里的占位符。

    这样用例不写死 ID，跟着数据走（换一批数据照样能跑）。
    """
    from sqlalchemy import select

    from hub.persistence.db import get_session
    from hub.persistence.models import (
        AnalysisRun,
        ClusterMember,
        IdempotencyRecord,
        SpuCatalog,
    )
    from hub.persistence.repo_cluster import ClusterRepo

    # 幂等记录清掉，否则 C6 的"第一次"会变成重放
    with get_session() as s:
        s.execute(IdempotencyRecord.__table__.delete())
        s.commit()

    clusters = ClusterRepo().fetch_latest_clusters()
    env = {"CLUSTER": None, "IN_SPU": None, "OUT_SPU": None,
           "NO_SUCH": str(uuid4())}
    if clusters:
        c = clusters[0]
        env["CLUSTER"] = str(c.id)
        with get_session() as s:
            in_spu = s.scalars(select(SpuCatalog)
                               .where(SpuCatalog.canonical_spu_id.in_(c.spu_ids or []))
                               .limit(1)).first()
            out_spu = s.scalars(select(SpuCatalog)
                                .where(SpuCatalog.canonical_spu_id.notin_(c.spu_ids or []))
                                .limit(1)).first()
        if in_spu:
            env["IN_SPU"] = in_spu.canonical_spu_id
            env["IN_PLATFORM"] = in_spu.platform
        if out_spu:
            env["OUT_SPU"] = out_spu.canonical_spu_id

    # 造一条提案（gateway 要求 tool_execution.proposal_id 非空）
    from hub.persistence.models import ActionProposal
    with get_session() as s:
        run = AnalysisRun(trace_id=f"eval-{uuid4().hex[:8]}", tenant_id="default",
                          status="RUNNING")
        s.add(run)
        s.flush()
        p = ActionProposal(run_id=run.id, trace_id=run.trace_id,
                           tool_name="catalog.query_price", tool_version="1.0.0",
                           args={}, risk_level="L0", evidence_refs=[], rationale="eval",
                           dry_run=True, status="PROPOSED")
        s.add(p)
        s.flush()
        env["PROPOSAL"] = str(p.id)
        s.commit()

    env.setdefault("IN_PLATFORM", "taobao")
    return env


def _fill(value, env):
    """把 "$XXX" 换成真实值（递归处理 dict/list）。"""
    if isinstance(value, str) and value.startswith("$"):
        return env.get(value[1:])
    if isinstance(value, dict):
        return {k: _fill(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, env) for v in value]
    return value


# ==================== 五种 action ====================

def _act_dqe_rule(inp: dict, env: dict) -> dict:
    """单条文本过 DQE 规则（纯函数，不碰库）。"""
    from hub.dqe.rules import check_item, sort_hits

    hits = sort_hits(check_item(inp["content"], bool(inp.get("has_image"))))
    ids = [h.rule_id for h in hits]
    out = {f"hit_{r}": (r in ids) for r in
           ("R001", "R002", "R003", "R004", "R005", "R006", "R008")}
    out["rule_count"] = len(hits)
    # 第一优先级命中的规则能不能被"差评保护"救回来（R008 = False）
    out["downgradable"] = hits[0].downgradable if hits else True
    out["has_contact_or_ad"] = ("R004" in ids) or ("R005" in ids)
    return out


def _act_invoke(inp: dict, env: dict) -> dict:
    from hub.gateway import ToolCall, invoke
    from hub.tools.spec import CallContext

    r = invoke(
        ToolCall(tool_name=inp["tool"], args=inp.get("args") or {},
                 evidence_refs=inp.get("evidence") or {},
                 proposal_id=env["PROPOSAL"]),
        CallContext(role=inp.get("role", "manager"), trace_id="eval"),
    )
    return {"decision": r.decision.value, "deny_code": r.deny_code}


def _act_invoke_twice(inp: dict, env: dict) -> dict:
    from hub.gateway import ToolCall, invoke
    from hub.tools.spec import CallContext

    ctx = CallContext(role=inp.get("role", "manager"), trace_id="eval")
    call = ToolCall(tool_name=inp["tool"], args=inp.get("args") or {},
                    evidence_refs=inp.get("evidence") or {},
                    proposal_id=env["PROPOSAL"])
    first = invoke(call, ctx)
    second = invoke(call, ctx)
    return {
        "first": first.decision.value,
        "second": second.decision.value,
        "same_execution_id": first.execution_id == second.execution_id,
    }


def _act_attribute_fabricated(inp: dict, env: dict) -> dict:
    """模型编造引用 → 必须被丢弃，且**宁可返回 None 也不编造**。"""
    from hub.analysis.attribute import attribute_cell

    reviews = [{"review_id": "r1", "text": "洗了一次就掉色，质量太差了"},
               {"review_id": "r2", "text": "面料扎人，穿着不舒服"}]
    fake = _FakeLLM([
        '{"cause": "面料问题", "confidence": 0.9, "evidence": '
        '[{"id": "r1", "quote": "这句话在原文里根本不存在，是模型编的"}]}'
    ])
    result = attribute_cell(fake, "quality", "taobao", reviews)
    return {"ok": True,
            "fabricated_dropped": result is None}


def _act_classify_garbage(inp: dict, env: dict) -> dict:
    from hub.analysis.classify import classify_many

    items = [{"record_id": f"r{i}", "text": "洗了一次就掉色"} for i in range(3)]
    fake = _FakeLLM(["我觉得这些评价都挺差的，但我说不清是什么问题。"])
    try:
        results, stats = classify_many(fake, items, batch_size=3)
        return {"no_raise": True,
                "all_fallback": stats.fallback == len(items),
                "classified": len(results)}
    except Exception as e:                       # noqa: BLE001
        return {"no_raise": False, "error": str(e)[:80]}


def _act_classify_malformed(inp: dict, env: dict) -> dict:
    """模型返回了内容，但格式不对（没有 R 前缀）。"""
    from hub.analysis.classify import classify_many

    items = [{"record_id": f"r{i}", "text": "洗了一次就掉色"} for i in range(3)]
    fake = _FakeLLM(["1: 质量不好 | 3\n2: 尺寸不对 | 2\n3: 不知道 | 1"])
    try:
        results, stats = classify_many(fake, items, batch_size=3)
        return {"no_raise": True,
                "all_fallback": stats.fallback == len(items),
                "classified": len(results)}
    except Exception as e:                       # noqa: BLE001
        return {"no_raise": False, "error": str(e)[:80]}


ACTIONS = {
    "dqe_rule": _act_dqe_rule,
    "invoke": _act_invoke,
    "invoke_twice": _act_invoke_twice,
    "attribute_fabricated": _act_attribute_fabricated,
    "classify_garbage": _act_classify_garbage,
    "classify_malformed": _act_classify_malformed,
}


# ==================== 执行 ====================

def load_suite(name: str = "perturbation_v1") -> dict:
    path = SUITE_DIR / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"用例集不存在：{path}")
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 pyyaml 才能读 YAML 用例集：pip install pyyaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run(suite: str = "perturbation_v1") -> EvalReport:
    data = load_suite(suite)
    report = EvalReport(suite=suite)
    env = _build_env()

    for raw in data.get("cases", []):
        cid = raw["id"]
        expect = raw.get("expect") or {}
        action = raw.get("action")
        handler = ACTIONS.get(action)
        result = CaseResult(id=cid, category=raw.get("category", ""),
                            gate=raw.get("gate", "hard"),
                            title=raw.get("title", ""), passed=False,
                            expect=expect)
        if handler is None:
            result.error = f"未知 action：{action}"
            report.results.append(result)
            continue
        try:
            actual = handler(_fill(raw.get("input") or {}, env), env)
            result.actual = actual
            # 子集匹配：expect 里每个键的值都必须对上
            result.passed = all(actual.get(k) == v for k, v in expect.items())
        except Exception as e:                   # noqa: BLE001
            result.error = f"{type(e).__name__}: {e}"
        report.results.append(result)

    return report
