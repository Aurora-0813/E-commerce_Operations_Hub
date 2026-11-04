<#
  一键停止 —— 项目根目录（2026-11-04）
  ====================================
  用法（在项目根目录下）：

      .\stop.ps1              # 停后端 + worker + 前端（**Redis 不停** ✅）
      .\stop.ps1 -All         # 连 Redis 容器一起停
      .\stop.ps1 -DryRun      # 只看会停什么（不动）

  也可以双击根目录的 **stop.bat**（绕过执行策略 ✅）

  ── 停什么 / 不停什么 ────────────────────────────────────
     ❌ 停：后端（uvicorn :8000）· worker · 前端（vite :5173）
     ✅ 不停：**Redis 容器**（它是数据，不是服务 ✅）
              PostgreSQL 服务（系统服务，不动 ✅）

  ── 为什么要有它 ────────────────────────────────────────
     · **改了后端代码必须重启 worker**（它不读 --reload ✅）
       → 先 `stop.bat` 再 `start.bat` ✅
     · 演示完关电脑前，一条命令收干净 ✅
#>
param(
    [switch]$All,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$ROOT    = $PSScriptRoot
$BACKEND = Join-Path $ROOT 'backend'
$env:PYTHONIOENCODING = 'utf-8'

function Line { Write-Host ('─' * 66) -ForegroundColor DarkGray }
function Title($t) { Write-Host ''; Write-Host "  $t" -ForegroundColor Cyan; Line }
function Ok($m) { Write-Host "  ✅ $m" -ForegroundColor Green }
function Bad($m) { Write-Host "  ❌ $m" -ForegroundColor Red }
function Warn($m) { Write-Host "  ⚠️  $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "     $m" -ForegroundColor DarkGray }

Write-Host ''
Write-Host '  ╔════════════════════════════════════════════════════════╗' -ForegroundColor Cyan
Write-Host '  ║   一键停止  ·  postgres / redis 不动                   ║' -ForegroundColor Cyan
Write-Host '  ╚════════════════════════════════════════════════════════╝' -ForegroundColor Cyan

# ── 收集要停的进程 ──
$targets = @()

# ① 后端（uvicorn）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*uvicorn*hub.api.main*' } |
    ForEach-Object { $targets += [pscustomobject]@{ Kind = '后端 uvicorn'; Pid = $_.ProcessId; Started = $_.CreationDate } }

# ② worker（**最容易忘的那个** ✅）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*hub.cli*worker*' } |
    ForEach-Object { $targets += [pscustomobject]@{ Kind = 'worker'; Pid = $_.ProcessId; Started = $_.CreationDate } }

# ③ 前端（vite / node）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like '*vite*' -or $_.CommandLine -like '*npm*run*dev*') } |
    ForEach-Object { $targets += [pscustomobject]@{ Kind = '前端 vite'; Pid = $_.ProcessId; Started = $_.CreationDate } }

Title '1/3  找到这些进程'

if ($targets.Count -eq 0) {
    Info '没有在跑的后端 / worker / 前端（可能已经停了）'
} else {
    Write-Host "  $('类型'.PadRight(16)) $('PID'.PadLeft(7))   启动时间"
    Write-Host '  ' + ('-' * 52)
    foreach ($t in $targets) {
        Write-Host "  $($t.Kind.PadRight(16)) $("$($t.Pid)".PadLeft(7))   $($t.Started)"
    }
}

# 端口状态
$p8000 = [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
$p5173 = [bool](Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
Write-Host ''
Info "端口：8000 $(if ($p8000) { '在监听' } else { '空闲' }) ｜ 5173 $(if ($p5173) { '在监听' } else { '空闲' })"

if ($DryRun) {
    Write-Host ''
    Info '-DryRun：到此为止，什么都没停 ✅'
    exit 0
}

# ── 真停 ──
Title '2/3  停止'
$killed = 0
foreach ($t in $targets) {
    try {
        Stop-Process -Id $t.Pid -Force -ErrorAction Stop
        Ok "已停 $($t.Kind)（PID $($t.Pid)）"
        $script:killed++
    } catch {
        Bad "停不掉 $($t.Kind)（PID $($t.Pid)）：$($_.Exception.Message)"
        Info '可能是权限不够 → 用管理员 PowerShell 再跑一次 ✅'
    }
}
if ($script:killed -eq 0 -and $targets.Count -gt 0) { Warn '一个都没停掉（检查权限 ✅）' }
Start-Sleep -Seconds 2

# ── 可选：停 Redis ──
if ($All) {
    Title '附加：停 Redis 容器'
    $out = docker stop hub-redis 2>&1
    if ($LASTEXITCODE -eq 0) { Ok 'Redis 容器已停（数据在 volume 里，下次 start 会起来 ✅）' }
    else { Warn "停不了 Redis：$($out | Select-Object -First 1)" }
} else {
    Info 'Redis 容器**没停**（要停加 `-All` ✅）'
}

# ── 收尾检查 ──
Title '3/3  收尾检查'
$left = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like '*uvicorn*hub.api.main*' -or $_.CommandLine -like '*hub.cli*worker*') } |
    ForEach-Object { $left += "python PID $($_.ProcessId)" }
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*vite*' } |
    ForEach-Object { $left += "node PID $($_.ProcessId)" }

if ($left.Count -gt 0) {
    Warn "还有残留：$($left -join ' / ')"
} else {
    Ok '后端 / worker / 前端 都停了 ✅'
}

$p8000b = [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
$p5173b = [bool](Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
if (-not $p8000b -and -not $p5173b) { Ok '8000 / 5173 都已释放 ✅' }
else { Warn "端口还占着：8000=$(if($p8000b){'占'}else{'空'}) 5173=$(if($p5173b){'占'}else{'空'})" }

Write-Host ''
Write-Host '  ── 提醒 ──' -ForegroundColor Cyan
Write-Host '     · Redis 还在跑（数据没丢）→ 下次 start.bat 直接能用 ✅'
Write-Host '     · **改了后端代码**：stop → start 就是"重启 worker" ✅'
Write-Host '     · 想连 Redis 一起停：stop.bat -All ✅'
Write-Host ''
Write-Host '  ✅ 完成' -ForegroundColor Green
Write-Host ''
