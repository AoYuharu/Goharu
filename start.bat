@echo off
REM Goharu 启动（Windows）
cd /d "%~dp0"
set PYTHON=%CD%\venv\Scripts\python.exe
"%PYTHON%" run_tui_ts.py %*
