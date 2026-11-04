<#
  一键启动 / 停止  （项目根目录）—— 2026-11-03
  ============================================
  用法（在项目根目录 F:\project_summary\E-commerce_Operations_Hub 下）：

      .\start.ps1                # 启动：Redis + 后端 + worker + 前端
      .\start.ps1 -Stop          # 停止：杀掉后端 / worker / 前端
      .\start.ps1 -NoBrowser     # 启动但不自动开浏览器
      .\start.ps1 -Reset         # 启动前先把库清零（= 系统初始状态，保留账号）
      .\start.ps1 -Brief         # 启动后顺便生成一次 AI 日报（要 Ollama）

  它做什么：
    ① 检查 Docker → 起 Redis（**端口从 .env 读**，现在是 6139）
    ② 起后端 uvicorn（8000）+ 等 /api/ping 真的通
    ③ 起 worker（**漏了它分析永远不动**）
    ④ 起前端 vite（5173）+ 等端口通
    ⑤ 打印一张状态表 + 演示入口 + 常见问题

  为什么要有它（踩过的坑）：
    · **worker 最容易被忘** → 分析一直"排队中" ✅
    · **Redis 端口被 Windows 封过**（6379 → 6139）→ 脚本从 .env 读，不再写死 ✅
    · **8000 会被 Windows 保留端口段占**（WinError 10013）→ 脚本先探测再报清楚 ✅
    · 打开 4 个窗口手动起 4 次 → 现在一条命令 ✅
#>
param(
    [switch]$Stop,
    [switch]$NoBrowser,
    [switch]$Reset,
    [switch]$Brief
)

$ErrorActionPreference = 'Continue'
$ROOT     = $PSScriptRoot
$BACKEND  = Join-Path $ROOT 'backend'
$FRONTEND = Join-Path $ROOT 'frontend'
$PY       = 'D:\Anaconda\envs\e-commerce_operations_hub\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Line { Write-Host ('─' * 66) -ForegroundColor DarkGray }
function Title($t) { Write-Host ''; Write-Host "  $t" -ForegroundColor Cyan; Line }
function Ok($m)   { Write-Host "  ✅ $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  ❌ $m" -ForegroundColor Red }
function Warn($m) { Write-Host "  ⚠️  $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "     $m" -ForegroundColor DarkGray }

# ══════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════
function Test-Port([int]$Port) {
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    } catch { return $false }
}

function Get-RedisPort {
    # 从 backend/.env 读 `HUB_QUEUE__URL=redis://:密码@主机:端口/库`
    $envFile = Join-Path $BACKEND '.env'
    if (-not (Test-Path $envFile)) { return 6379 }
    $line = Select-String -Path $envFile -Pattern '^HUB_QUEUE__URL=' | Select-Object -First 1
    if (-not $line) { return 6379 }
    if ($line.Line -match '127\.0\.0\.1:(\d+)') { return [int]$Matches[1] }
    return 6379
}

function Find-PythonProc([string]$Pattern) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Pattern*" }
}

function Wait-Http([string]$Url, [int]$Seconds = 40) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

# ══════════════════════════════════════════════════════════════
#  -Stop：停止
# ══════════════════════════════════════════════════════════════
if ($Stop) {
    Title '停止服务'
    $killed = 0
    foreach ($p in @('uvicorn*hub.api.main*', 'hub.cli*worker*')) {
        Find-PythonProc $p | ForEach-Object {
            Write-Host "  杀掉 $($p)  PID=$($_.ProcessId)" -ForegroundColor DarkGray
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $script:killed++
        }
    }
    # 前端 vite（node.exe 里带 vite 的）
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*vite*' } |
        ForEach-Object {
            Write-Host "  杀掉 vite  PID=$($_.ProcessId)" -ForegroundColor DarkGray
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $script:killed++
        }
    if ($script:killed -eq 0) { Warn '没找到在跑的服务（可能已经停了）' }
    else { Ok "已停 $($script:killed) 个进程" }
    Info 'Redis 容器**不停**（它是数据，不是服务 ✅）'
    exit 0
}

# ══════════════════════════════════════════════════════════════
Write-Host ''
Write-Host '  ╔════════════════════════════════════════════════════════╗' -ForegroundColor Cyan
Write-Host '  ║   电商运营决策中枢  ·  一键启动                        ║' -ForegroundColor Cyan
Write-Host '  ╚════════════════════════════════════════════════════════╝' -ForegroundColor Cyan

