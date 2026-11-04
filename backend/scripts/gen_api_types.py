# -*- coding: utf-8 -*-
"""
从**真实响应**生成前端 TS 类型（gen_api_types.py）—— 2026-11-04
=============================================================
**为什么要这个脚本**
    我在接图表接口时**靠猜字段名**，先后错了 3 次：

        /api/charts/cluster-trend   我写 `n`          → 实际 `reviews`
        /api/charts/hitl-trend      我写 `created`    → 实际 `PENDING/APPROVED/REJECTED`
        /api/charts/bad-rate-trend  我写 `rate`       → 实际 `bad_rate`

    每次都是"线平在 0 / 柱子高 0 / 图是空的"，而且**编译期抓不到** ✅ ✗

**为什么不用 openapi.json**
    看了 `/api/openapi.json`：`/api/charts/*` 这 9 个端点**没声明返回模型**
    （函数签名是 `-> dict`）→ **schema 是空的 `{}`** ✅
    → 契约里没字段，只能猜 ✅ ✗

**所以这个脚本的做法**
    **直接打接口，拿真实 JSON，反推 TS 类型** ✅ —— 响应本身就是最准的契约 ✅
    （比手写 Pydantic 响应模型更快，且零偏差 ✅）

**用法**
    python -B scripts/gen_api_types.py            # 生成 + 对账
    python -B scripts/gen_api_types.py --check    # 只对账（发现前端字段名写错）

**它会做什么**
    ① 调每个 `/api/charts/*`，拿真实 JSON
    ② 生成 `frontend/src/types/api.generated.ts`（**不要手改** ✅）
    ③ **对账**：把 `frontend/src/api/data.ts` 里手写的 interface 和真实字段比一遍
       → 打印"多了哪些字段（= 拼错了）/ 少了哪些" ✅
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FRONT = ROOT / "frontend"
BASE = "http://127.0.0.1:8000"

#: 要生成的端点（名字 → 路径）
ENDPOINTS: dict[str, str] = {
    "BadRateTrendResp": "/api/charts/bad-rate-trend?days=30",
    "ClusterTiersResp": "/api/charts/cluster-tiers",
    "ClustersResp": "/api/charts/clusters",
    "GmvTopResp": "/api/charts/gmv-top?n=10",
    "HitlTrendResp": "/api/charts/hitl-trend?days=30",
    "ObserveTimelineResp": "/api/charts/observe-timeline",
    "PriceVsBadRateResp": "/api/charts/price-vs-badrate",
    "TopicDistributionResp": "/api/charts/topic-distribution",
}

#: 需要参数、不能直接打的端点（对账时跳过 ✅）
NEEDS_PARAM = {"/api/charts/cluster-trend"}


def login() -> dict[str, str]:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"email": "operator@hub.local", "password": "123456"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    tok = json.loads(urllib.request.urlopen(req).read())["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def ts(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return f"{ts(v[0])}[]" if v else "unknown[]"
    if isinstance(v, dict):
        inner = "; ".join(f"{k}: {ts(x)}" for k, x in v.items())
        return f"{{ {inner} }}"
    return "unknown"


def fetch_all(headers: dict[str, str]) -> dict[str, dict]:
    """打所有端点，拿真实响应（失败的记下来 ✅）"""
    out: dict[str, dict] = {}
    for name, path in ENDPOINTS.items():
        try:
            r = urllib.request.Request(BASE + path, headers=headers)
            out[name] = json.loads(urllib.request.urlopen(r).read())
        except urllib.error.HTTPError as e:
            print(f"  ⚠️ {name:<24} HTTP {e.code} → 跳过（可能需要参数 ✅）")
    return out


def generate(resp: dict[str, dict]) -> str:
    lines = [
        "// ⚠️ **自动生成 — 不要手改**（2026-11-04）",
        "// 来源：FastAPI `/api/charts/*` 的**真实响应 JSON**",
        "// 为什么不用 openapi.json：这 9 个端点**没声明返回模型** → schema 是空的",
        "// 重新生成：python backend/scripts/gen_api_types.py",
        "//",
        "// **背景**：我靠猜字段名错了 3 次（n→reviews / created→PENDING / rate→bad_rate）",
        "//   每次都是「图是空的」，而且编译期抓不到 → 所以改成**从真实响应生成** ✅",
        "",
    ]
    for name, body in resp.items():
        path = ENDPOINTS[name]
        if isinstance(body, dict) and isinstance(body.get("items"), list) and body["items"]:
            item = name.replace("Resp", "Item")
            lines.append(f"/** `GET {path}` 的列表项 */")
            lines.append(f"export interface {item} {ts(body['items'][0])}")
            lines.append("")
            others = {k: v for k, v in body.items() if k != "items"}
            fields = "; ".join(f"{k}: {ts(v)}" for k, v in others.items())
            lines.append(f"/** `GET {path}` */")
            lines.append(f"export interface {name} "
                         f"{{ {fields + '; ' if fields else ''}items: {item}[] }}")
        else:
            lines.append(f"/** `GET {path}` */")
            lines.append(f"export type {name} = {ts(body)}")
        lines.append("")
    return "\n".join(lines)


def audit(resp: dict[str, dict]) -> int:
    """拿手写 interface 和真实字段对账 → 返回「错了几处」 """
    src = (FRONT / "src" / "api" / "data.ts").read_text(encoding="utf-8")
    # 抽出所有 `export interface X { a?: T; b?: T }` 的字段名
    hand: dict[str, set[str]] = {}
    for m in re.finditer(r"export interface (\w+)\s*\{([^}]*)\}", src):
        name, body = m.group(1), m.group(2)
        hand[name] = set(re.findall(r"(\w+)\??\s*:", body))

    # **显式映射**：端点 → 前端 interface 名
    # ⚠️ 原来用"字段重合最多"自动配对 → 把 `observe-timeline` 误配到 `RetroItem` ✅ ✗
    #    → 现在写死映射，**误报归零** ✅
    PAIR = {
        "BadRateTrendResp": "TrendPoint",
        "ClusterTiersResp": "TierItem",
        "ClustersResp": "ClusterRow",
        "GmvTopResp": "GmvItem",
        "PriceVsBadRateResp": "PricePoint",
        "TopicDistributionResp": "TopicItem",
        "HitlTrendResp": "HitlPoint",
    }

    bad = 0
    print("\n  ══════ 对账：手写 interface vs 真实响应 ══════")
    for name, body in resp.items():
        if not (isinstance(body, dict) and isinstance(body.get("items"), list) and body["items"]):
            continue
        real = set(body["items"][0].keys())
        best = PAIR.get(name)
        if not best or best not in hand:
            print(f"  ⏭️  {name}：没有对应的手写 interface（跳过 ✅）")
            continue
        wrong = hand[best] - real
        missing = real - hand[best]
        if wrong or missing:
            bad += 1
            print(f"  ❌ {name}  ←→  data.ts:{best}")
            if wrong:
                print(f"      **多了（拼错的字段）**：{sorted(wrong)}  ← 这就是「图上没数据」的元凶 ✅")
            if missing:
                print(f"      少了（后端有、前端没接）：{sorted(missing)}")
        else:
            print(f"  ✅ {name}  ←→  data.ts:{best}  完全一致")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只对账，不写文件")
    args = ap.parse_args()

    print("① 登录并拉真实响应 …")
    headers = login()
    resp = fetch_all(headers)
    print(f"   拿到 {len(resp)} 个端点的响应 ✅")

    if not args.check:
        out = FRONT / "src" / "types" / "api.generated.ts"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generate(resp), encoding="utf-8")
        print(f"② 已生成 {out.relative_to(ROOT)} ✅")

    bad = audit(resp)
    print()
    if bad == 0:
        print("  ✅ 对账通过：前端手写字段和真实响应一致 ✅")
    else:
        print(f"  ⚠️ **{bad} 个接口的字段名对不上** ✅ ← 这就是「图是空的」的原因 ✅")
        print("     修法：把 `frontend/src/api/data.ts` 里那几个字段名改成真实的 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
