@echo off
chcp 65001 >nul
REM ============================================================
REM   一键启动（双击这个就行）—— 2026-11-03
REM
REM   为什么要有这个 .bat：
REM     Windows 默认执行策略会**禁止运行 .ps1**（未签名）✅
REM     → 这个 bat 用 -ExecutionPolicy Bypass 调它，双击即可 ✅
REM
REM   用法：
REM     双击                 启动全部
REM     start.bat -Stop      停止
REM     start.bat -Reset     启动前清零（保留账号）
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
echo.
pause