# ── 0. 环境自检 ──
Title '0/5  环境自检'
if (-not (Test-Path $PY)) { Bad "找不到 Python：$PY"; Info '改脚本顶部的 $PY 路径'; exit 1 }
Ok "Python 在：$PY"
if (-not (Test-Path $BACKEND)) { Bad "找不到 backend 目录：$BACKEND"; exit 1 }
if (-not (Test-Path $FRONTEND)) { Bad "找不到 frontend 目录：$FRONTEND"; exit 1 }
Ok '目录结构正常'

$redisPort = Get-RedisPort
Info "Redis 端口（从 .env 读）= $redisPort"

# ── 1. Docker + Redis ──
Title '1/5  Redis（Docker）'
$dockerOk = $false
try {
    $null = docker ps 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch { }

if (-not $dockerOk) {
    Bad 'Docker Desktop 没在运行'
    Info '请先启动 Docker Desktop（托盘图标 / 开始菜单），等它显示 Engine running'
    Info '然后重新跑这个脚本 ✅'
    Info '（Redis 是队列 + token 黑名单，没有它：登出 / 触发分析 / 通知 都会 503 ✅）'
    exit 1
}
Ok 'Docker 在运行'

if (Test-Port $redisPort) {
    Ok "Redis 已在监听 $redisPort"
} else {
    Warn "Redis 没在 $redisPort，尝试 docker start hub-redis …"
    $null = docker start hub-redis 2>&1
    Start-Sleep -Seconds 5
    if (Test-Port $redisPort) {
        Ok "Redis 起来了（$redisPort）"
    } else {
        Bad "Redis 起不来 —— 多半是 Windows 把这个端口封了"
        Info "查一下是不是在保留区间里："
        Info "  netsh int ipv4 show excludedportrange protocol=tcp"
        Info "如果真的被封：管理员窗口跑 net stop winnat → docker start hub-redis → net start winnat"
        Info "或者换端口：docker rm -f hub-redis 然后用 -p 127.0.0.1:6139:6379 重建，再改 .env ✅"
        exit 1
    }
}

# ── 2. 后端 ──
Title '2/5  后端（uvicorn :8000）'
$API = 'http://127.0.0.1:8000'
if (Test-Port 8000) {
    if (Wait-Http "$API/api/ping" 5) {
        Ok '后端已经在跑（复用）'
    } else {
        Warn '8000 被别的程序占着，但 /api/ping 不通'
        Info '可能是旧进程卡住 → 先跑 .\start.ps1 -Stop 再试'
    }
} else {
    Warn '起后端 …'
    Start-Process -FilePath $PY `
        -ArgumentList '-B', '-m', 'uvicorn', 'hub.api.main:app',
                      '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $BACKEND -WindowStyle Hidden
    if (Wait-Http "$API/api/ping" 40) {
        Ok '后端起来了'
    } else {
        Bad '后端 40 秒没起来'
        Info '常见原因：① 8000 被 Windows 保留端口段占了（WinError 10013）'
        Info '          ② 数据库连不上（检查 PostgreSQL 在跑 + backend/.env）'
        Info '手动验证：cd backend; python -m uvicorn hub.api.main:app --port 8000'
        exit 1
    }
}

# ── 3. worker ──
Title '3/5  worker（队列消费者）'
$workers = @(Find-PythonProc 'hub.cli*worker*')
if ($workers.Count -gt 0) {
    Ok "worker 已在跑（$($workers.Count) 个）"
} else {
    Warn '起 worker …（**漏了它：分析会一直"排队中" ✅）'
    # ⚠️ **必须把 stdout/stderr 重定向到文件**（2026-11-04 踩过的大坑 ✅）
    #    用 `-WindowStyle Hidden` 但不重定向 → stdout 写到隐藏控制台
    #    → 没人读 → **缓冲区满** → 链打印多的时候 **worker 进程阻塞** ✅ ✗
    #    → 表现：`heartbeat` 几分钟不动、CPU 接近 0、链卡在某阶段 ✅
    #    → dqe 实际只要 3.9 秒，却"卡了 20 分钟" ✅
    # ⚠️ **起 2 个 worker**（2026-11-04 改，很重要）
    #    为什么：**图片批次和全链会互相堵** ✅
    #      · 图片批次：新包 395 张 × 8.9 秒 ≈ **59 分钟**（本地视觉模型）
    #      · 全链：约 8 分钟
    #      → 只有 1 个 worker 时，图片批次会把全链**堵在后面 59 分钟** ✅ ✗
    #    2 个 worker 就能并行：一个吃图片批次，一个吃全链 ✅
    #    （队列本身就是多消费者设计：PEL + XAUTOCLAIM 抢消息 ✅）
    $wlog = Join-Path $BACKEND 'worker.log'
    $werr = Join-Path $BACKEND 'worker.err.log'
    Start-Process -FilePath $PY -ArgumentList '-u', '-B', '-m', 'hub.cli', 'worker' `
        -WorkingDirectory $BACKEND -WindowStyle Hidden `
        -RedirectStandardOutput $wlog -RedirectStandardError $werr
    Start-Sleep -Seconds 2
    Start-Process -FilePath $PY -ArgumentList '-u', '-B', '-m', 'hub.cli', 'worker' `
        -WorkingDirectory $BACKEND -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $BACKEND 'worker2.log') `
        -RedirectStandardError  (Join-Path $BACKEND 'worker2.err.log')
    Info "worker 日志：$wlog 与 worker2.log（已加 -u，实时可看 ✅）"
    Start-Sleep -Seconds 6
    $workers = @(Find-PythonProc 'hub.cli*worker*')
    if ($workers.Count -gt 0) { Ok 'worker 起来了' }
    else { Bad 'worker 没起来（手动跑：cd backend; python -m hub.cli worker）' }
}

# ── 4. 前端 ──
Title '4/5  前端（vite :5173）'
if (Test-Port 5173) {
    Ok '前端已经在跑（复用）'
} else {
    Warn '起前端 …（首次启动要编译，等 10~20 秒 ✅）'
    Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev' `
        -WorkingDirectory $FRONTEND -WindowStyle Hidden
    $up = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port 5173) { $up = $true; break }
    }
    if ($up) { Ok '前端起来了' } else { Bad '前端 40 秒没起来（手动：cd frontend; npm run dev）' }
}

