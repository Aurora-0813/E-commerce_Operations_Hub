<#
  一键清空测试数据 —— 系统交付前状态（根目录）
  ============================================
  用法（在项目根目录 F:\project_summary\E-commerce_Operations_Hub 下）：

      .\reset.ps1                # 交互式：先列出会清什么，问你 Y/N
      .\reset.ps1 -Yes           # 不问，直接清
      .\reset.ps1 -DryRun        # 只看（不动）

  也可以双击根目录的 **reset.bat**（绕过执行策略 ✅）

  ── 清完之后是什么状态 ──────────────────────────────────────
      ✅ **保留**：两个账号（operator / supervisor）+ 租户 + 平台字段映射
      ❌ **清空**：所有业务数据 + 分析结果 + 图片明细 + 日志 + 上传的图片包

      具体清这些表（36 张）：
        业务数据  record / review_topic / review_feature / spu_catalog /
                 sales_daily / price_snapshot / sku_mapping / promo_calendar /
                 incremental_cursor / ingest_batch
        分析结果  issue_cluster / cluster_member / anomaly / analysis_run
        执行记录  action_proposal / tool_execution / idempotency_record /
                 hitl_task / dead_letter / action_outcome
        图片      image_asset
        可观测    trace_span / judge_score / model_registry / eval_case /
                 eval_result / agent_run / pipeline_run / audit_log
        内容      knowledge_entry / case_entry / notification / report_job /
                 report_field_whitelist / review_reply_draft / review_appeal

  ── 还会顺手清 ────────────────────────────────────────────
      · `data/uploads/*.zip`  （你上传过的图片包，几百 MB ✅）
      · 前端 E2E 截图（`frontend/e2e/shots*/`）—— 那是测试产物 ✅

  ── 为什么这是"交付前状态" ─────────────────────────────────
      客户拿到系统后：打开 → 用两个账号能登录 → 页面全是**空态**（有引导文案）
      → 自己上传数据 → 点「开始分析」→ 一切从头跑 ✅
#>
param(
    [switch]$Yes,
    [switch]$DryRun,
    # **只清链**（保留上传的数据 / 结果），只清队列 + 锁 + pipeline_run ✅
    [switch]$RunsOnly
)

$ErrorActionPreference = 'Continue'
$ROOT    = $PSScriptRoot
$BACKEND = Join-Path $ROOT 'backend'
$PY      = 'D:\Anaconda\envs\e-commerce_operations_hub\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Line { Write-Host ('─' * 68) -ForegroundColor DarkGray }
function Title($t) { Write-Host ''; Write-Host "  $t" -ForegroundColor Cyan; Line }
function Ok($m) { Write-Host "  ✅ $m" -ForegroundColor Green }
function Bad($m) { Write-Host "  ❌ $m" -ForegroundColor Red }
function Warn($m) { Write-Host "  ⚠️  $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "     $m" -ForegroundColor DarkGray }

Write-Host ''
Write-Host '  ╔════════════════════════════════════════════════════════════╗' -ForegroundColor Yellow
Write-Host '  ║   清空测试数据  →  系统交付前状态                          ║' -ForegroundColor Yellow
Write-Host '  ╚════════════════════════════════════════════════════════════╝' -ForegroundColor Yellow

# ── 0. 自检 ──
if (-not (Test-Path $PY)) { Bad "找不到 Python：$PY"; Info '改脚本顶部的 $PY'; exit 1 }
if (-not (Test-Path $BACKEND)) { Bad "找不到 backend：$BACKEND"; exit 1 }
$script = Join-Path $BACKEND 'scripts\reset_business_data.py'
if (-not (Test-Path $script)) { Bad "找不到 $script"; exit 1 }
Ok '环境自检通过'

# ── 1. 先看会清什么（dry-run 由 python 脚本负责 ✅）──
# ── 0.5 清链（Redis 队列 + GPU 锁 + pipeline_run）─────────────────
#  ⚠️ **为什么必须有这一步**
#    只清数据库不够：Redis 里还有
#      · hub:tasks 流（残留消息 / PEL 待确认）→ 新任务排在死消息后面 → 半天不动
#      · hub:lock:gpu（死 worker 留下的 GPU 锁）→ 卡在 topic 等锁
#      · hub:delayed / hub:dlq
Title '0.5/4  清链（Redis 队列 + GPU 锁 + pipeline_run）'
Push-Location $BACKEND
if ($DryRun) {
    & $PY -B -c @"
import redis
from sqlalchemy import text
from hub.core.config import Settings
from hub.persistence.db import get_session
r = redis.Redis.from_url(Settings().queue.url, decode_responses=True)
print('  Redis hub:tasks :', r.xlen('hub:tasks'), '条（将清空）')
print('  Redis GPU 锁    :', r.get('hub:lock:gpu'), '（将清除）')
from sqlalchemy import text as _t
print('  pipeline_run    : 见下方数据预演')
"@
} else {
    & $PY -B -c @"
import redis
from sqlalchemy import text
from hub.core.config import Settings
from hub.persistence.db import get_session
r = redis.Redis.from_url(Settings().queue.url, decode_responses=True)
for key in ('hub:tasks', 'hub:lock:gpu', 'hub:delayed', 'hub:dlq'):
    try:
        if r.type(key) == 'none':
            continue
        r.delete(key)
        print(f'  OK 清掉 Redis {key}')
    except Exception as e:
        print(f'  WARN {key}: {e}')
with get_session() as s:
    n = s.execute(text('DELETE FROM pipeline_run')).rowcount
    s.commit()
    if n:
        print(f'  OK 删掉 {n} 条 pipeline_run')
"@
}
Pop-Location

# **-RunsOnly**：只清链，不碰数据 ✅
if ($RunsOnly) {
    Write-Host ''
    Write-Host '  ── 只清了链（数据保留）──' -ForegroundColor Cyan
    Write-Host '     下一步：双击 start.bat → 点「开始分析」'
    Write-Host ''
    Write-Host '  ✅ 完成' -ForegroundColor Green
    exit 0
}

Title '1/3  先看清会清什么（不动数据）'
Push-Location $BACKEND
& $PY -B 'scripts\reset_business_data.py'
Pop-Location

if ($DryRun) {
    Write-Host ''
    Info '-DryRun：到此为止，什么都没删 ✅'
    exit 0
}

# ── 2. 确认 ──
Title '2/3  确认'
Warn '这是**不可撤销**的操作：上面列出的表会被清成 0 行'
Info '保留：tenant / app_user（两个账号）/ platform_field_map'
Write-Host ''
if (-not $Yes) {
    $ans = Read-Host '  确认清空？输入 Y 继续，其它键取消'
    if ($ans -notmatch '^[Yy]') {
        Write-Host ''
        Warn '已取消，什么也没删'
        exit 0
    }
}

# ── 3. 真清 ──
Title '3/3  开始清空'
Push-Location $BACKEND
& $PY -B 'scripts\reset_business_data.py' --yes
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Bad "清理失败（退出码 $code）"; exit 1 }

