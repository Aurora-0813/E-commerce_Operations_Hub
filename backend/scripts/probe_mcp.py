# -*- coding: utf-8 -*-
"""
MCP API 探针（probe_mcp.py）—— 阶段 3.2 的前置
==============================================
**为什么要有这个**：`mcp 1.30.0` 的 API 我不能按记忆写（1.x 有过重构），
而包在 `D:\Anaconda\...`（沙箱外），我读不到源码。

这个探针做三件事：
    ① 打印版本 + `mcp` / `mcp.server` / `mcp.server.fastmcp` 的公开名字
    ② 用 `FastMCP` 注册一个工具，然后**试着从几个可能的名字里找到"列工具/调工具"**
       （`list_tools` / `call_tool` / `_tool_manager` … 谁在就打印谁）
    ③ **跑一个真实的 stdio 无关的最小验证**：
       注册工具 → 列出工具 → 直接调用 → 看返回

跑法：python scripts/probe_mcp.py
"""
import asyncio
import sys


def show(obj, label: str) -> list[str]:
    names = [n for n in dir(obj) if not n.startswith("__")]
    print(f"  {label} 公开名字（前 40 个）：{names[:40]}")
    return names


def main() -> int:
    print("=" * 66)
    print("  MCP API 探针")
    print("=" * 66)

    try:
        import mcp
    except ImportError:
        print("[X] mcp 没装。pip install mcp")
        return 1

    print(f"\n[1] 版本：{getattr(mcp, '__version__', '(无 __version__)')}")
    show(mcp, "mcp")

    # ---------- ② 找 FastMCP ----------
    print("\n[2] 找高层 API（FastMCP）")
    FastMCP = None
    for mod_path in ("mcp.server.fastmcp", "mcp.server.fastmcp.server"):
        try:
            mod = __import__(mod_path, fromlist=["FastMCP"])
            FastMCP = getattr(mod, "FastMCP", None)
            print(f"  [OK] {mod_path}.FastMCP 存在：{FastMCP is not None}")
            if FastMCP:
                break
        except Exception as e:                # noqa: BLE001
            print(f"  [ ] {mod_path} 导入失败：{type(e).__name__}: {e}")

    if FastMCP is None:
        print("  [X] 没找到 FastMCP —— 那要用低层的 Server + list_tools/call_tool 装饰器")
        print("      把上面的名字列表贴给我，我按实际 API 写 ✅")
        return 1

    # ---------- ③ 注册 + 列出 + 调用 ----------
    m = FastMCP("probe")
    print(f"\n[3] 实例化：{type(m).__name__}")
    show(m, "FastMCP 实例")

    @m.tool()
    def add(a: int, b: int) -> int:
        """把两个数加起来（探针用）。"""
        return a + b

    @m.tool()
    def echo(text: str) -> str:
        """原样返回（探针用）。"""
        return text

    print("\n[4] 注册了 2 个工具（add / echo）")

    # 列工具 / 调工具：不同版本名字可能不同，逐个试
    async def probe() -> bool:
        # --- 列工具 ---
        lister = None
        for name in ("list_tools", "_list_tools", "get_tools"):
            fn = getattr(m, name, None)
            if fn is None:
                continue
            try:
                tools = await fn() if asyncio.iscoroutinefunction(fn) else fn()
                names = [getattr(t, "name", str(t)) for t in tools]
                print(f"  [OK] 列工具用 {name}()：{names}")
                lister = name
                break
            except Exception as e:            # noqa: BLE001
                print(f"  [ ] {name}() 失败：{type(e).__name__}: {e}")
        if lister is None:
            # 低层兜底：_tool_manager._tools
            tm = getattr(m, "_tool_manager", None)
            if tm is not None:
                print(f"  [OK] 用 _tool_manager._tools 能看到：{list(getattr(tm, '_tools', {}))}")
                lister = "_tool_manager"
            else:
                print("  [X] 列不出工具 —— 贴给我上面的名字列表")

        # --- 调工具 ---
        for name in ("call_tool", "_call_tool", "call"):
            fn = getattr(m, name, None)
            if fn is None:
                continue
            try:
                out = await fn("add", {"a": 2, "b": 3}) if asyncio.iscoroutinefunction(fn) \
                    else fn("add", {"a": 2, "b": 3})
                print(f"  [OK] 调工具用 {name}()：{out}")
                return True
            except Exception as e:            # noqa: BLE001
                print(f"  [ ] {name}() 失败：{type(e).__name__}: {e}")
        print("  [X] 调不了工具 —— 贴给我上面的名字列表")
        return False

    ok = asyncio.run(probe())

    print("\n" + "=" * 66)
    if ok:
        print("  结论：上面的「注册 + 列工具 + 调工具」写法就是可用 API —— 按它写 server")
    else:
        print("  结论：**注册可以，但列/调没找到** —— 把输出全贴给我，我按实际 API 改")
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