# ── 5. 可选：清零 / 日报 ──
Title '5/5  可选动作'
if ($Reset) {
    Warn '--Reset：把库清零（保留账号 ✅）'
    Push-Location $BACKEND
    & $PY -B 'scripts\reset_business_data.py' --yes
    Pop-Location
} else {
    Info '（加 -Reset 可以在启动前把库清零 = 系统初始状态 ✅）'
}

if ($Brief) {
    Info '生成一次 AI 日报（要 Ollama 在跑 ✅）…'
    Push-Location $BACKEND
    & $PY -B -m hub.cli brief 2>&1 | Select-Object -Last 6
    Pop-Location
}

# ══════════════════════════════════════════════════════════════
#  状态汇总
# ══════════════════════════════════════════════════════════════
Title '状态汇总'
$rows = @(
    @{ N = 'Redis';    P = $redisPort; U = '' },
    @{ N = '后端';      P = 8000;       U = "$API/api/ping" },
    @{ N = '前端';      P = 5173;       U = 'http://localhost:5173' },
    @{ N = 'worker';    P = 0;          U = '' }
)
foreach ($r in $rows) {
    if ($r.N -eq 'worker') {
        $n = @(Find-PythonProc 'hub.cli*worker*').Count
        if ($n -gt 0) { Ok ("worker    " + ('在跑 ' * 1) + "（$n 个）") } else { Bad 'worker    没在跑' }
        continue
    }
    $ok = Test-Port $r.P
    $label = "{0,-8} :{1}" -f $r.N, $r.P
    if ($ok) { Ok "$label   ✅" } else { Bad "$label   没起" }
}

Write-Host ''
Write-Host '  ── 打开这里开始演示 ──' -ForegroundColor Cyan
Write-Host '     http://localhost:5173' -ForegroundColor White
Write-Host ''
Write-Host '  ── 两个账号（密码都是 123456）──' -ForegroundColor Cyan
Write-Host '     operator@hub.local      运营专员（看不到「批准」）'
Write-Host '     supervisor@hub.local    运营主管（能批准 + 另一套菜单）'
Write-Host ''
Write-Host '  ── 演示主线 ──' -ForegroundColor Cyan
Write-Host '     数据接入 → 上传 6 个文件 → 点「开始分析」（约 8 分钟）'
Write-Host '     → 工作台 → 问题全景 → 提案详情（**看依据原文高亮**）→ Agent → 批准 → 复盘'
Write-Host ''
Write-Host '  ── 常用命令 ──' -ForegroundColor Cyan
Write-Host '     .\start.ps1 -Stop        停止后端/worker/前端'
Write-Host '     .\start.ps1 -Reset       启动前把库清零（保留账号）'
Write-Host '     想演复盘误差棒：'
Write-Host '       cd backend; python -B scripts\demo_retro_backfill.py --tool listing.update_desc'
Write-Host ''

if (-not $NoBrowser) {
    try { Start-Process 'http://localhost:5173'; Ok '已打开浏览器' } catch { }
}

Write-Host '  ✅ 就绪' -ForegroundColor Green
Write-Host ''