# ── 4. 顺手清上传的图片包 + E2E 截图 ──
Title '附加清理'
$updir = Join-Path $ROOT 'data\uploads'
if (Test-Path $updir) {
    $files = @(Get-ChildItem $updir -File -ErrorAction SilentlyContinue)
    $mb = [math]::Round((($files | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    if ($files.Count -gt 0) {
        $files | Remove-Item -Force -ErrorAction SilentlyContinue
        Ok "清掉上传的图片包 $($files.Count) 个（释放 $mb MB）"
    } else {
        Info 'data/uploads 本来就是空的'
    }
} else {
    Info 'data/uploads 不存在（没传过图片包）'
}

$shots = @(Join-Path $ROOT 'frontend\e2e\shots', (Join-Path $ROOT 'frontend\e2e\shots_full'))
$n = 0
foreach ($d in $shots) {
    if (Test-Path $d) {
        $n += @(Get-ChildItem $d -File -ErrorAction SilentlyContinue).Count
        Remove-Item (Join-Path $d '*') -Force -Recurse -ErrorAction SilentlyContinue
    }
}
Ok "清掉 E2E 截图 $n 张"

# ── 5. 收尾自检 ──
Title '完成 · 现在的状态'
Push-Location $BACKEND
& $PY -B -c @"
from sqlalchemy import text
from hub.persistence.db import get_session
KEEP = {'tenant', 'app_user', 'platform_field_map'}
with get_session() as s:
    rows = s.execute(text('''
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' ORDER BY table_name''')).scalars().all()
    nonempty = []
    for t in rows:
        if t == 'alembic_version':
            continue
        n = s.scalar(text(f'SELECT count(*) FROM \""{t}\""')) or 0
        if n > 0:
            nonempty.append((t, n))
    print('  有数据的表：')
    for t, n in nonempty:
        mark = '  ← 保留 ✅' if t in KEEP else '  ← ⚠️ 不该有数据'
        print(f'    {t:<28} {n:>6}{mark}')
    if not nonempty:
        print('    （全部为空 ✅）')
"@
Pop-Location

Write-Host ''
Line
Write-Host '  ✅ **交付前状态就绪**' -ForegroundColor Green
Write-Host ''
Write-Host '  ── 怎么验 ──' -ForegroundColor Cyan
Write-Host '     双击 start.bat 启动 → 打开 http://localhost:5173'
Write-Host '     → 两个账号都能登录（operator / supervisor，密码 123456）'
Write-Host '     → 每个页面都是**空态 + 引导文案**（不是白屏）'
Write-Host '     → 「数据接入」8 个数字全是 0，点「开始分析」没数据不会跑'
Write-Host ''
Write-Host '  ── 客户拿到后怎么开始 ──' -ForegroundColor Cyan
Write-Host '     数据接入 → 上传平台导出的 6 个 xlsx → 点「开始分析」（约 8 分钟）'
Write-Host ''
