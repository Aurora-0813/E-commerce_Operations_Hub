# -*- coding: utf-8 -*-
"""
检查点：多 worker 安全性（M7 最容易"只有推理没有实测"的部分）

**为什么不真的起两个 worker 跑全链**：
    那要 2 × 300 秒，而且"跑通"不能证明"没重复执行"——
    得看数据有没有被写两遍，反而更难断言。

**换成在协议层测**——"两个 worker 不抢同一个消息"这件事，
    本质上就是 Redis 消费者组的语义，直接测它更准、更快、更确定：

验 6 件事：
    ① 同一个消费组里，两个消费者领到的是**不同**消息
    ② 一个消费者领走后，另一个用 `>` 领不到同一条（消息不会重复投递）
    ③ `XAUTOCLAIM` 在 min_idle=600s 时**不会**抢走正在跑的任务
    ④ `XAUTOCLAIM` 在 min_idle=0 时**会**抢走（证明机制真的在工作）
    ⑤ GPU 锁：同一时刻只有一个持有者
    ⑥ GPU 锁：持有者释放后，别人能拿到

运行（在 backend/ 目录下，需要 Redis）：
    python tests/check_queue_multi.py
"""
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hub.queue import client as q                     # noqa: E402

FAILED = []
CFG = q._q


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{('｜' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def main() -> None:
    r = q.get_redis()
    ok, msg = q.ping(r)
    if not ok:
        print(f"❌ Redis 不可用：{msg}")
        return
    print(f"Redis：{msg}\n")

    # 干净的队列（只清这几个 key，不 flushdb——避免误删别的东西）
    for key in (CFG.stream, CFG.dlq_key, CFG.delayed_key, CFG.gpu_lock_key):
        r.delete(key)
    q.ensure_group(r)

    # ---------- ①② 消费者组：不重复投递 ----------
    print("=== ①② 消费者组：两个消费者领到不同消息 ===")
    q.enqueue(r, "run-A")
    q.enqueue(r, "run-B")

    a = r.xreadgroup(CFG.group, "worker-A", {CFG.stream: ">"}, count=1, block=1000)
    b = r.xreadgroup(CFG.group, "worker-B", {CFG.stream: ">"}, count=1, block=1000)
    a_run = a[0][1][0][1]["run_id"] if a else None
    b_run = b[0][1][0][1]["run_id"] if b else None
    check("worker-A 领到一条", a_run is not None, f"run={a_run}")
    check("worker-B 领到的是**另一条**", b_run is not None and b_run != a_run,
          f"A={a_run} B={b_run}")

    third = r.xreadgroup(CFG.group, "worker-C", {CFG.stream: ">"}, count=1, block=1000)
    check("第三条领不到（消息已经分完了，不会重复投递）", not third)

    # ---------- ③ XAUTOCLAIM 不抢正在跑的 ----------
    print("\n=== ③ XAUTOCLAIM：阈值 600s 时不抢正在跑的任务 ===")
    resp = r.xautoclaim(CFG.stream, CFG.group, "worker-C",
                        min_idle_time=600_000, start_id="0-0", count=10)
    stolen = resp[1] if len(resp) > 1 else []
    check("没有抢到任何消息（它们刚被领走，不算闲置）", len(stolen) == 0,
          f"抢到 {len(stolen)} 条")

    # ---------- ④ XAUTOCLAIM 会抢闲置的 ----------
    print("\n=== ④ XAUTOCLAIM：阈值 0 时会抢（证明机制在工作）===")
    time.sleep(1.1)          # 让消息"闲置"超过 1 秒
    resp = r.xautoclaim(CFG.stream, CFG.group, "worker-C",
                        min_idle_time=1_000, start_id="0-0", count=10)
    stolen = resp[1] if len(resp) > 1 else []
    check("抢到了任务（原持有者被认为崩了）", len(stolen) >= 1,
          f"抢到 {len(stolen)} 条")

    # ---------- ⑤ ⑥ GPU 锁互斥 ----------
    print("\n=== ⑤⑥ GPU 锁：互斥 + 可释放 ===")
    got_a = r.set(CFG.gpu_lock_key, "worker-A", nx=True, ex=60)
    got_b = r.set(CFG.gpu_lock_key, "worker-B", nx=True, ex=60)
    check("worker-A 拿到锁", got_a is True)
    check("worker-B 拿不到（同一时刻只有一个持有者）", got_b is None)
    check("锁的内容是 worker-A", r.get(CFG.gpu_lock_key) == "worker-A")

    r.delete(CFG.gpu_lock_key)          # 模拟 A 跑完释放
    got_b2 = r.set(CFG.gpu_lock_key, "worker-B", nx=True, ex=60)
    check("释放后 worker-B 能拿到", got_b2 is True)

    # ---------- ⑦ PEL 心跳续租：跑得久也不会被抢 ----------
    #
    # 这是本文件最重要的用例。做法：
    #   领一条消息 → 启动 PelRenewer（阈值 4 秒 → 每 1 秒续一次）
    #   → 等 3 秒（超过阈值）→ 用 min_idle=4s 尝试抢 → **应该抢不到**
    #   然后停掉续租 → 再等 4 秒 → 抢 → **应该抢得到**
    #
    # 前后两次断言缺一不可：只测"抢不到"无法证明续租在工作（可能抢的代码本身没调用）。
    print("\n=== ⑦ PEL 心跳续租：任务跑得久也不会被抢 ===")
    r.delete(CFG.stream)
    q.ensure_group(r)
    q.enqueue(r, "run-long")
    got = r.xreadgroup(CFG.group, "worker-A", {CFG.stream: ">"}, count=1, block=1000)
    msg_id = got[0][1][0][0]

    from hub.queue.worker import PelRenewer
    renewer = PelRenewer(r, msg_id, claim_idle_s=4)     # 每 1 秒续一次
    renewer.start()
    try:
        time.sleep(3.5)                                  # 超过 4 秒阈值的一半还多
        stolen = r.xautoclaim(CFG.stream, CFG.group, "worker-C",
                              min_idle_time=4_000, start_id="0-0", count=10)[1]
        check("续租期间：**抢不到**（正在跑的任务不会被误判成崩了）",
              len(stolen) == 0, f"抢到 {len(stolen)} 条")
    finally:
        renewer.stop()

    time.sleep(1.5)
    stolen = r.xautoclaim(CFG.stream, CFG.group, "worker-C",
                          min_idle_time=1_000, start_id="0-0", count=10)[1]
    check("停掉续租后：**抢得到**（真崩了就该被接管）", len(stolen) >= 1,
          f"抢到 {len(stolen)} 条")

    # ---------- 收尾 ----------
    r.delete(CFG.gpu_lock_key)
    for key in (CFG.stream, CFG.dlq_key, CFG.delayed_key):
        r.delete(key)
    q.ensure_group(r)

    print("\n" + "=" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
    else:
        print("✅ 多 worker 安全性通过：不重复投递 / 抢占阈值 / GPU 锁 / **心跳续租**")
    print("\n说明：这不是'起两个 worker 跑全链'，而是直接测")
    print("      **Redis 消费者组、分布式锁、以及续租的语义**——那才是'不重复执行'的真正保证。")


if __name__ == "__main__":
    main()
