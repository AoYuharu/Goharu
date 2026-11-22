@echo off
REM Goharu 一键安装（Windows）
REM   install.bat [/with-embedding]
setlocal enabledelayedexpansion
cd /d "%~dp0"

set WITH_EMBEDDING=0
if "%~1"=="--with-embedding" set WITH_EMBEDDING=1
if "%~1"=="/with-embedding" set WITH_EMBEDDING=1

set PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

echo [+] 检查 Python…
where python >nul 2>&1 || (echo [x] 需要 Python ^>= 3.10: https://www.python.org/downloads/ & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || (echo [x] Python 版本太旧，需要 ^>= 3.10 & exit /b 1)

echo [+] 检查 Node.js…
where node >nul 2>&1 || (echo [x] 需要 Node.js ^>= 18: https://nodejs.org & exit /b 1)
where npm >nul 2>&1 || (echo [x] 需要 npm & exit /b 1)

if not exist venv (
  echo [+] 创建 venv…
  python -m venv venv || exit /b 1
)

echo [+] 安装 Python 依赖（镜像: %PIP_MIRROR%）…
venv\Scripts\python.exe -m pip install --upgrade pip -q -i %PIP_MIRROR% || exit /b 1
venv\Scripts\python.exe -m pip install -r requirements.txt -q -i %PIP_MIRROR% || exit /b 1

if "%WITH_EMBEDDING%"=="1" (
  echo [+] 安装 embedding 依赖（torch 等，体积大）…
  venv\Scripts\python.exe -m pip install -r requirements-embedding.txt -i %PIP_MIRROR% || exit /b 1
) else (
  echo [+] 跳过 embedding 重依赖（向量检索降级为 FTS5+BM25；需要时 install.bat /with-embedding）
)

echo [+] 构建 TUI…
pushd tui-ts && call npm install --silent && call npm run build --silent && popd || exit /b 1

if not exist .env (
  copy .env.example .env >nul
  echo [!] 已生成 .env 模板——请编辑填入 ANTHROPIC_API_KEY 等密钥后再运行
)

echo.
echo [+] 安装完成。启动: start.bat   （或 venv\Scripts\python.exe run_tui_ts.py）
endlocal
