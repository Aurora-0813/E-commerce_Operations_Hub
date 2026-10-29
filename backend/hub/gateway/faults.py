# -*- coding: utf-8 -*-
"""
故障注入（faults.py）
====================
模拟工具失败，用来测"重试 / 补偿 / 死信"这三条路径。

**为什么必须有它**：
    真实工具不会按剧本失败。没有故障注入，重试和补偿就是**死代码**——
    写了但从没被执行过，等于没写。

怎么用：
    FAULTS = {
        "listing.update_desc": ["timeout", "timeout", "ok"],  # 前两次超时，第三次成功
        "review.draft_reply":  ["500", "500", "500"],        # 一直失败 → 补偿/死信
    }
    网关在调用 handler **之前**问一句 `maybe_fail(tool, attempt)`，
    返回一个异常就抛出去。

**默认只对 L2 生效**（见 `ENABLED`）：L0/L1 是自动执行的，不该让演示里的
"随机失败"打断正常流程；L3 根本不自动执行，注入也没意义。
"""
import time

from hub.tools.spec import RiskLevel, ToolSpec

# 工具名 → 每次尝试的行为序列（"ok" 表示正常）
# 空字典 = 不注入任何故障
FAULTS: dict[str, list[str]] = {}

# 哪些工具的**回查**要故意失败一次（用完即删）
# 用途：演示"写了但回查不一致 → 补偿回滚"
VERIFY_FAULTS: set[str] = set()

# 只对这些风险等级注入（L2：有补偿的那一类）
ENABLED_RISK = {RiskLevel.L2}

# 重试退避（秒）。真实生产是 [1, 4, 16]，演示压缩到 0.2/0.4，否则一次演示要等 21 秒
BACKOFF = [0.2, 0.4]
MAX_ATTEMPTS = 3


class ToolFault(Exception):
    """注入的工具故障。"""
    def __init__(self, kind: str):
        super().__init__(f"注入故障：{kind}")
        self.kind = kind


def configure(mapping: dict[str, list[str]] | None = None,
              verify_fail: set[str] | None = None) -> None:
    """设置故障剧本（测试用）。"""
    global FAULTS, VERIFY_FAULTS
    FAULTS = dict(mapping or {})
    VERIFY_FAULTS = set(verify_fail or set())


def maybe_fail(spec: ToolSpec, attempt: int) -> None:
    """第 attempt 次尝试（从 1 开始）该不该失败。该失败就抛 ToolFault。"""
    if spec.risk_level not in ENABLED_RISK:
        return
    script = FAULTS.get(spec.name)
    if not script:
        return
    idx = attempt - 1
    if idx >= len(script):
        return                          # 剧本用完 = 后面都成功
    if script[idx] != "ok":
        raise ToolFault(script[idx])


def should_fail_verify(spec: ToolSpec) -> bool:
    """这次回查要不要假装失败？（一次性：取走就没了）"""
    if spec.name in VERIFY_FAULTS:
        VERIFY_FAULTS.discard(spec.name)
        return True
    return False


def backoff_sleep(attempt: int) -> None:
    """重试前等一会儿（指数退避）。"""
    idx = min(attempt - 1, len(BACKOFF) - 1)
    if idx >= 0:
        time.sleep(BACKOFF[idx])
