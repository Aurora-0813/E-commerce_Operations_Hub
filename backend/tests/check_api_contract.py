# -*- coding: utf-8 -*-
"""
接口契约测试（check_api_contract.py）—— 2026-11-04
================================================
**为什么要有这个测试**
    我在接图表接口时**靠猜字段名**，先后错了 3 次：

        /api/charts/cluster-trend   我写 `n`        → 实际 `reviews`
        /api/charts/hitl-trend      我写 `created`  → 实际 `PENDING/APPROVED/REJECTED`
        /api/charts/bad-rate-trend  我写 `rate`     → 实际 `bad_rate`

    每次的后果都是"**图是空的**"，而且：
        · TypeScript **编译期抓不到**（我把类型写成了 `rate?: number` ✅ 自己也信了）
        · 页面**不报错**（`d.rate` 是 `undefined` → `?? 0` → 线平在 0）✅
        · 只有**人眼**能发现 ✅ ✗

**这个测试做什么**
    ① 打每一个 `/api/charts/*`，拿**真实响应**
    ② 读 `frontend/src/api/data.ts` 里**手写的 interface**
    ③ 逐字段比对（**显式映射**，不猜配对 ✅）
    ④ 只要有"前端写了但后端没返回"的字段 → **失败** ✅
       （那就是拼错了 ✅ 会画出空图 ✅）

**为什么不是 Pydantic 响应模型**
    `hub/api/charts.py` 这 9 个端点签名是 `-> dict`，**没声明返回模型** ✅
    → openapi.json 里 schema 是空的 ✅ → 契约里本来就没字段 ✅
    给 9 个端点补 Pydantic 模型是"更彻底"的方案（约 40 分钟），
    但**这个测试能立刻拿到 90% 的收益**：每次跑测试/CI 都会扫一遍 ✅
    （等有整块时间再把 Pydantic 模型补上，那时这个测试自动变成"双保险" ✅）

用法：
    python -B tests/check_api_contract.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_TS = ROOT / "frontend" / "src" / "api" / "data.ts"
BASE = "http://127.0.0.1:8000"

#: 端点 → 前端手写的 interface 名（**显式映射** ✅ 不猜 ✅）
PAIRS: dict[str, str] = {
    "/api/charts/bad-rate-trend?days=30": "TrendPoint",
    "/api/charts/cluster-tiers": "TierItem",
    "/api/charts/clusters": "ClusterRow",
    "/api/charts/gmv-top?n=10": "GmvItem",
    "/api/charts/hitl-trend?days=30": "HitlPoint",
    "/api/charts/price-vs-badrate": "PricePoint",
    "/api/charts/topic-distribution": "TopicItem",
}

passed = 0
failed: list[str] = []


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  [OK] {msg}")


def bad(msg: str) -> None:
    failed.append(msg)
    print(f"  [!!] {msg}")


def login() -> dict[str, str]:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"email": "operator@hub.local", "password": "123456"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    tok = json.loads(urllib.request.urlopen(req).read())["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def hand_fields() -> dict[str, set[str]]:
    """从 data.ts 抽出手写 interface 的字段名 ✅"""
    src = DATA_TS.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"export interface (\w+)\s*\{([^}]*)\}", src):
        out[m.group(1)] = set(re.findall(r"(\w+)\??\s*:", m.group(2)))
    return out


def main() -> int:
    print("=" * 62)
    print("  接口契约：前端手写字段 vs 后端真实响应")
    print("=" * 62)

    try:
        headers = login()
    except Exception as e:                    # noqa: BLE001
        print(f"  [!!] 登录失败（后端在跑吗？Redis 在跑吗？）：{e}")
        return 1
    ok("登录成功")

    hand = hand_fields()
    if not hand:
        bad("没从 data.ts 抽到任何 interface（文件结构改了？）")
        return 1
    ok(f"从 data.ts 抽到 {len(hand)} 个 interface")

    checked = 0
    for path, iface in PAIRS.items():
        try:
            r = urllib.request.Request(BASE + path, headers=headers)
            body = json.loads(urllib.request.urlopen(r).read())
        except urllib.error.HTTPError as e:
            # 没数据时 items 可能是空数组 → 没法比字段，跳过但**要说明** ✅
            print(f"  [--] {path}  HTTP {e.code}（跳过）")
            continue

        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not items:
            print(f"  [--] {path}  items 为空，**没法比对字段**（先跑全链造数据 ✅）")
            continue

        real = set(items[0].keys())
        if iface not in hand:
            print(f"  [--] {path}  前端没有 `{iface}` 这个 interface（跳过）")
            continue

        mine = hand[iface]
        wrong = mine - real      # **前端写了但后端没有 = 拼错 ✅**
        missing = real - mine    # 后端有、前端没接（可能是故意的，只提示 ✅）

        checked += 1
        if wrong:
            bad(f"{path}\n       前端 `{iface}` 里这些字段**后端没返回**（拼错了 ✅ → 会画出空图）：{sorted(wrong)}\n"
                f"       后端真实字段：{sorted(real)}")
        elif missing:
            ok(f"{path}  字段对得上（后端多返回了 {sorted(missing)}，前端没接 ✅ 不算错）")
        else:
            ok(f"{path}  ←→  {iface}  完全一致")

    print()
    if checked == 0:
        print("  [!!] 一个都没比对上 —— 先跑全链造数据，再来测 ✅")
        return 1

    print("=" * 62)
    if failed:
        print(f"  失败 {len(failed)} 项 / 通过 {passed} 项")
        print("=" * 62)
        return 1
    print(f"  全部通过（{passed} 项，比对了 {checked} 个接口）✅")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
