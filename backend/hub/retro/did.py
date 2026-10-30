# -*- coding: utf-8 -*-
"""
双重差分（retro/did.py）
=======================
**DID = (处理组：后 - 前) - (对照组：后 - 前)**

为什么不能只看"处理组的前后变化"：
    两个月后差评率下降了 —— 这可能是因为
        · 我们做的动作（想证明的）
        · 大促让买家更满意（别的原因）
        · 整体季节/趋势变化（别的原因）
    对照组的"前后变化"就是这些"别的原因"的度量。减掉它，剩下的才归给动作。

**符号约定**：指标是【差评率】，所以
    did < 0 → 差评率相对降得更多 → 动作**有效**（POSITIVE）
    did > 0 → 差评率相对升得更多 → 动作**有害**（NEGATIVE）
"""
import random
from dataclasses import dataclass, field

from hub.retro.metrics import DidInput, aggregate

# bootstrap 次数。1000 次在"点数估计 + 95% 分位区间"上够用了；
# 想更稳可以调到 5000（代价是慢 5 倍，而这个规模下毫秒级，不是瓶颈）。
BOOTSTRAP_N = 1000
RNG_SEED = 42          # 固定种子：同一份数据每次复盘结论一致，可复现

# 一个商品在两个窗口里各自至少要多少条评价，才有资格参与计算。
#
# 为什么必须有这道门槛：
#   实测数据里，单个商品每窗口只有 6~20 条评价（DQE 洗掉刷单后更少）。
#   3/12 这种比率的标准误是 ±12 个百分点——把它算进 DID，就是拿噪声减噪声。
#   这类商品只会放大方差、不会提供信息，所以**宁可少用几个商品**。
MIN_REVIEWS_PER_SPU = 8


@dataclass
class DidResult:
    did_pp: float | None                  # 百分点（None = 数据不足）
    ci_low: float | None
    ci_high: float | None
    treated_before: float | None
    treated_after: float | None
    control_before: float | None
    control_after: float | None
    n_treated: int
    n_control: int
    n_reviews: int                        # 参与计算的总评价数
    significant: bool
    confounds: list[str] = field(default_factory=list)   # 混杂因素（不显著之外的原因）
    # 每组四个计数，用来判断"这个比率有多可信"
    # 只给比率不给样本量，是让人无法判断真假的报告方式
    counts: dict = field(default_factory=dict)

    @property
    def ci_excludes_zero(self) -> bool:
        """置信区间是否不跨 0（= 统计上显著）。"""
        if self.ci_low is None or self.ci_high is None:
            return False
        return self.ci_low > 0 or self.ci_high < 0


def _eligible(counts: dict[str, dict]) -> dict[str, dict]:
    """筛掉"两边窗口评价都不够"的商品（它们只贡献噪声）。"""
    return {k: v for k, v in counts.items()
            if v["n_before"] >= MIN_REVIEWS_PER_SPU
            and v["n_after"] >= MIN_REVIEWS_PER_SPU}


def _delta_mean(counts: dict[str, dict]) -> float | None:
    """**逐商品差值再平均**（面板数据的固定效应估计量）。

    为什么不用"聚合比率之差"：
        聚合是把所有评价加起来算比率，相当于**按评价数加权**。
        但评价多的商品不一定更有代表性——一个 60% 差评率的商品
        和一堆 5% 的商品，加权后会把它自己的异常放大。
        逐商品先算差值，每个商品只贡献"自己的变化"，基期水平被自己抵消。
        这是面板 DID 的标准做法，也让方差小得多（不需要估计商品间的水平差异）。
    """
    deltas = []
    for c in counts.values():
        if not c["n_before"] or not c["n_after"]:
            continue
        deltas.append(c["bad_after"] / c["n_after"]
                      - c["bad_before"] / c["n_before"])
    return sum(deltas) / len(deltas) if deltas else None


def _did_of(t: dict, c: dict) -> float | None:
    """一组的 DID 点值。入参是【已经筛过】的按商品计数。"""
    dt, dc = _delta_mean(t), _delta_mean(c)
    if dt is None or dc is None:
        return None
    return (dt - dc) * 100


