@echo off
chcp 65001 >nul
REM ============================================================
REM   一键停止（双击这个）
REM
REM   停：后端 8000 + worker + 前端 5173
REM   不停：Redis（数据）/ PostgreSQL（系统服务）
REM
REM   用法：
REM     双击             停后端/worker/前端
REM     stop.bat -All    连 Redis 容器一起停
REM     stop.bat -DryRun 只看会停什么
REM
REM   ⚠️ 改了后端代码时：**先 stop 再 start**（worker 不读 --reload ✅）
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
echo.
pause
