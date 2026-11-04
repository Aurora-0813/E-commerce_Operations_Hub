@echo off
chcp 65001 >nul
REM ============================================================
REM   一键清空（双击）—— 数据 + 链（DB + Redis 队列 + GPU 锁）
REM
REM   用法：
REM     双击                    清**数据 + 链**（交付前 / 演示前最彻底）
REM     reset.bat -RunsOnly     只清链（保留上传的数据和结果）
REM     reset.bat -DryRun       只看会清什么
REM     reset.bat -Yes          不问直接清
REM
REM   清什么：
REM     ❌ 数据：record / sales_daily / 簇 / 提案 / 图片明细 / 日志 …（36 张表）
REM     ❌ 链  ：pipeline_run + Redis 队列 + GPU 锁 + 延时队列 + 死信
REM     ✅ 保留：两个账号 + 租户 + 平台字段映射
REM
REM   ⚠️ 为什么必须一起清：
REM     只清数据，Redis 里还留着死消息和 GPU 锁
REM     → 新任务排在死消息后面 → 「点了开始分析半天不动」✅ ✗
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset.ps1" %*
echo.
pause
