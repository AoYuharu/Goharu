@echo off
REM Goharu 一键卸载（Windows）——薄封装，逻辑在 goharu_cli.py uninstall（跨平台同一份）
REM   uninstall.bat                 停服务 + 杀 gateway/TUI + 删依赖产物/数据/密钥/全局命令
REM   uninstall.bat --keep-data     保留 runtime_memory\ 与 models\
REM   uninstall.bat --stop-only     只停进程，不删任何文件
REM   uninstall.bat --dry-run       只列出将杀/将删的内容
REM   uninstall.bat --yes           跳过确认
REM 等价命令：goharu uninstall [选项]
setlocal
cd /d "%~dp0"
set "PY=python"
if exist venv\Scripts\python.exe set "PY=venv\Scripts\python.exe"
%PY% goharu_cli.py uninstall %*
exit /b %ERRORLEVEL%