def compute(data: DidInput, confounds: list[str] | None = None,
            n_boot: int = BOOTSTRAP_N) -> DidResult:
    """算 DID 点估计 + 按【商品】重抽样的 bootstrap 95% 区间。

    为什么按商品重抽样、不按评价：
        同一个商品下的评价不独立（同批面料、同一批货 → 差评成堆出现）。
        按评价抽样会把 CI 算得比真实窄，导致"本来不显著"被判成显著。
        这在统计上叫"聚类相关"，处理办法就是**按聚类（商品）抽样**。
    """
    t_all, c_all = aggregate(data.treated), aggregate(data.control)
    confounds = confounds or []

    # 先筛掉样本太少的商品，再算 DID（和 bootstrap 用同一批商品，口径一致）
    t_ok = _eligible(data.treated)
    c_ok = _eligible(data.control)
    did = _did_of(t_ok, c_ok)

    t_keys = list(t_ok)
    c_keys = list(c_ok)
    n_reviews = (t_all["n_before"] + t_all["n_after"]
                 + c_all["n_before"] + c_all["n_after"])

    ci_low = ci_high = None
    if did is not None and t_keys and c_keys:
        rng = random.Random(RNG_SEED)
        samples: list[float] = []
        for _ in range(n_boot):
            # 有放回地抽商品（数量不变），再算逐商品差值平均
            tp = [rng.choice(t_keys) for _ in t_keys]
            cp = [rng.choice(c_keys) for _ in c_keys]
            v = _did_of({f"t{i}": t_ok[k] for i, k in enumerate(tp)},
                        {f"c{i}": c_ok[k] for i, k in enumerate(cp)})
            if v is not None:
                samples.append(v)
        if samples:
            samples.sort()
            ci_low = samples[int(0.025 * len(samples))]
            ci_high = samples[int(0.975 * len(samples)) - 1]

    return DidResult(
        did_pp=did, ci_low=ci_low, ci_high=ci_high,
        treated_before=t_all["rate_before"], treated_after=t_all["rate_after"],
        control_before=c_all["rate_before"], control_after=c_all["rate_after"],
        n_treated=len(t_keys), n_control=len(c_keys),
        n_reviews=n_reviews, significant=False, confounds=confounds,
        counts={"treated": t_all, "control": c_all},
    )


def judge(result: DidResult, min_control: int = 3) -> dict:
    """判结论。**混杂因素优先于统计显著性** —— 这是这一节的关键。

    为什么顺序不能反：
        撞大促那一组的 DID 会**又大又显著**（差评率降了 61%），
        如果先看显著性，它会得到一个非常自信的 POSITIVE。
        但那个改善是大促带来的，不是动作带来的。
        **统计显著不等于因果成立** —— 混杂要先排除，再看显著性。

    :return: {"conclusion": ..., "reason_code": ..., "detail": ...}
    """
    if result.did_pp is None:
        return {"conclusion": "INCONCLUSIVE", "reason_code": "窗口数据缺失",
                "detail": "处理组或对照组在某个窗口内一条评价都没有"}

    if result.n_control < min_control:
        return {"conclusion": "INCONCLUSIVE", "reason_code": "对照组不足",
                "detail": f"对照组只有 {result.n_control} 个商品（要求 ≥{min_control}）"}

    if "重叠干预" in result.confounds:
        return {"conclusion": "INCONCLUSIVE", "reason_code": "重叠干预",
                "detail": "处理组里有商品在同一窗口内被别的动作也干预过，效果无法归因"}

    if "撞大促" in result.confounds:
        return {"conclusion": "INCONCLUSIVE", "reason_code": "撞大促",
                "detail": "观察窗口和促销活动重叠，改善可能来自促销而不是本次动作"}

    if not result.ci_excludes_zero:
        return {"conclusion": "INCONCLUSIVE", "reason_code": "变化不显著",
                "detail": f"DID={result.did_pp:+.2f}pp，"
                          f"95% 区间 [{result.ci_low:+.2f}, {result.ci_high:+.2f}] 跨过 0"}

    if result.did_pp < 0:
        return {"conclusion": "POSITIVE", "reason_code": "显著的改善",
                "detail": f"差评率相对对照组多降 {abs(result.did_pp):.2f} 个百分点"}
    return {"conclusion": "NEGATIVE", "reason_code": "显著的恶化",
            "detail": f"差评率相对对照组多升 {abs(result.did_pp):.2f} 个百分点"}
