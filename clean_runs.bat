@echo off
chcp 65001 >nul
REM ============================================================
REM   清空所有分析链（双击）—— 2026-11-04 起**已合并进 reset.bat**
REM
REM   这个文件只是**别名**，实际调用：
REM       reset.bat -RunsOnly
REM
REM   为什么合并：
REM     原来 reset（清数据）和 clean_runs（清链）是两套 ✅
REM     → 用户经常只跑了其中一个 → 数据清了但队列还堵着 ✅ ✗
REM     → 现在**一处实现**：reset.ps1 里同时清 DB + Redis ✅
REM
REM   用法：
REM     clean_runs.bat           清链（保留数据）＝ reset.bat -RunsOnly
REM     clean_runs.bat -DryRun   只看
REM
REM   想连数据一起清 → 用 **reset.bat**
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset.ps1" -RunsOnly %*
echo.
pause
