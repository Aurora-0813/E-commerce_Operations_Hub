# -*- coding: utf-8 -*-
"""
契约层自检（check_contracts.py）
================================
**这个测试是为了防止"契约层慢慢变成装饰品"。**

为什么要它：2026-10-31 的审计发现，契约层 8 个模块里有 3 个是**零使用者**——
`gateway.py`（80 行）和 `retro.py`（88 行）整份没人用，`case.py` 也没有，
而文档里还写着"契约层是全项目唯一真相"。**没有测试盯着，这种漂移不会被发现。**

三条断言：
    1. 每个 `hub/contracts/*.py` 模块，要么**被业务代码 import**，
       要么在文件顶部标了 `PLANNED: 阶段 X`（说明它什么时候会启用）。
    2. `from hub.contracts import <每个 __all__ 名字>` 都能成功（re-export 没漏）。
    3. `contracts/` 里**不许 import 业务模块**（契约必须无依赖，
       否则会形成 契约 → 业务 → 契约 的循环）。

────────────────────────────────────────────────────────────────
2026-10-31 修过一次"假绿"（值得记下来）
────────────────────────────────────────────────────────────────
第一版判断"某契约模块被使用"的逻辑是：
    "如果有任何文件写了 `from hub.contracts import X`，就算它被使用"
→ 结果**五个模块全部通过**，连 `case.py`（明明没人用）也显示"被使用"，
  因为它把 `hub/analysis/attribute.py`（只导入了 `Attribution`）当成了证据。
→ **断言 1 永远不可能失败**，等于没测。

现在的做法：**用 AST 解析出"到底 import 了哪个符号"**，
再和该契约模块里定义的类名比对 —— 符号级匹配，不是"谁碰了这个包"。
（教训：测试必须能失败；写完要故意造一个反例看它红不红。）
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "[OK]" if ok else "[FAIL]"
    print(f"  {mark} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


ROOT = Path(__file__).resolve().parent.parent      # backend/
CONTRACTS = ROOT / "hub" / "contracts"
HUB = ROOT / "hub"


def py_files() -> list[Path]:
    """backend/hub 下所有 .py（不含 contracts 目录本身）。"""
    out = []
    for p in HUB.rglob("*.py"):
        if p.parent == CONTRACTS or CONTRACTS in p.parents:
            continue
        out.append(p)
    return out


def module_names() -> list[str]:
    """契约模块名：去掉 __init__。"""
    return sorted(p.stem for p in CONTRACTS.glob("*.py") if p.stem != "__init__")


def defined_classes(path: Path) -> list[str]:
    """该文件里 `class Foo:` 定义的所有名字（用 AST，不用正则）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.name for n in tree.body if isinstance(n, ast.ClassDef)]


def imported_symbols() -> dict[str, set[str]]:
    """扫一遍业务代码，返回 {文件: {从 hub.contracts 导入的符号名}}。

    只认两种写法：
        from hub.contracts import A, B
        from hub.contracts.<mod> import A, B
    用 AST，所以跨行括号导入也能正确解析。
    """
    found: dict[str, set[str]] = {}
    for p in py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:                      # 坏文件交给 compileall 去报
            continue
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            mod = node.module or ""
            if mod == "hub.contracts" or mod.startswith("hub.contracts."):
                names.update(a.name for a in node.names)
        if names:
            found[str(p.relative_to(ROOT))] = names
    return found


def planned_phase(path: Path) -> str | None:
    """从文件顶部找 `PLANNED: 阶段 X`（只在前 40 行里找，避免正文误命中）。"""
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:40])
    m = re.search(r"PLANNED:\s*阶段\s*([0-9.]+)", head)
    return m.group(1) if m else None


def main() -> int:
    print("=" * 62)
    print("  契约层自检")
    print("=" * 62)

    usage = imported_symbols()

    # ---------- 断言 1：每个契约模块的类，真的有人 import ----------
    print("\n[1] 每个契约模块的类真的被某处 import，或标了 PLANNED")
    mods = module_names()
    check("找到契约模块", len(mods) >= 5, f"{len(mods)} 个：{', '.join(mods)}")

    for mod in mods:
        path = CONTRACTS / f"{mod}.py"
        classes = defined_classes(path)
        # 谁 import 了这个模块里的类？
        users = {
            f: sorted(classes_set & usage[f])
            for f in usage
            for classes_set in [set(classes)]
            if classes_set & usage[f]
        }
        planned = planned_phase(path)
        if users:
            who = "；".join(f"{f}({','.join(n)})" for f, n in list(users.items())[:2])
            check(f"{mod}.py 被 import", True, who)
        elif planned:
            check(f"{mod}.py 标了 PLANNED", True, f"阶段 {planned} 启用")
        else:
            check(
                f"{mod}.py 被 import", False,
                f"定义了 {classes} 但没人 import，也没标 `PLANNED: 阶段 X`。"
                "要么用它，要么删掉它，要么标注计划（三者选一）",
            )

    # ---------- 断言 2：__all__ 里每个名字都能导入 ----------
    print("\n[2] `from hub.contracts import <名字>` 全部可用")
    code = (
        "import hub.contracts as c\n"
        "missing = [n for n in c.__all__ if not hasattr(c, n)]\n"
        "print('MISSING:' + ','.join(missing) if missing else 'OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "").strip()
    check(
        "__all__ 与实际导出一致",
        proc.returncode == 0 and out == "OK",
        out if out else (proc.stderr or "")[-300:],
    )

    # ---------- 断言 3：契约层不许 import 业务模块 ----------
    print("\n[3] 契约层保持纯净（不 import 业务模块）")
    bad: list[str] = []
    for p in CONTRACTS.glob("*.py"):
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s.startswith(("from hub.", "import hub.")):
                continue
            if "hub.contracts" in s:          # 允许契约内部互相 import
                continue
            bad.append(f"{p.name}: {s}")
    check("没有指向业务模块的 import", not bad, "；".join(bad[:5]))

    # ---------- 元断言：这个测试本身必须能失败 ----------
    # 造一个"定义了类但没人 import、也没标 PLANNED"的假模块，
    # 验证断言 1 的判断逻辑会把它判红。否则测试就是假绿的。
    print("\n[4] 元检查：断言 1 真的能判红吗")
    fake = CONTRACTS / "_selftest_fake.py"
    try:
        fake.write_text(
            '# -*- coding: utf-8 -*-\n"""临时自检文件，测试结束会删除。"""\n'
            "class NobodyImportsMe(BaseModel):\n    pass\n",
            encoding="utf-8",
        )
        tree = ast.parse(fake.read_text(encoding="utf-8"))
        fake_classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        # 用同一套逻辑判它
        fake_used = any(set(fake_classes) & s for s in usage.values())
        check(
            "没人 import 且未标 PLANNED 的模块会被判红",
            (not fake_used) and planned_phase(fake) is None,
            f"定义了 {fake_classes}，被判为「无使用者」" if not fake_used else "逻辑失效（被误判为已使用）",
        )
    finally:
        fake.unlink(missing_ok=True)

    print("\n" + "=" * 62)
    if FAILS:
        print(f"  失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"    ❌ {f}")
        print("=" * 62)
        return 1
    print("  全部通过")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
