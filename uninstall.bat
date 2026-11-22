@echo off
REM Goharu 一键卸载/清除（Windows）
REM   uninstall.bat [/all] [/purge]
setlocal
cd /d "%~dp0"

set DEL_DATA=0
set DEL_ENV=0
for %%a in (%*) do (
  if "%%a"=="--all" set DEL_DATA=1
  if "%%a"=="/all" set DEL_DATA=1
  if "%%a"=="--purge" (set DEL_DATA=1 & set DEL_ENV=1)
  if "%%a"=="/purge" (set DEL_DATA=1 & set DEL_ENV=1)
)

REM 杀掉残留进程
tasklist /fi "imagename eq python.exe" 2>nul | find /i "python.exe" >nul && (
  wmic process where "CommandLine like '%%gateway_entry.py%%'" delete >nul 2>&1
)

if exist venv rmdir /s /q venv
if exist tui-ts\node_modules rmdir /s /q tui-ts\node_modules
if exist tui-ts\dist rmdir /s /q tui-ts\dist
del /s /q tui-ts\src\*.js tui-ts\src\*.js.map tui-ts\src\*.d.ts tui-ts\src\*.d.ts.map >nul 2>&1
echo [+] venv / node_modules / dist 已清除

if "%DEL_DATA%"=="1" (
  if exist runtime_memory rmdir /s /q runtime_memory
  if exist models rmdir /s /q models
  echo [+] runtime_memory / models 已删除（记忆数据不可恢复）
)
if "%DEL_ENV%"=="1" (
  if exist .env del /f .env
  echo [+] .env 已删除
)

echo [+] 卸载完成。仓库代码保留在当前目录。
endlocal
