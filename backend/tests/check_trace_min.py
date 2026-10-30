# -*- coding: utf-8 -*-
"""
trace 的最小定位脚本（30 秒内跑完，用来找卡在哪一步）

它不走 pytest、不碰业务，只做 5 件事，每件事前都打印一行。
**只要看最后打印的是哪一行，就知道卡在哪。**

运行：
    python tests\\check_trace_min.py
"""
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def mark(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


mark("1. 开始导入 hub.core.tracing（这一步会连带的导入 SQLAlchemy）")
from hub.core.tracing import set_trace_id, span, trace_tree   # noqa: E402
mark("   ✅ 导入完成")

mark("2. 设置 trace_id（纯内存，不碰数据库）")
tid = set_trace_id()
mark(f"   ✅ trace_id = {tid}")

mark("3. 开一个空的 span（进入时不写库）")
with span("only_one"):
    mark("   ✅ 进入 span")
    time.sleep(0.05)
    mark("   ✅ span 体内执行完，即将退出（**退出时会写一次数据库**）")
mark("   ✅ 已退出并写完库")

mark("4. 读回来（SELECT trace_span）")
rows = trace_tree(tid)
mark(f"   ✅ 读回 {len(rows)} 条 span")

mark("5. 完成")
if not rows:
    print("❌ 一条都没读到——写库失败了（看上面有没有 [trace] 报错）")
else:
    print("✅ trace 这条链路是通的")
